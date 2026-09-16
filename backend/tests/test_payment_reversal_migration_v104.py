"""Structural contract for PaymentReversal migration 0083."""

from datetime import datetime, timezone
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    event,
    inspect,
)
from sqlalchemy.exc import IntegrityError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "0083_payment_reversal_v104.py"
MIGRATION_0084_PATH = BACKEND_ROOT / "alembic" / "versions" / "0084_payment_settlement_loan_paid_at_v104.py"


def _engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _legacy_0082_schema():
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    Table("members", metadata, Column("id", Integer, primary_key=True))
    Table("payments", metadata, Column("id", Integer, primary_key=True))
    Table("contributions", metadata, Column("id", Integer, primary_key=True))
    Table("loans", metadata, Column("id", Integer, primary_key=True))
    Table("loan_installments", metadata, Column("id", Integer, primary_key=True), Column("loan_id", Integer, ForeignKey("loans.id")))
    Table("agreement_installments", metadata, Column("id", Integer, primary_key=True))
    Table("webhook_events", metadata, Column("id", Integer, primary_key=True))
    Table(
        "ledger_entries",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("account", String(80), nullable=False),
        Column("direction", String(10), nullable=False),
        Column("amount", Numeric(14, 2), nullable=False),
        Column("reference_type", String(50), nullable=False),
        Column("reference_id", String(80), nullable=False),
        Column("reversal_of_id", Integer, ForeignKey("ledger_entries.id")),
        Column("previous_hash", String(64)),
        Column("entry_hash", String(64), unique=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "member_financial_accounts",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("member_id", Integer, ForeignKey("members.id")),
    )
    Table(
        "member_financial_entries",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("account_id", Integer, ForeignKey("member_financial_accounts.id"), nullable=False),
        Column("entry_type", String(50), nullable=False),
        Column("direction", String(20), nullable=False),
        Column("amount", Numeric(14, 2), nullable=False),
        Column("reference_type", String(50)),
        Column("reference_id", String(100)),
        Column("description", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "payment_settlements",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("payment_id", Integer, ForeignKey("payments.id"), nullable=False, unique=True),
        Column("member_id", Integer, ForeignKey("members.id"), nullable=False),
        Column("obligation_type", String(30), nullable=False),
        Column("contribution_id", Integer, ForeignKey("contributions.id")),
        Column("loan_installment_id", Integer, ForeignKey("loan_installments.id")),
        Column("agreement_installment_id", Integer, ForeignKey("agreement_installments.id")),
        Column("amount_received", Numeric(14, 2), nullable=False),
        Column("amount_applied", Numeric(14, 2), nullable=False),
        Column("principal_applied", Numeric(14, 2), nullable=False),
        Column("interest_applied", Numeric(14, 2), nullable=False),
        Column("penalty_applied", Numeric(14, 2), nullable=False),
        Column("excess_amount", Numeric(14, 2), nullable=False),
        Column("obligation_status_before", String(20), nullable=False),
        Column("obligation_status_after", String(20), nullable=False),
        Column("confirmed_at", DateTime(timezone=True), nullable=False),
        Column("confirmation_source", String(30), nullable=False),
        Column("webhook_event_id", Integer, ForeignKey("webhook_events.id")),
        Column("receipt_number", String(80), nullable=False, unique=True),
        Column("receipt_version", String(20), nullable=False),
        Column("receipt_snapshot_json", Text, nullable=False),
        Column("receipt_hash", String(64), nullable=False, unique=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0", name="ck_payment_settlements_nonnegative_amounts"),
        CheckConstraint("amount_received = amount_applied + excess_amount", name="ck_payment_settlements_received_allocation"),
        CheckConstraint("amount_applied = principal_applied + interest_applied + penalty_applied", name="ck_payment_settlements_applied_components"),
        CheckConstraint("obligation_type = 'CONTRIBUTION' AND contribution_id IS NOT NULL AND loan_installment_id IS NULL AND agreement_installment_id IS NULL OR obligation_type = 'LOAN_INSTALLMENT' AND contribution_id IS NULL AND loan_installment_id IS NOT NULL AND agreement_installment_id IS NULL OR obligation_type = 'AGREEMENT_INSTALLMENT' AND contribution_id IS NULL AND loan_installment_id IS NULL AND agreement_installment_id IS NOT NULL", name="ck_payment_settlements_single_obligation"),
        CheckConstraint("obligation_status_before IN ('OPEN', 'PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_before"),
        CheckConstraint("obligation_status_after IN ('OPEN', 'PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_after"),
        CheckConstraint("obligation_type != 'AGREEMENT_INSTALLMENT' OR interest_applied = 0", name="ck_payment_settlements_agreement_no_interest"),
        CheckConstraint("receipt_version = 'v1'", name="ck_payment_settlements_receipt_version"),
    )
    return metadata


def _load_migration():
    spec = spec_from_file_location("migration_0083", MIGRATION_PATH)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_migration_0084():
    spec = spec_from_file_location("migration_0084", MIGRATION_0084_PATH)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(engine):
    migration = _load_migration()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()


def _downgrade(engine):
    migration = _load_migration()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()


def _upgrade_0084(engine):
    migration = _load_migration_0084()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()


def _downgrade_0084(engine):
    migration = _load_migration_0084()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()


def _db():
    engine = _engine()
    _legacy_0082_schema().create_all(engine)
    _upgrade(engine)
    return engine


def _seed(engine, *, include_loan=True):
    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO members (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO payments (id) VALUES (1), (2)"))
        connection.execute(sa.text("INSERT INTO contributions (id) VALUES (1)"))
        if include_loan:
            connection.execute(sa.text("INSERT INTO loans (id, state_revision) VALUES (1, 0)"))
            connection.execute(sa.text("INSERT INTO loan_installments (id, loan_id) VALUES (1, 1)"))
        connection.execute(sa.text("INSERT INTO agreement_installments (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO member_financial_accounts (id, member_id) VALUES (1, 1)"))
        connection.execute(sa.text("INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, contribution_id, amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, obligation_status_before, obligation_status_after, confirmed_at, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) VALUES (1, 1, 1, 'CONTRIBUTION', 1, 10, 10, 10, 0, 0, 0, 'PENDING', 'PAID', :now, 'TEST', 'settlement-1', 'v1', :snapshot, 'settlement-hash-1', :now)"), {"now": now, "snapshot": '{\"legacy\":true}'})


def _reversal_values(**overrides):
    values = {
        "id": 1,
        "payment_id": 1,
        "settlement_id": 1,
        "admin_id": 1,
        "reason": "Motivo estrutural válido",
        "reversed_at": "2026-09-16 12:00:00+00:00",
        "reversal_competence": "2026-09-16",
        "original_competence": "2026-09-01",
        "original_due_date": None,
        "original_date_kind": "CONTRIBUTION_COMPETENCE",
        "amount_received": "10.00",
        "amount_applied": "10.00",
        "principal_applied": "10.00",
        "interest_applied": "0.00",
        "penalty_applied": "0.00",
        "excess_amount": "0.00",
        "receipt_number": "reversal-receipt-1",
        "receipt_version": "v1",
        "receipt_snapshot_json": "{}",
        "receipt_hash": "reversal-hash-1",
        "created_at": "2026-09-16 12:00:00+00:00",
    }
    values.update(overrides)
    return values


def _insert_reversal(engine, **overrides):
    values = _reversal_values(**overrides)
    columns = ", ".join(values)
    placeholders = ", ".join(f":{key}" for key in values)
    with engine.begin() as connection:
        connection.execute(sa.text(f"INSERT INTO payment_reversals ({columns}) VALUES ({placeholders})"), values)


def _columns(engine, table_name):
    return {column["name"]: column for column in inspect(engine).get_columns(table_name)}


def _foreign_keys(engine, table_name):
    return {
        column: (foreign_key["referred_table"], foreign_key["referred_columns"][0])
        for foreign_key in inspect(engine).get_foreign_keys(table_name)
        for column in foreign_key["constrained_columns"]
    }


def test_upgrade_creates_0083_schema_and_preserves_head_contract():
    engine = _db()
    inspector = inspect(engine)
    assert {"payment_reversals", "payment_reversal_components"}.issubset(set(inspector.get_table_names()))
    assert "payment_reversal_id" in {column["name"] for column in inspector.get_columns("member_financial_entries")}
    assert "state_revision" in {column["name"] for column in inspector.get_columns("loans")}
    settlement_columns = {column["name"] for column in inspector.get_columns("payment_settlements")}
    assert {"loan_status_before", "loan_status_after", "loan_state_revision_before", "loan_state_revision_after"}.issubset(settlement_columns)
    assert "payment_id" in {column["name"] for column in inspector.get_columns("payment_reversals")}
    assert any(fk["referred_table"] == "payments" for fk in inspector.get_foreign_keys("payment_reversals"))
    assert any(fk["referred_table"] == "payment_settlements" for fk in inspector.get_foreign_keys("payment_reversals"))
    assert any(fk["referred_table"] == "ledger_entries" for fk in inspector.get_foreign_keys("payment_reversal_components"))


def test_0083_critical_columns_types_nullability_and_foreign_keys():
    engine = _db()
    reversal_columns = _columns(engine, "payment_reversals")
    expected_reversal = {
        "id": (Integer, False),
        "payment_id": (Integer, False),
        "settlement_id": (Integer, False),
        "admin_id": (Integer, False),
        "reason": (Text, False),
        "reversed_at": (DateTime, False),
        "reversal_competence": (Date, False),
        "original_competence": (Date, True),
        "original_due_date": (Date, True),
        "original_date_kind": (String, False),
        "amount_received": (Numeric, False),
        "amount_applied": (Numeric, False),
        "principal_applied": (Numeric, False),
        "interest_applied": (Numeric, False),
        "penalty_applied": (Numeric, False),
        "excess_amount": (Numeric, False),
        "receipt_number": (String, False),
        "receipt_version": (String, False),
        "receipt_snapshot_json": (Text, False),
        "receipt_hash": (String, False),
        "created_at": (DateTime, False),
    }
    assert set(reversal_columns) >= set(expected_reversal)
    for name, (type_, nullable) in expected_reversal.items():
        assert isinstance(reversal_columns[name]["type"], type_)
        assert reversal_columns[name]["nullable"] is nullable
    for name in ("amount_received", "amount_applied", "principal_applied", "interest_applied", "penalty_applied", "excess_amount"):
        assert reversal_columns[name]["type"].precision == 14
        assert reversal_columns[name]["type"].scale == 2
    assert reversal_columns["original_date_kind"]["type"].length == 40
    assert reversal_columns["receipt_number"]["type"].length == 80
    assert reversal_columns["receipt_version"]["type"].length == 20
    assert reversal_columns["receipt_hash"]["type"].length == 64
    assert {
        "payment_id": ("payments", "id"),
        "settlement_id": ("payment_settlements", "id"),
        "admin_id": ("users", "id"),
    }.items() <= _foreign_keys(engine, "payment_reversals").items()

    component_columns = _columns(engine, "payment_reversal_components")
    expected_components = {
        "id": (Integer, False),
        "payment_reversal_id": (Integer, False),
        "original_ledger_entry_id": (Integer, False),
        "compensating_ledger_entry_id": (Integer, False),
        "created_at": (DateTime, False),
    }
    for name, (type_, nullable) in expected_components.items():
        assert isinstance(component_columns[name]["type"], type_)
        assert component_columns[name]["nullable"] is nullable
    assert {
        "payment_reversal_id": ("payment_reversals", "id"),
        "original_ledger_entry_id": ("ledger_entries", "id"),
        "compensating_ledger_entry_id": ("ledger_entries", "id"),
    }.items() <= _foreign_keys(engine, "payment_reversal_components").items()

    mfe_columns = _columns(engine, "member_financial_entries")
    assert mfe_columns["payment_reversal_id"]["nullable"] is True
    assert _foreign_keys(engine, "member_financial_entries")["payment_reversal_id"] == ("payment_reversals", "id")

    loan_columns = _columns(engine, "loans")
    assert isinstance(loan_columns["state_revision"]["type"], Integer)
    assert loan_columns["state_revision"]["nullable"] is False

    settlement_columns = _columns(engine, "payment_settlements")
    for name in ("loan_status_before", "loan_status_after", "loan_state_revision_before", "loan_state_revision_after"):
        assert settlement_columns[name]["nullable"] is True
    assert isinstance(settlement_columns["loan_status_before"]["type"], String)
    assert settlement_columns["loan_status_before"]["type"].length == 30
    assert isinstance(settlement_columns["loan_state_revision_before"]["type"], Integer)

    component_checks = {check["name"]: check["sqltext"] for check in inspect(engine).get_check_constraints("payment_reversal_components")}
    assert "ck_payment_reversal_components_distinct_ledgers" in component_checks


def test_upgrade_preserves_historical_settlement_receipt_and_null_evidence():
    engine = _engine()
    schema = _legacy_0082_schema()
    schema.create_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO payments (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO members (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO contributions (id) VALUES (1)"))
        connection.execute(sa.text("INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, contribution_id, amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, obligation_status_before, obligation_status_after, confirmed_at, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) VALUES (1, 1, 1, 'CONTRIBUTION', 1, 10, 10, 10, 0, 0, 0, 'PENDING', 'PAID', CURRENT_TIMESTAMP, 'TEST', 'old-receipt', 'v1', :snapshot, 'old-hash', CURRENT_TIMESTAMP)"), {"snapshot": '{"old":true}'})
    _upgrade(engine)
    with engine.connect() as connection:
        row = connection.execute(sa.text("SELECT receipt_version, receipt_snapshot_json, receipt_hash, loan_status_before, loan_state_revision_after FROM payment_settlements WHERE id = 1")).one()
        assert tuple(row) == ("v1", '{"old":true}', "old-hash", None, None)


@pytest.mark.parametrize("field,value", [
    ("reason", "x"),
    ("amount_received", "-1.00"),
    ("amount_applied", "-1.00"),
    ("principal_applied", "-1.00"),
    ("interest_applied", "-1.00"),
    ("penalty_applied", "-1.00"),
    ("excess_amount", "-1.00"),
])
def test_payment_reversal_checks_reject_invalid_reason_or_amount(field, value):
    engine = _db()
    _seed(engine)
    with pytest.raises(IntegrityError):
        _insert_reversal(engine, **{field: value})


@pytest.mark.parametrize("values", [
    {"amount_received": "11.00", "amount_applied": "10.00"},
    {"amount_applied": "9.00", "principal_applied": "10.00"},
    {"original_date_kind": "UNAVAILABLE", "original_competence": None, "original_due_date": None},
    {"original_date_kind": "CONTRIBUTION_COMPETENCE", "original_competence": None},
    {"original_date_kind": "LOAN_INSTALLMENT_DUE_DATE", "original_due_date": None},
    {"original_date_kind": "AGREEMENT_INSTALLMENT_DUE_DATE", "original_competence": "2026-09-01", "original_due_date": "2026-09-10"},
    {"receipt_version": "v2"},
])
def test_payment_reversal_checks_reject_invalid_financial_date_or_version_shape(values):
    engine = _db()
    _seed(engine)
    with pytest.raises(IntegrityError):
        _insert_reversal(engine, **values)


def test_payment_reversal_accepts_each_supported_original_date_kind():
    for values in (
        {"original_date_kind": "CONTRIBUTION_COMPETENCE", "original_competence": "2026-09-01", "original_due_date": None},
        {"original_date_kind": "LOAN_INSTALLMENT_DUE_DATE", "original_competence": None, "original_due_date": "2026-09-10"},
        {"original_date_kind": "AGREEMENT_INSTALLMENT_DUE_DATE", "original_competence": None, "original_due_date": "2026-09-10"},
    ):
        engine = _db()
        _seed(engine)
        _insert_reversal(engine, **values)


def test_payment_reversal_unique_payment_settlement_receipt_number_and_hash():
    engine = _db()
    _seed(engine)
    _insert_reversal(engine)
    for index, field in enumerate(("payment_id", "settlement_id", "receipt_number", "receipt_hash"), start=2):
        with pytest.raises(IntegrityError):
            values = {"id": index, "payment_id": index, "settlement_id": index, "receipt_number": f"other-receipt-{index}", "receipt_hash": f"other-hash-{index}"}
            values[field] = 1
            _insert_reversal(engine, **values)


def test_components_accept_valid_pair_and_reject_duplicate_original_or_compensation():
    engine = _db()
    _seed(engine)
    _insert_reversal(engine)
    now = "2026-09-16 12:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO ledger_entries (id, account, direction, amount, reference_type, reference_id, created_at) VALUES (1, 'CAIXINHA', 'CREDIT', 10, 'CONTRIBUTION_PAYMENT', '1', :now), (2, 'CAIXINHA', 'DEBIT', 10, 'REVERSAL', '1', :now)"), {"now": now})
        connection.execute(sa.text("INSERT INTO payment_reversal_components (id, payment_reversal_id, original_ledger_entry_id, compensating_ledger_entry_id, created_at) VALUES (1, 1, 1, 2, :now)"), {"now": now})
        connection.execute(sa.text("INSERT INTO ledger_entries (id, account, direction, amount, reference_type, reference_id, created_at) VALUES (3, 'CAIXINHA', 'DEBIT', 10, 'REVERSAL', '1', :now)"), {"now": now})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO payment_reversal_components (id, payment_reversal_id, original_ledger_entry_id, compensating_ledger_entry_id, created_at) VALUES (2, 1, 3, 3, :now)"), {"now": now})
    for original, compensating in ((1, 3), (3, 2)):
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO ledger_entries (id, account, direction, amount, reference_type, reference_id, created_at) VALUES (3, 'CAIXINHA', 'DEBIT', 10, 'REVERSAL', '1', :now) ON CONFLICT(id) DO NOTHING"), {"now": now})
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(sa.text("INSERT INTO payment_reversal_components (id, payment_reversal_id, original_ledger_entry_id, compensating_ledger_entry_id, created_at) VALUES (2, 1, :original, :compensating, :now)"), {"original": original, "compensating": compensating, "now": now})


def test_component_foreign_keys_and_mfe_partial_uniqueness_are_enforced():
    engine = _db()
    _seed(engine)
    _insert_reversal(engine)
    now = "2026-09-16 12:00:00+00:00"
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO payment_reversal_components (payment_reversal_id, original_ledger_entry_id, compensating_ledger_entry_id, created_at) VALUES (999, 999, 998, :now)"), {"now": now})
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO member_financial_entries (id, account_id, entry_type, direction, amount, payment_reversal_id, created_at) VALUES (1, 1, 'LOAN_PRINCIPAL_REVERSAL', 'DEBIT', 10, 1, :now)"), {"now": now})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO member_financial_entries (id, account_id, entry_type, direction, amount, payment_reversal_id, created_at) VALUES (2, 1, 'LOAN_PRINCIPAL_REVERSAL', 'DEBIT', 10, 1, :now)"), {"now": now})
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO member_financial_entries (id, account_id, entry_type, direction, amount, created_at) VALUES (3, 1, 'LOAN_PRINCIPAL_PAYMENT', 'CREDIT', 10, :now)"), {"now": now})


def test_loan_state_revision_and_settlement_loan_evidence_checks():
    engine = _db()
    _seed(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE loans SET state_revision = 0 WHERE id = 1"))
        connection.execute(sa.text("INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, loan_installment_id, amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, obligation_status_before, obligation_status_after, loan_status_before, loan_status_after, loan_state_revision_before, loan_state_revision_after, confirmed_at, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) VALUES (2, 2, 1, 'LOAN_INSTALLMENT', 1, 10, 10, 10, 0, 0, 0, 'OPEN', 'PAID', 'ACTIVE', 'PAID', 0, 1, CURRENT_TIMESTAMP, 'TEST', 'settlement-2', 'v2', '{}', 'settlement-hash-2', CURRENT_TIMESTAMP)"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE payment_settlements SET receipt_version = 'v3' WHERE id = 2"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE loans SET state_revision = -1 WHERE id = 1"))
    for values in (
        {"loan_status_before": "ACTIVE"},
        {"loan_status_before": "INVALID", "loan_status_after": "PAID", "loan_state_revision_before": 0, "loan_state_revision_after": 1},
        {"loan_status_before": "ACTIVE", "loan_status_after": "PAID", "loan_state_revision_before": -1, "loan_state_revision_after": 1},
        {"loan_status_before": "ACTIVE", "loan_status_after": "PAID", "loan_state_revision_before": 2, "loan_state_revision_after": 1},
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(sa.text("INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, loan_installment_id, amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, obligation_status_before, obligation_status_after, loan_status_before, loan_status_after, loan_state_revision_before, loan_state_revision_after, confirmed_at, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) VALUES (3, 2, 1, 'LOAN_INSTALLMENT', 1, 10, 10, 10, 0, 0, 0, 'OPEN', 'PAID', :before, :after, :revision_before, :revision_after, CURRENT_TIMESTAMP, 'TEST', :receipt, 'v2', '{}', :hash, CURRENT_TIMESTAMP)"), {"before": values.get("loan_status_before"), "after": values.get("loan_status_after"), "revision_before": values.get("loan_state_revision_before"), "revision_after": values.get("loan_state_revision_after"), "receipt": f"bad-{len(values)}", "hash": f"bad-hash-{len(values)}"})


def test_downgrade_empty_is_safe_and_restores_0082_shape():
    engine = _db()
    _seed(engine, include_loan=False)
    _downgrade(engine)
    inspector = inspect(engine)
    assert "payment_reversals" not in inspector.get_table_names()
    assert "payment_reversal_components" not in inspector.get_table_names()
    assert "payment_reversal_id" not in {column["name"] for column in inspector.get_columns("member_financial_entries")}
    assert "state_revision" not in {column["name"] for column in inspector.get_columns("loans")}
    assert "loan_status_before" not in {column["name"] for column in inspector.get_columns("payment_settlements")}
    settlement_checks = {check["name"]: check["sqltext"] for check in inspector.get_check_constraints("payment_settlements")}
    assert "ck_payment_settlements_receipt_version" in settlement_checks
    assert "v2" not in settlement_checks["ck_payment_settlements_receipt_version"]
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE payment_settlements SET receipt_version = 'v2' WHERE id = 1"))
    assert "uq_member_financial_entries_one_payment_reversal" not in {index["name"] for index in inspector.get_indexes("member_financial_entries")}


@pytest.mark.parametrize("evidence", ["reversal", "component", "mfe", "v2", "loan_fields", "revision"])
def test_downgrade_blocks_every_new_evidence_kind(evidence):
    engine = _db()
    _seed(engine)
    now = "2026-09-16 12:00:00+00:00"
    if evidence == "reversal":
        _insert_reversal(engine)
    elif evidence == "component":
        _insert_reversal(engine)
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO ledger_entries (id, account, direction, amount, reference_type, reference_id, created_at) VALUES (1, 'CAIXINHA', 'CREDIT', 10, 'CONTRIBUTION_PAYMENT', '1', :now), (2, 'CAIXINHA', 'DEBIT', 10, 'REVERSAL', '1', :now)"), {"now": now})
            connection.execute(sa.text("INSERT INTO payment_reversal_components (payment_reversal_id, original_ledger_entry_id, compensating_ledger_entry_id, created_at) VALUES (1, 1, 2, :now)"), {"now": now})
    elif evidence == "mfe":
        _insert_reversal(engine)
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO member_financial_entries (id, account_id, entry_type, direction, amount, payment_reversal_id, created_at) VALUES (1, 1, 'LOAN_PRINCIPAL_REVERSAL', 'DEBIT', 10, 1, :now)"), {"now": now})
    elif evidence == "v2":
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE payment_settlements SET receipt_version = 'v2' WHERE id = 1"))
    elif evidence == "loan_fields":
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE payment_settlements SET loan_status_before = 'ACTIVE', loan_status_after = 'PAID', loan_state_revision_before = 0, loan_state_revision_after = 1 WHERE id = 1"))
    else:
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE loans SET state_revision = 1 WHERE id = 1"))
    with pytest.raises(RuntimeError):
        _downgrade(engine)


def test_0084_adds_paid_at_evidence_and_accepts_v1_v2_v3():
    engine = _db()
    _seed(engine)
    _upgrade_0084(engine)
    columns = _columns(engine, "payment_settlements")
    assert isinstance(columns["loan_paid_at_before"]["type"], DateTime)
    assert isinstance(columns["loan_paid_at_after"]["type"], DateTime)
    assert columns["loan_paid_at_before"]["nullable"] is True
    assert columns["loan_paid_at_after"]["nullable"] is True

    original_v1 = ("v1", '{"legacy":true}', "settlement-hash-1")
    now = "2026-09-16 12:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE payment_settlements SET receipt_version = 'v2' WHERE id = 1"))
        connection.execute(sa.text(
            "INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, loan_installment_id, "
            "amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, "
            "obligation_status_before, obligation_status_after, loan_status_before, loan_status_after, "
            "loan_state_revision_before, loan_state_revision_after, loan_paid_at_before, loan_paid_at_after, "
            "confirmed_at, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) "
            "VALUES (2, 2, 1, 'LOAN_INSTALLMENT', 1, 10, 10, 10, 0, 0, 0, 'OPEN', 'PAID', 'ACTIVE', 'PAID', 0, 1, NULL, :now, CURRENT_TIMESTAMP, 'TEST', 'settlement-2', 'v3', '{}', 'settlement-hash-2', CURRENT_TIMESTAMP)"
        ), {"now": now})

    preserved_v2 = engine.connect().execute(sa.text(
        "SELECT receipt_version, receipt_snapshot_json, receipt_hash FROM payment_settlements WHERE id = 1"
    )).one()
    assert tuple(preserved_v2) == ("v2", original_v1[1], original_v1[2])

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE payment_settlements SET receipt_version = 'v3' WHERE id = 1"))


def test_0084_downgrade_empty_preserves_0083_and_rejects_v3_afterward():
    engine = _db()
    _seed(engine)
    original = engine.connect().execute(sa.text(
        "SELECT receipt_version, receipt_snapshot_json, receipt_hash FROM payment_settlements WHERE id = 1"
    )).one()
    _upgrade_0084(engine)
    _downgrade_0084(engine)
    columns = _columns(engine, "payment_settlements")
    assert "loan_paid_at_before" not in columns
    assert "loan_paid_at_after" not in columns
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE payment_settlements SET receipt_version = 'v2' WHERE id = 1"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE payment_settlements SET receipt_version = 'v3' WHERE id = 1"))
    restored = engine.connect().execute(sa.text(
        "SELECT receipt_version, receipt_snapshot_json, receipt_hash FROM payment_settlements WHERE id = 1"
    )).one()
    assert tuple(original) == ("v1", "{\"legacy\":true}", "settlement-hash-1")
    assert tuple(restored) == ("v2", "{\"legacy\":true}", "settlement-hash-1")


