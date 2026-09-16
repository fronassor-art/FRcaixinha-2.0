"""Structural and receipt contracts for PaymentSettlement v4."""

from datetime import date, datetime, timezone
import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import DateTime, String, inspect
from sqlalchemy.exc import IntegrityError

from test_payment_reversal_migration_v104 import (
    _columns,
    _db,
    _load_migration_0084,
    _seed,
    _upgrade_0084,
)
from test_payment_settlement_v103 import (
    _db as _settlement_db,
    _installment,
    _member,
    _payment,
    _settle,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_0085_PATH = BACKEND_ROOT / "alembic" / "versions" / "0085_payment_settlement_installment_state_v105.py"


def _load_migration_0085():
    spec = spec_from_file_location("migration_0085", MIGRATION_0085_PATH)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade_0085(engine):
    migration = _load_migration_0085()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()


def _downgrade_0085(engine):
    migration = _load_migration_0085()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()


def _v4_values(**overrides):
    values = {
        "id": 2,
        "payment_id": 2,
        "member_id": 1,
        "obligation_type": "LOAN_INSTALLMENT",
        "loan_installment_id": 1,
        "amount_received": 10,
        "amount_applied": 10,
        "principal_applied": 10,
        "interest_applied": 0,
        "penalty_applied": 0,
        "excess_amount": 0,
        "obligation_status_before": "PENDING",
        "obligation_status_after": "PARTIAL",
        "loan_status_before": "ACTIVE",
        "loan_status_after": "ACTIVE",
        "loan_state_revision_before": 0,
        "loan_state_revision_after": 1,
        "confirmed_at": "2026-09-16 12:00:00+00:00",
        "confirmation_source": "TEST",
        "receipt_number": "PIX-V4-000000000002",
        "receipt_version": "v4",
        "receipt_snapshot_json": "{}",
        "receipt_hash": "v4-hash",
        "created_at": "2026-09-16 12:00:00+00:00",
        "loan_installment_status_before": "OPEN",
        "loan_installment_status_after": "PARTIAL",
        "loan_installment_paid_at_before": None,
        "loan_installment_paid_at_after": None,
    }
    values.update(overrides)
    return values


def _insert_settlement(engine, **overrides):
    values = _v4_values(**overrides)
    columns = ", ".join(values)
    placeholders = ", ".join(f":{key}" for key in values)
    with engine.begin() as connection:
        connection.execute(sa.text(
            f"INSERT INTO payment_settlements ({columns}) VALUES ({placeholders})"
        ), values)


def _engine_0085():
    engine = _db()
    _seed(engine)
    _upgrade_0084(engine)
    _upgrade_0085(engine)
    return engine


def test_0085_adds_nullable_installment_state_columns_and_keeps_head():
    engine = _engine_0085()
    columns = _columns(engine, "payment_settlements")
    for name in (
        "loan_installment_status_before",
        "loan_installment_status_after",
        "loan_installment_paid_at_before",
        "loan_installment_paid_at_after",
    ):
        assert columns[name]["nullable"] is True
    assert isinstance(columns["loan_installment_status_before"]["type"], String)
    assert columns["loan_installment_status_before"]["type"].length == 20
    assert isinstance(columns["loan_installment_paid_at_before"]["type"], DateTime)
    assert "0085_payment_settlement_installment_state_v105" == _load_migration_0085().revision
    engine.dispose()


@pytest.mark.parametrize("overrides", [
    {"obligation_type": "CONTRIBUTION", "contribution_id": 1, "loan_installment_id": None},
    {"loan_installment_status_before": None},
    {"loan_installment_status_after": None},
    {"receipt_version": "v3", "loan_installment_status_before": "OPEN"},
])
def test_0085_constraints_reject_invalid_v4_or_new_fields(overrides):
    engine = _engine_0085()
    with pytest.raises(IntegrityError):
        _insert_settlement(engine, **overrides)
    engine.dispose()


def test_0085_accepts_v4_with_null_paid_at_evidence_and_preserves_v1():
    engine = _engine_0085()
    _insert_settlement(engine)
    with engine.begin() as connection:
        row = connection.execute(sa.text(
            "SELECT receipt_version, loan_installment_status_before, "
            "loan_installment_status_after, loan_installment_paid_at_before, "
            "loan_installment_paid_at_after FROM payment_settlements WHERE id = 2"
        )).one()
        assert tuple(row) == ("v4", "OPEN", "PARTIAL", None, None)
        legacy = connection.execute(sa.text(
            "SELECT receipt_version, receipt_snapshot_json, receipt_hash "
            "FROM payment_settlements WHERE id = 1"
        )).one()
        assert tuple(legacy) == ("v1", '{"legacy":true}', "settlement-hash-1")
    engine.dispose()


def test_v4_paid_at_is_nullable_evidence_and_preserves_utc_value():
    engine = _engine_0085()
    value = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    _insert_settlement(
        engine,
        loan_installment_paid_at_before=value,
        loan_installment_paid_at_after=value,
    )
    with engine.connect() as connection:
        row = connection.execute(sa.text(
            "SELECT loan_installment_paid_at_before, loan_installment_paid_at_after "
            "FROM payment_settlements WHERE id = 2"
        )).one()
        assert row[0] == row[1]
    engine.dispose()


def test_0085_downgrade_is_reversible_when_no_v4_evidence_exists():
    engine = _engine_0085()
    _downgrade_0085(engine)
    columns = _columns(engine, "payment_settlements")
    assert "loan_installment_status_before" not in columns
    assert "loan_installment_paid_at_after" not in columns
    engine.dispose()


def _structure_snapshot(engine):
    inspector = inspect(engine)

    def normalized_checks(table):
        return {
            (row["name"], " ".join((row.get("sqltext") or "").split()))
            for row in inspector.get_check_constraints(table)
        }

    return {
        "pk": inspector.get_pk_constraint("payment_settlements"),
        "fks": sorted(
            (
                tuple(row["constrained_columns"]),
                row["referred_table"],
                tuple(row["referred_columns"]),
            )
            for row in inspector.get_foreign_keys("payment_settlements")
        ),
        "uniques": sorted(
            (row["name"], tuple(row["column_names"]))
            for row in inspector.get_unique_constraints("payment_settlements")
        ),
        "indexes": sorted(
            (row["name"], tuple(row["column_names"]), bool(row["unique"]))
            for row in inspector.get_indexes("payment_settlements")
        ),
        "columns": {
            row["name"]: (str(row["type"]), row["nullable"], row["default"])
            for row in inspector.get_columns("payment_settlements")
            if row["name"] not in {
                "loan_installment_status_before",
                "loan_installment_status_after",
                "loan_installment_paid_at_before",
                "loan_installment_paid_at_after",
            }
        },
        "checks": normalized_checks("payment_settlements"),
    }


def test_0085_sqlite_rebuild_preserves_preexisting_structure():
    engine = _db()
    _seed(engine)
    _upgrade_0084(engine)
    before = _structure_snapshot(engine)
    changed_checks = {
        "ck_payment_settlements_receipt_version",
        "ck_payment_settlements_paid_at_version",
        "ck_payment_settlements_v3_loan_only",
    }
    new_checks = {
        "ck_payment_settlements_installment_state_version",
        "ck_payment_settlements_v4_installment_state_complete",
    }
    _upgrade_0085(engine)
    after = _structure_snapshot(engine)

    assert after["pk"] == before["pk"]
    assert after["fks"] == before["fks"]
    assert after["uniques"] == before["uniques"]
    assert after["indexes"] == before["indexes"]
    assert after["columns"] == before["columns"]
    assert {
        check for check in after["checks"]
        if check[0] not in changed_checks | new_checks
    } == {
        check for check in before["checks"]
        if check[0] not in changed_checks
    }
    engine.dispose()


def test_0085_downgrade_refuses_v4_without_discarding_data():
    engine = _engine_0085()
    _insert_settlement(engine)
    with pytest.raises(RuntimeError, match="v4 installment evidence"):
        _downgrade_0085(engine)
    with engine.connect() as connection:
        row = connection.execute(sa.text(
            "SELECT receipt_version, loan_installment_status_before, "
            "loan_installment_status_after FROM payment_settlements WHERE id = 2"
        )).one()
        assert tuple(row) == ("v4", "OPEN", "PARTIAL")
    assert "loan_installment_status_before" in _columns(engine, "payment_settlements")
    engine.dispose()


def _assert_v4_installment_evidence(settlement, *, status_before, status_after, paid_at_before, paid_at_after):
    def utc_iso(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    assert settlement.receipt_version == "v4"
    assert settlement.loan_installment_status_before == status_before
    assert settlement.loan_installment_status_after == status_after
    assert settlement.loan_installment_paid_at_before == paid_at_before
    assert settlement.loan_installment_paid_at_after == paid_at_after
    snapshot = json.loads(settlement.receipt_snapshot_json)
    assert snapshot["loan_installment_state"] == {
        "status_before": status_before,
        "status_after": status_after,
        "paid_at_before": utc_iso(paid_at_before),
        "paid_at_after": utc_iso(paid_at_after),
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert settlement.receipt_snapshot_json == canonical
    assert settlement.receipt_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_v4_open_to_partial_captures_literal_operational_and_derived_states():
    db = _settlement_db()
    member = _member(db, "v4-open-partial")
    _, installment = _installment(db, member, amount="120.00", interest="20.00")
    payment = _payment(db, suffix="v4-open-partial", amount="50.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement = _settle(db, payment)
    _assert_v4_installment_evidence(settlement, status_before="OPEN", status_after="PARTIAL", paid_at_before=None, paid_at_after=None)
    assert settlement.obligation_status_before == "PENDING"
    assert settlement.obligation_status_after == "PARTIAL"
    db.close()


def test_v4_open_to_paid_captures_utc_completion_timestamp():
    db = _settlement_db()
    member = _member(db, "v4-open-paid")
    _, installment = _installment(db, member, amount="120.00", interest="20.00")
    payment = _payment(db, suffix="v4-open-paid", amount="120.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement = _settle(db, payment)
    assert settlement.loan_installment_paid_at_after is not None
    _assert_v4_installment_evidence(settlement, status_before="OPEN", status_after="PAID", paid_at_before=None, paid_at_after=settlement.loan_installment_paid_at_after)
    parsed_paid_at = datetime.fromisoformat(json.loads(settlement.receipt_snapshot_json)["loan_installment_state"]["paid_at_after"])
    assert parsed_paid_at.tzinfo is not None
    assert parsed_paid_at.utcoffset() == timezone.utc.utcoffset(parsed_paid_at)
    assert settlement.obligation_status_before == "PENDING"
    assert settlement.obligation_status_after == "PAID"
    db.close()


def test_v4_partial_to_partial_captures_existing_partial_state():
    db = _settlement_db()
    member = _member(db, "v4-partial-partial")
    _, installment = _installment(db, member, amount="120.00", interest="20.00")
    installment.paid_amount = 40
    installment.status = "PARTIAL"
    db.flush()
    payment = _payment(db, suffix="v4-partial-partial", amount="10.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement = _settle(db, payment)
    _assert_v4_installment_evidence(settlement, status_before="PARTIAL", status_after="PARTIAL", paid_at_before=None, paid_at_after=None)
    assert settlement.obligation_status_before == "PARTIAL"
    assert settlement.obligation_status_after == "PARTIAL"
    db.close()


def test_v4_partial_to_paid_captures_existing_partial_state_and_completion():
    db = _settlement_db()
    member = _member(db, "v4-partial-paid")
    _, installment = _installment(db, member, amount="120.00", interest="20.00")
    installment.paid_amount = 40
    installment.status = "PARTIAL"
    db.flush()
    payment = _payment(db, suffix="v4-partial-paid", amount="80.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement = _settle(db, payment)
    assert settlement.loan_installment_paid_at_after is not None
    _assert_v4_installment_evidence(settlement, status_before="PARTIAL", status_after="PAID", paid_at_before=None, paid_at_after=settlement.loan_installment_paid_at_after)
    parsed_paid_at = datetime.fromisoformat(json.loads(settlement.receipt_snapshot_json)["loan_installment_state"]["paid_at_after"])
    assert parsed_paid_at.tzinfo is not None
    assert parsed_paid_at.utcoffset() == timezone.utc.utcoffset(parsed_paid_at)
    assert settlement.obligation_status_before == "PARTIAL"
    assert settlement.obligation_status_after == "PAID"
    db.close()


def test_v4_receipt_captures_literal_installment_state_and_retry_is_immutable():
    db = _settlement_db()
    member = _member(db, "settlement-v4-receipt")
    loan, installment = _installment(db, member, amount="120.00", interest="20.00")
    payment = _payment(
        db,
        suffix="settlement-v4-receipt",
        amount="50.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    first = _settle(db, payment)
    snapshot = first.receipt_snapshot_json
    receipt_hash = first.receipt_hash

    assert first.receipt_version == "v4"
    assert first.receipt_number == f"PIX-V4-{payment.id:012d}"
    assert first.loan_installment_status_before == "OPEN"
    assert first.loan_installment_status_after == "PARTIAL"
    assert first.loan_installment_paid_at_before is None
    assert first.loan_installment_paid_at_after is None
    assert '"loan_installment_state"' in snapshot
    assert '"paid_at_after":null' in snapshot

    second = _settle(db, payment, remote_payload={"status_detail": "ignored"})
    assert second.id == first.id
    assert second.receipt_version == "v4"
    assert second.receipt_snapshot_json == snapshot
    assert second.receipt_hash == receipt_hash
    db.close()
