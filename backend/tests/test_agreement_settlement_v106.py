"""Structural tests for Agreement settlement v5 and migration 0086."""

from datetime import date
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Column, Date, ForeignKey, Integer, String, Table, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import CollectionAgreement, Group, Member, User
from app.services.agreements_v039 import lock_collection_agreement, touch_collection_agreement
from test_payment_reversal_migration_v104 import _legacy_0082_schema, _seed, _upgrade, _upgrade_0084
from test_payment_settlement_v105 import _upgrade_0085


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/0086_payment_settlement_agreement_state_v106.py"


def _load_migration():
    spec = spec_from_file_location("migration_0086", MIGRATION)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine():
    engine = sa.create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _engine_0085():
    engine = _engine()
    metadata = _legacy_0082_schema()
    Table(
        "collection_agreements", metadata,
        Column("id", Integer, primary_key=True),
        Column("loan_id", Integer, ForeignKey("loans.id")),
        Column("member_id", Integer, ForeignKey("members.id")),
        Column("requested_by", Integer, ForeignKey("users.id")),
        Column("status", String(20), nullable=False),
    )
    metadata.create_all(engine)
    _upgrade(engine)
    _seed(engine)
    _upgrade_0084(engine)
    _upgrade_0085(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO collection_agreements (id, loan_id, member_id, requested_by, status) VALUES (1, 1, 1, 1, 'APPROVED')"))
        connection.execute(sa.text("INSERT INTO payments (id) VALUES (3), (4), (5), (6), (7)"))
    return engine


def _v5(engine, **overrides):
    values = {
        "id": 2, "payment_id": 2, "member_id": 1, "obligation_type": "AGREEMENT_INSTALLMENT",
        "agreement_installment_id": 1, "amount_received": "12.00", "amount_applied": "12.00",
        "principal_applied": "10.00", "interest_applied": "0.00", "penalty_applied": "2.00", "excess_amount": "0.00",
        "obligation_status_before": "OPEN", "obligation_status_after": "PARTIAL",
        "confirmed_at": "2026-09-16 12:00:00", "confirmation_source": "TEST",
        "receipt_number": "PIX-V5-000000000002", "receipt_version": "v5", "receipt_snapshot_json": "{}", "receipt_hash": "v5-hash",
        "created_at": "2026-09-16 12:00:00",
        "agreement_installment_status_before": "OPEN", "agreement_installment_status_after": "PARTIAL",
        "agreement_installment_paid_at_before": None, "agreement_installment_paid_at_after": None,
        "agreement_installment_paid_amount_before": "0.00", "agreement_installment_paid_amount_after": "10.00",
        "agreement_installment_paid_penalty_amount_before": "0.00", "agreement_installment_paid_penalty_amount_after": "2.00",
        "collection_agreement_status_before": "APPROVED", "collection_agreement_status_after": "APPROVED",
        "collection_agreement_state_revision_before": 0, "collection_agreement_state_revision_after": 1,
    }
    values.update(overrides)
    columns = ", ".join(values)
    binds = ", ".join(f":{key}" for key in values)
    with engine.begin() as connection:
        connection.execute(sa.text(f"INSERT INTO payment_settlements ({columns}) VALUES ({binds})"), values)


def _upgrade_0086(engine):
    migration = _load_migration()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()


@pytest.mark.parametrize("field,value", [
    ("agreement_installment_paid_amount_before", "-0.01"),
    ("agreement_installment_paid_amount_after", "-0.01"),
    ("agreement_installment_paid_penalty_amount_before", "-0.01"),
    ("agreement_installment_paid_penalty_amount_after", "-0.01"),
    ("agreement_installment_status_before", "INVALID"),
    ("agreement_installment_status_after", "INVALID"),
    ("collection_agreement_status_before", "REQUESTED"),
    ("collection_agreement_status_after", "REJECTED"),
])
def test_0086_v5_rejects_invalid_absolute_values_and_operational_statuses(field, value):
    engine = _engine_0085()
    _upgrade_0086(engine)
    with pytest.raises(IntegrityError):
        _v5(engine, id=7, payment_id=7, receipt_number=f"invalid-{field}", **{field: value})
    engine.dispose()


def test_0086_v5_accepts_valid_payment_statuses_and_absolute_values():
    engine = _engine_0085()
    _upgrade_0086(engine)
    _v5(engine, collection_agreement_status_before="APPROVED", collection_agreement_status_after="SETTLED")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM payment_settlements WHERE receipt_version = 'v5'")).scalar_one() == 1
    engine.dispose()


def test_0086_adds_state_revision_and_nullable_agreement_evidence():
    engine = _engine_0085()
    _upgrade_0086(engine)
    agreement = inspect(engine).get_columns("collection_agreements")
    assert next(row for row in agreement if row["name"] == "state_revision")["nullable"] is False
    columns = {row["name"]: row for row in inspect(engine).get_columns("payment_settlements")}
    for name in (
        "agreement_installment_status_before", "agreement_installment_status_after",
        "agreement_installment_paid_at_before", "agreement_installment_paid_at_after",
        "agreement_installment_paid_amount_before", "agreement_installment_paid_amount_after",
        "agreement_installment_paid_penalty_amount_before", "agreement_installment_paid_penalty_amount_after",
        "collection_agreement_status_before", "collection_agreement_status_after",
        "collection_agreement_state_revision_before", "collection_agreement_state_revision_after",
    ):
        assert columns[name]["nullable"] is True
    _v5(engine)
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT state_revision FROM collection_agreements WHERE id=1")).scalar_one() == 0
    engine.dispose()


def test_0086_constraints_preserve_legacy_and_reject_invalid_v5():
    engine = _engine_0085()
    _upgrade_0086(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO payment_settlements (id,payment_id,member_id,obligation_type,contribution_id,amount_received,amount_applied,principal_applied,interest_applied,penalty_applied,excess_amount,obligation_status_before,obligation_status_after,confirmed_at,confirmation_source,receipt_number,receipt_version,receipt_snapshot_json,receipt_hash,created_at) VALUES (3,3,1,'CONTRIBUTION',1,1,1,1,0,0,0,'OPEN','PAID','2026-09-16','TEST','legacy-v1','v1','{}','legacy-hash','2026-09-16')"))
    with pytest.raises(IntegrityError):
        _v5(engine, id=4, payment_id=4, receipt_number="bad-missing", agreement_installment_status_before=None)
    with pytest.raises(IntegrityError):
        _v5(engine, id=5, payment_id=5, receipt_number="bad-revision", collection_agreement_state_revision_after=2)
    with pytest.raises(IntegrityError):
        _v5(engine, id=6, payment_id=6, receipt_number="bad-loan", loan_status_before="ACTIVE")
    engine.dispose()


def test_0086_downgrade_blocks_evidence_and_clean_downgrade_is_structural():
    engine = _engine_0085()
    migration = _load_migration()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    _upgrade_0086(engine)
    _v5(engine)
    with pytest.raises(RuntimeError, match="Agreement v5 evidence"):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
    assert "agreement_installment_status_before" in {row["name"] for row in inspect(engine).get_columns("payment_settlements")}
    engine.dispose()


def test_0086_clean_downgrade_removes_only_new_structure():
    engine = _engine_0085()
    migration = _load_migration()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.downgrade()
    assert "state_revision" not in {row["name"] for row in inspect(engine).get_columns("collection_agreements")}
    assert "agreement_installment_status_before" not in {row["name"] for row in inspect(engine).get_columns("payment_settlements")}
    checks = {row["name"] for row in inspect(engine).get_check_constraints("payment_settlements")}
    assert "ck_payment_settlements_receipt_version" in checks
    engine.dispose()


def test_0086_downgrade_blocks_nonzero_agreement_revision_without_discarding_schema():
    engine = _engine_0085()
    migration = _load_migration()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        connection.execute(sa.text("UPDATE collection_agreements SET state_revision = 1 WHERE id = 1"))
    with pytest.raises(RuntimeError, match="state revision"):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
    assert "state_revision" in {row["name"] for row in inspect(engine).get_columns("collection_agreements")}
    engine.dispose()


def _structure(engine, table):
    inspector = inspect(engine)
    return {
        "columns": [(row["name"], str(row["type"]), row["nullable"], row["default"]) for row in inspector.get_columns(table)],
        "pk": inspector.get_pk_constraint(table),
        "fks": sorted((tuple(row["constrained_columns"]), row["referred_table"], tuple(row["referred_columns"])) for row in inspector.get_foreign_keys(table)),
        "unique": sorted((row["name"], tuple(row["column_names"])) for row in inspector.get_unique_constraints(table)),
        "indexes": sorted((row["name"], tuple(row["column_names"]), bool(row["unique"])) for row in inspector.get_indexes(table)),
        "checks": sorted((row["name"], " ".join((row.get("sqltext") or "").split())) for row in inspector.get_check_constraints(table)),
    }


def test_0086_clean_downgrade_restores_0085_structure_for_both_tables():
    engine = _engine_0085()
    before = {table: _structure(engine, table) for table in ("payment_settlements", "collection_agreements")}
    migration = _load_migration()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.downgrade()
    after = {table: _structure(engine, table) for table in before}
    assert after == before
    engine.dispose()


def test_agreement_lock_helper_and_touch_are_logical_on_sqlite_without_commit():
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        group = Group(name="G")
        user = User(name="U", email="u-v106@example.com", cpf="cpf-v106", password_hash="x")
        db.add_all([group, user])
        db.flush()
        member = Member(user_id=user.id, group_id=group.id)
        db.add(member)
        db.flush()
        agreement = CollectionAgreement(loan_id=1, member_id=member.id, requested_by=user.id, status="APPROVED", installments=1, total_amount=Decimal("1.00"), snapshot="{}")
        db.add(agreement)
        db.flush()
        locked = lock_collection_agreement(db, agreement.id)
        assert locked.state_revision == 0
        touch_collection_agreement(locked)
        assert locked.state_revision == 1
        assert db.query(CollectionAgreement).filter_by(id=agreement.id).one().state_revision == 1
        assert len(db.new) == 0
