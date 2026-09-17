import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.admin_finance import verify_month
from app.models import MonthlyClosing
from app.services.monthly_closing_v040 import close_month_v040
from app.services.payment_reversal import reverse_payment
from app.services.monthly_closing_v040 import snapshot_digest
from test_payment_reversal_contribution_v104 import _db, _setup
from test_payment_settlement_v103 import _installment, _member, _payment, _settle


def _persisted_snapshot():
    raw = {
        "schema": "v0.40",
        "competence": "2026-12-01",
        "period_end": "2027-01-01",
        "contributions_paid": "100.00",
        "expenses_posted": "5.00",
        "interest_received": "20.00",
        "ledger_net": "115.00",
        "findings": [],
    }
    snapshot = dict(raw)
    snapshot["closing_schema"] = "v0.40"
    snapshot["reconciliation_hash"] = snapshot_digest(raw)
    return snapshot


def _closing(db, *, snapshot="default", snapshot_hash_marker=True, status="CLOSED"):
    snapshot = _persisted_snapshot() if snapshot == "default" else snapshot
    raw = None if snapshot is None else json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    row = MonthlyClosing(
        competence=date(2026, 12, 1),
        status=status,
        total_contributions=Decimal("100.00"),
        total_expenses=Decimal("5.00"),
        total_interest_received=Decimal("20.00"),
        ledger_balance=Decimal("115.00"),
        snapshot_json=raw,
        snapshot_hash=snapshot_digest(snapshot) if snapshot_hash_marker and snapshot is not None else None,
    )
    db.add(row)
    db.commit()
    return row


def _verify(db, competence=date(2026, 12, 1)):
    return verify_month(competence, admin=object(), db=db)


def test_modern_closed_snapshot_is_verified_without_current_reconciliation(monkeypatch):
    db = _db()
    row = _closing(db)

    def current_reconciliation_must_not_run(*_args, **_kwargs):
        raise AssertionError("current reconciliation is not historical authority")

    import app.services.reconciliation_v040 as reconciliation
    monkeypatch.setattr(reconciliation, "build_advanced_reconciliation", current_reconciliation_must_not_run)

    result = _verify(db)

    assert set(result) == {"competence", "status", "stored_hash", "current_hash", "snapshot"}
    assert result["status"] == "PASS"
    assert result["stored_hash"] == row.snapshot_hash
    assert result["current_hash"] == json.loads(row.snapshot_json)["reconciliation_hash"]


def test_persisted_reconciliation_hash_is_returned_and_later_state_cannot_change_result(monkeypatch):
    db = _db()
    row = _closing(db)
    before = row.snapshot_json

    import app.services.reconciliation_v040 as reconciliation
    monkeypatch.setattr(reconciliation, "build_advanced_reconciliation", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))
    result = _verify(db)
    snapshot = json.loads(before)

    assert result["status"] == "PASS"
    assert result["current_hash"] == snapshot["reconciliation_hash"]
    assert result["snapshot"] == snapshot
    assert db.get(MonthlyClosing, row.id).snapshot_json == before


def test_closed_december_with_later_valid_contribution_reversal_remains_pass():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h3b1-contribution")
    december_payment_time = datetime(2026, 12, 20, 12, tzinfo=timezone.utc)
    settlement.confirmed_at = december_payment_time
    payment.created_at = december_payment_time
    payment.ledger_posted_at = december_payment_time
    db.execute(
        text("UPDATE ledger_entries SET created_at = :created_at WHERE reference_id = :payment_id"),
        {"created_at": december_payment_time, "payment_id": str(payment.id)},
    )
    db.commit()
    closing, _snapshot, _hash = close_month_v040(db, date(2026, 12, 1), admin.id)
    db.commit()
    before = (closing.status, closing.snapshot_json, closing.snapshot_hash)
    assert _verify(db)["status"] == "PASS"

    reverse_payment(
        db, payment_id=payment.id, admin_id=admin.id,
        reason="reversal after historical close",
        now=datetime(2027, 1, 10, 12, tzinfo=timezone.utc),
    )
    db.commit()

    db.expire_all()
    reloaded = db.query(MonthlyClosing).filter_by(competence=date(2026, 12, 1)).one()
    assert (reloaded.status, reloaded.snapshot_json, reloaded.snapshot_hash) == before
    assert _verify(db)["status"] == "PASS"