def test_0084_downgrade_blocks_v3_evidence_before_changes():
    engine = _db()
    _seed(engine)
    _upgrade_0084(engine)
    now = "2026-09-16 12:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, loan_installment_id, "
            "amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, "
            "obligation_status_before, obligation_status_after, loan_status_before, loan_status_after, "
            "loan_state_revision_before, loan_state_revision_after, loan_paid_at_before, loan_paid_at_after, "
            "confirmed_at, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) "
            "VALUES (2, 2, 1, 'LOAN_INSTALLMENT', 1, 10, 10, 10, 0, 0, 0, 'OPEN', 'PAID', 'ACTIVE', 'PAID', 0, 1, NULL, :now, CURRENT_TIMESTAMP, 'TEST', 'settlement-2', 'v3', '{}', 'settlement-hash-2', CURRENT_TIMESTAMP)"
        ), {"now": now})
    with pytest.raises(RuntimeError):
        _downgrade_0084(engine)
    assert "loan_paid_at_before" in _columns(engine, "payment_settlements")


def test_0084_downgrade_blocks_v3_with_null_paid_at_evidence():
    engine = _db()
    _seed(engine)
    _upgrade_0084(engine)
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO payment_settlements (id, payment_id, member_id, obligation_type, loan_installment_id, "
            "amount_received, amount_applied, principal_applied, interest_applied, penalty_applied, excess_amount, "
            "obligation_status_before, obligation_status_after, loan_status_before, loan_status_after, "
            "loan_state_revision_before, loan_state_revision_after, loan_paid_at_before, loan_paid_at_after, "
            "confirmed_at, confirmation_source, receipt_number, receipt_version, receipt_snapshot_json, receipt_hash, created_at) "
            "VALUES (2, 2, 1, 'LOAN_INSTALLMENT', 1, 10, 10, 10, 0, 0, 0, 'OPEN', 'PARTIAL', 'ACTIVE', 'ACTIVE', 0, 1, NULL, NULL, CURRENT_TIMESTAMP, 'TEST', 'settlement-2', 'v3', '{}', 'settlement-hash-2', CURRENT_TIMESTAMP)"
        ))
    with pytest.raises(RuntimeError):
        _downgrade_0084(engine)
    assert "loan_paid_at_before" in _columns(engine, "payment_settlements")
