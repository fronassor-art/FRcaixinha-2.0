"""Structural contract for AgreementInstallment payment settlements."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import importlib.util

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, Text, create_engine, event, inspect
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import (
    AgreementInstallment,
    CollectionAgreement,
    Contribution,
    Group,
    Loan,
    LoanInstallment,
    Member,
    Payment,
    PaymentSettlement,
    User,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "0082_payment_settlement_agreement_v104.py"


def _sqlite_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _session():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _fixture(db):
    group = Group(name="Agreement structural", max_installments=6)
    user = User(name="Agreement member", email="agreement-structure@example.test", cpf="agreement-structure", password_hash="x")
    db.add_all([group, user])
    db.flush()
    member = Member(user_id=user.id, group_id=group.id)
    db.add(member)
    db.flush()
    loan = Loan(member_id=member.id, principal=Decimal("100.00"), monthly_rate=Decimal("0.10"), installments=1, status="ACTIVE")
    db.add(loan)
    db.flush()
    agreement = CollectionAgreement(
        loan_id=loan.id,
        member_id=member.id,
        requested_by=user.id,
        status="ACTIVE",
        installments=1,
        total_amount=Decimal("110.00"),
        snapshot="{}",
    )
    db.add(agreement)
    db.flush()
    installment = AgreementInstallment(
        agreement_id=agreement.id,
        number=1,
        due_date=date(2026, 1, 10),
        principal=Decimal("100.00"),
        penalty_amount=Decimal("10.00"),
        amount=Decimal("110.00"),
        status="OPEN",
    )
    db.add(installment)
    contribution = Contribution(
        member_id=member.id,
        competence=date(2026, 1, 1),
        amount=Decimal("50.00"),
        status="PENDING",
    )
    db.add(contribution)
    loan_installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date(2026, 1, 10),
        principal=Decimal("100.00"),
        interest=Decimal("0.00"),
        amount=Decimal("100.00"),
        penalty_amount=Decimal("0.00"),
        status="OPEN",
    )
    db.add(loan_installment)
    db.flush()
    return member, contribution, loan_installment, installment


def _settlement(db, member, payment_id, *, obligation_type, contribution_id=None, loan_installment_id=None, agreement_installment_id=None, before="OPEN", after="OPEN", interest="0.00", received="10.00", applied="10.00", principal="10.00", penalty="0.00", excess="0.00"):
    row = PaymentSettlement(
        payment_id=payment_id,
        member_id=member.id,
        obligation_type=obligation_type,
        contribution_id=contribution_id,
        loan_installment_id=loan_installment_id,
        agreement_installment_id=agreement_installment_id,
        amount_received=Decimal(received),
        amount_applied=Decimal(applied),
        principal_applied=Decimal(principal),
        interest_applied=Decimal(interest),
        penalty_applied=Decimal(penalty),
        excess_amount=Decimal(excess),
        obligation_status_before=before,
        obligation_status_after=after,
        confirmed_at=datetime.now(timezone.utc),
        confirmation_source="TEST",
        receipt_number=f"receipt-{payment_id}",
        receipt_version="v1",
        receipt_snapshot_json="{}",
        receipt_hash=f"{payment_id:064d}",
    )
    db.add(row)
    return row


def _payment(db, suffix):
    payment = Payment(
        provider="test",
        provider_payment_id=f"provider-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        amount=Decimal("10.00"),
        status="approved",
    )
    db.add(payment)
    db.flush()
    return payment


def test_existing_contribution_and_loan_settlements_remain_valid():
    db, _ = _session()
    member, contribution, loan_installment, _ = _fixture(db)
    contribution_payment = _payment(db, "contribution")
    loan_payment = _payment(db, "loan")
    _settlement(db, member, contribution_payment.id, obligation_type="CONTRIBUTION", contribution_id=contribution.id)
    _settlement(db, member, loan_payment.id, obligation_type="LOAN_INSTALLMENT", loan_installment_id=loan_installment.id)
    db.commit()


def test_valid_agreement_settlement_and_fk_are_accepted():
    db, engine = _session()
    member, _, _, installment = _fixture(db)
    payment = _payment(db, "agreement")
    settlement = _settlement(db, member, payment.id, obligation_type="AGREEMENT_INSTALLMENT", agreement_installment_id=installment.id, before="OPEN", after="PARTIAL", received="15.00", applied="15.00", principal="5.00", penalty="10.00")
    db.commit()
    assert settlement.agreement_installment_id == installment.id
    assert "agreement_installment_id" in {column["name"] for column in inspect(engine).get_columns("payment_settlements")}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"obligation_type": "AGREEMENT_INSTALLMENT"},
        {"obligation_type": "AGREEMENT_INSTALLMENT", "contribution_id": "contribution"},
        {"obligation_type": "AGREEMENT_INSTALLMENT", "loan_installment_id": "loan"},
        {"obligation_type": "CONTRIBUTION", "agreement_installment_id": "agreement"},
        {"obligation_type": "LOAN_INSTALLMENT", "agreement_installment_id": "agreement"},
    ],
)
def test_obligation_exclusivity_rejects_missing_or_mixed_references(kwargs):
    db, _ = _session()
    member, contribution, loan_installment, agreement_installment = _fixture(db)
    refs = {"contribution": contribution.id, "loan": loan_installment.id, "agreement": agreement_installment.id}
    payment = _payment(db, "invalid-" + str(len(db.new)))
    resolved = {
        key: refs[value] if key != "obligation_type" and isinstance(value, str) else value
        for key, value in kwargs.items()
    }
    _settlement(db, member, payment.id, **resolved)
    with pytest.raises(IntegrityError):
        db.commit()


def test_agreement_fk_rejects_nonexistent_installment():
    db, _ = _session()
    member, _, _, _ = _fixture(db)
    payment = _payment(db, "invalid-fk")
    _settlement(db, member, payment.id, obligation_type="AGREEMENT_INSTALLMENT", agreement_installment_id=999)
    with pytest.raises(IntegrityError):
        db.commit()


def test_open_statuses_and_zero_interest_are_valid_for_agreement():
    db, _ = _session()
    member, _, _, installment = _fixture(db)
    payment = _payment(db, "open")
    _settlement(db, member, payment.id, obligation_type="AGREEMENT_INSTALLMENT", agreement_installment_id=installment.id, before="OPEN", after="OPEN")
    db.commit()


def test_agreement_interest_must_be_zero():
    db, _ = _session()
    member, _, _, installment = _fixture(db)
    payment = _payment(db, "interest")
    _settlement(db, member, payment.id, obligation_type="AGREEMENT_INSTALLMENT", agreement_installment_id=installment.id, interest="1.00", received="11.00", applied="11.00", principal="10.00", penalty="0.00")
    with pytest.raises(IntegrityError):
        db.commit()


def test_math_constraints_and_payment_id_uniqueness_remain_enforced():
    db, _ = _session()
    member, contribution, _, _ = _fixture(db)
    payment = _payment(db, "math")
    _settlement(db, member, payment.id, obligation_type="CONTRIBUTION", contribution_id=contribution.id, received="12.00", applied="10.00", principal="10.00", excess="2.00")
    db.commit()
    duplicate = _settlement(db, member, payment.id, obligation_type="CONTRIBUTION", contribution_id=contribution.id, received="10.00", applied="10.00", principal="10.00", excess="0.00")
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(PaymentSettlement).count() == 1


def test_old_settlements_do_not_receive_agreement_reference():
    db, _ = _session()
    member, contribution, _, _ = _fixture(db)
    payment = _payment(db, "old")
    settlement = _settlement(db, member, payment.id, obligation_type="CONTRIBUTION", contribution_id=contribution.id)
    db.commit()
    assert settlement.agreement_installment_id is None


def test_migration_revision_and_single_head():
    migration = MIGRATION_PATH.read_text()
    assert 'down_revision = "0081_master_integrity_v104"' in migration
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    assert tuple(ScriptDirectory.from_config(config).get_heads()) == ("0089_collection_agreement_subjects",)




def _old_settlement_schema():
    metadata = MetaData()
    for name in ("payments", "members", "contributions", "loan_installments", "webhook_events", "agreement_installments"):
        Table(name, metadata, Column("id", Integer, primary_key=True))
    Table(
        "payment_settlements", metadata,
        Column("id", Integer, primary_key=True), Column("payment_id", Integer, nullable=False, unique=True),
        Column("member_id", Integer, nullable=False), Column("obligation_type", String(30), nullable=False),
        Column("contribution_id", Integer), Column("loan_installment_id", Integer),
        Column("amount_received", Numeric(14, 2), nullable=False), Column("amount_applied", Numeric(14, 2), nullable=False),
        Column("principal_applied", Numeric(14, 2), nullable=False), Column("interest_applied", Numeric(14, 2), nullable=False),
        Column("penalty_applied", Numeric(14, 2), nullable=False), Column("excess_amount", Numeric(14, 2), nullable=False),
        Column("obligation_status_before", String(20), nullable=False), Column("obligation_status_after", String(20), nullable=False),
        Column("confirmed_at", DateTime), Column("confirmation_source", String(30), nullable=False),
        Column("webhook_event_id", Integer), Column("receipt_number", String(80), nullable=False, unique=True),
        Column("receipt_version", String(20), nullable=False), Column("receipt_snapshot_json", Text, nullable=False),
        Column("receipt_hash", String(64), nullable=False, unique=True), Column("created_at", DateTime, nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]), sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.ForeignKeyConstraint(["contribution_id"], ["contributions.id"]), sa.ForeignKeyConstraint(["loan_installment_id"], ["loan_installments.id"]),
        sa.ForeignKeyConstraint(["webhook_event_id"], ["webhook_events.id"]),
        sa.CheckConstraint("(obligation_type = 'CONTRIBUTION' AND contribution_id IS NOT NULL AND loan_installment_id IS NULL) OR (obligation_type = 'LOAN_INSTALLMENT' AND loan_installment_id IS NOT NULL AND contribution_id IS NULL)", name="ck_payment_settlements_single_obligation"),
        sa.CheckConstraint("obligation_status_before IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_before"),
        sa.CheckConstraint("obligation_status_after IN ('PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_after"),
    )
    return metadata


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0082", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(engine, operation):
    migration = _load_migration()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            operation(migration)


def test_migration_upgrade_adds_agreement_structure_without_backfill():
    engine = create_engine("sqlite:///:memory:")
    _old_settlement_schema().create_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO payments (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO members (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO contributions (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, contribution_id, amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, obligation_status_before, obligation_status_after, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) VALUES (1, 1, 1, 'CONTRIBUTION', 1, 10, 10, 10, 0, 0, 0, 'PENDING', 'PAID', 'TEST', 'r1', 'v1', '{}', 'h1', CURRENT_TIMESTAMP)"))
    _run_migration(engine, lambda migration: migration.upgrade())
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT agreement_installment_id FROM payment_settlements WHERE id = 1")).scalar_one() is None
        assert "ix_payment_settlements_agreement_installment_id" in {index["name"] for index in inspect(connection).get_indexes("payment_settlements")}


def test_migration_downgrade_without_agreement_rows_restores_old_shape():
    engine = create_engine("sqlite:///:memory:")
    _old_settlement_schema().create_all(engine)
    _run_migration(engine, lambda migration: migration.upgrade())
    _run_migration(engine, lambda migration: migration.downgrade())
    with engine.connect() as connection:
        assert "agreement_installment_id" not in {column["name"] for column in inspect(connection).get_columns("payment_settlements")}
        assert "ix_payment_settlements_agreement_installment_id" not in {index["name"] for index in inspect(connection).get_indexes("payment_settlements")}


def test_migration_downgrade_aborts_when_agreement_rows_exist():
    engine = create_engine("sqlite:///:memory:")
    _old_settlement_schema().create_all(engine)
    _run_migration(engine, lambda migration: migration.upgrade())
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO payments (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO members (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO agreement_installments (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, agreement_installment_id, amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, obligation_status_before, obligation_status_after, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) VALUES (1, 1, 1, 'AGREEMENT_INSTALLMENT', 1, 10, 10, 10, 0, 0, 0, 'OPEN', 'OPEN', 'TEST', 'r1', 'v1', '{}', 'h1', CURRENT_TIMESTAMP)"))
    with pytest.raises(RuntimeError, match="AGREEMENT_INSTALLMENT"):
        _run_migration(engine, lambda migration: migration.downgrade())
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM payment_settlements")).scalar_one() == 1



def _insert_settlement_row(connection, *, row_id, payment_id, contribution_id, receipt_number, receipt_hash):
    connection.execute(sa.text(
        "INSERT INTO payment_settlements "
        "(id, payment_id, member_id, obligation_type, contribution_id, "
        "amount_received, amount_applied, principal_applied, interest_applied, "
        "penalty_applied, excess_amount, obligation_status_before, "
        "obligation_status_after, confirmation_source, receipt_number, "
        "receipt_version, receipt_snapshot_json, receipt_hash, created_at) "
        "VALUES (:id, :payment_id, 1, 'CONTRIBUTION', :contribution_id, "
        "10, 10, 10, 0, 0, 0, 'PENDING', 'PAID', 'TEST', "
        ":receipt_number, 'v1', '{}', :receipt_hash, CURRENT_TIMESTAMP)"
    ), {
        "id": row_id,
        "payment_id": payment_id,
        "contribution_id": contribution_id,
        "receipt_number": receipt_number,
        "receipt_hash": receipt_hash,
    })


def test_upgrade_preserves_unique_constraints_after_sqlite_batch_recreation():
    engine = _sqlite_engine()
    _old_settlement_schema().create_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO payments (id) VALUES (1), (2), (3), (4), (5)"))
        connection.execute(sa.text("INSERT INTO members (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO contributions (id) VALUES (1), (2), (3), (4), (5)"))
        _insert_settlement_row(connection, row_id=1, payment_id=1, contribution_id=1, receipt_number="old-receipt", receipt_hash="old-hash")
    _run_migration(engine, lambda migration: migration.upgrade())
    with engine.begin() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM payment_settlements")).scalar_one() == 1
        assert connection.execute(sa.text("SELECT agreement_installment_id FROM payment_settlements WHERE id = 1")).scalar_one() is None
        assert "ix_payment_settlements_agreement_installment_id" in {index["name"] for index in inspect(connection).get_indexes("payment_settlements")}
        assert "agreement_installments" in {fk["referred_table"] for fk in inspect(connection).get_foreign_keys("payment_settlements")}

    with engine.begin() as connection:
        _insert_settlement_row(connection, row_id=2, payment_id=2, contribution_id=2, receipt_number="receipt-2", receipt_hash="hash-2")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_settlement_row(connection, row_id=3, payment_id=2, contribution_id=3, receipt_number="receipt-3", receipt_hash="hash-3")

    with engine.begin() as connection:
        _insert_settlement_row(connection, row_id=3, payment_id=3, contribution_id=3, receipt_number="receipt-3", receipt_hash="hash-3")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_settlement_row(connection, row_id=4, payment_id=4, contribution_id=4, receipt_number="receipt-2", receipt_hash="hash-4")

    with engine.begin() as connection:
        _insert_settlement_row(connection, row_id=4, payment_id=4, contribution_id=4, receipt_number="receipt-4", receipt_hash="hash-4")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_settlement_row(connection, row_id=5, payment_id=5, contribution_id=5, receipt_number="receipt-5", receipt_hash="hash-4")