def test_closed_december_with_later_valid_interest_reversal_remains_pass():
    db = _db()
    admin, _contribution, _contribution_payment, _contribution_settlement = _setup(db, suffix="h3b1-interest")
    member = _member(db, "h3b1-interest-loan")
    _loan, installment = _installment(db, member, amount="120.00", interest="20.00", penalty="0.00")
    payment = _payment(
        db, suffix="h3b1-interest-loan", amount="120.00",
        reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id),
    )
    settlement = _settle(
        db, payment,
        when=datetime(2026, 12, 20, 12, tzinfo=timezone.utc),
    )
    db.commit()
    closing, _snapshot, _hash = close_month_v040(db, date(2026, 12, 1), admin.id)
    db.commit()
    before = (closing.status, closing.snapshot_json, closing.snapshot_hash)
    assert _verify(db)["status"] == "PASS"

    reverse_payment(
        db, payment_id=payment.id, admin_id=admin.id,
        reason="interest reversal after historical close",
        now=datetime(2027, 1, 10, 12, tzinfo=timezone.utc),
    )
    db.commit()

    db.expire_all()
    reloaded = db.query(MonthlyClosing).filter_by(competence=date(2026, 12, 1)).one()
    assert (reloaded.status, reloaded.snapshot_json, reloaded.snapshot_hash) == before
    assert settlement.interest_applied == Decimal("20.00")
    assert _verify(db)["status"] == "PASS"


@pytest.mark.parametrize("tamper", [
    "snapshot_json",
    "snapshot_hash",
    "reconciliation_hash",
    "metric",
    "finding",
])
def test_snapshot_tampering_fails_without_current_state_fallback(tamper):
    db = _db()
    row = _closing(db)
    snapshot = json.loads(row.snapshot_json)
    if tamper == "snapshot_json":
        snapshot["contributions_paid"] = "101.00"
        row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    elif tamper == "snapshot_hash":
        row.snapshot_hash = "0" * 64
    elif tamper == "reconciliation_hash":
        snapshot["reconciliation_hash"] = "0" * 64
        row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    elif tamper == "metric":
        snapshot["interest_received"] = "21.00"
        row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    else:
        snapshot["findings"] = [{"code": "TAMPER", "status": "FAIL"}]
        row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    db.commit()

    assert _verify(db)["status"] == "FAIL"


@pytest.mark.parametrize("column", [
    "total_contributions",
    "total_expenses",
    "total_interest_received",
    "ledger_balance",
])
def test_persisted_totals_must_match_historical_snapshot(column):
    db = _db()
    row = _closing(db)
    setattr(row, column, Decimal("999.99"))
    db.commit()

    assert _verify(db)["status"] == "FAIL"


@pytest.mark.parametrize("snapshot,hash_marker", [
    (None, False),
    (None, True),
])
def test_missing_snapshot_or_hash_is_deterministic_fail(snapshot, hash_marker):
    db = _db()
    if snapshot is None and hash_marker:
        row = MonthlyClosing(
            competence=date(2026, 12, 1), status="CLOSED", snapshot_hash="a" * 64,
        )
        db.add(row)
        db.commit()
    else:
        row = _closing(db, snapshot=snapshot, snapshot_hash_marker=hash_marker)
    result = _verify(db)

    assert result["status"] == "FAIL"
    assert result["snapshot"] is None


@pytest.mark.parametrize("mutation", [
    lambda snapshot: snapshot.pop("reconciliation_hash"),
    lambda snapshot: snapshot.pop("closing_schema"),
    lambda snapshot: snapshot.update(closing_schema="v9.99"),
])
def test_legacy_or_unsupported_snapshot_is_fail(mutation):
    db = _db()
    row = _closing(db)
    snapshot = json.loads(row.snapshot_json)
    mutation(snapshot)
    row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    db.commit()

    result = _verify(db)
    assert result["status"] == "FAIL"


def test_invalid_json_returns_http_200_shape_and_fail():
    db = _db()
    row = _closing(db)
    row.snapshot_json = "not-json"
    db.commit()

    result = _verify(db)

    assert result == {
        "competence": "2026-12-01",
        "status": "FAIL",
        "stored_hash": row.snapshot_hash,
        "current_hash": None,
        "snapshot": None,
    }


def test_verify_is_read_only_and_repeatable():
    db = _db()
    row = _closing(db)
    before = {
        "status": row.status,
        "snapshot_json": row.snapshot_json,
        "snapshot_hash": row.snapshot_hash,
        "total_contributions": row.total_contributions,
        "total_expenses": row.total_expenses,
        "total_interest_received": row.total_interest_received,
        "ledger_balance": row.ledger_balance,
    }

    first = _verify(db)
    second = _verify(db)
    db.expire_all()
    after = db.get(MonthlyClosing, row.id)

    assert first == second
    assert {key: getattr(after, key) for key in before} == before


def test_missing_or_open_closing_remains_404():
    db = _db()
    with pytest.raises(HTTPException) as missing:
        _verify(db)
    assert missing.value.status_code == 404

    _closing(db, status="OPEN")
    with pytest.raises(HTTPException) as open_closing:
        _verify(db)
    assert open_closing.value.status_code == 404
