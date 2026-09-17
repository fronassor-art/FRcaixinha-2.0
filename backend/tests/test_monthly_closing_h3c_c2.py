import json
from datetime import date

import pytest

from app.api.admin_finance import verify_month
from app.services.monthly_closing_v040 import snapshot_digest

from test_monthly_closing_h3b1 import _closing, _db


def _rewrite_snapshot(db, row, snapshot):
    raw = dict(snapshot)
    raw.pop("closing_schema")
    raw.pop("reconciliation_hash", None)
    snapshot["reconciliation_hash"] = snapshot_digest(raw)
    row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    row.snapshot_hash = snapshot_digest(snapshot)
    db.commit()


def test_verify_rejects_physical_competence_different_from_snapshot_competence():
    db = _db()
    snapshot = {
        "schema": "v0.40",
        "competence": "2027-01-01",
        "period_end": "2027-02-01",
        "contributions_paid": "100.00",
        "expenses_posted": "5.00",
        "interest_received": "20.00",
        "ledger_net": "115.00",
        "findings": [],
    }
    persisted = dict(snapshot)
    persisted["closing_schema"] = "v0.40"
    persisted["reconciliation_hash"] = snapshot_digest(snapshot)
    row = _closing(db, snapshot=persisted)

    result = verify_month(date(2026, 12, 1), admin=object(), db=db)

    assert json.loads(row.snapshot_json)["competence"] == "2027-01-01"
    assert result["status"] == "FAIL"


def test_verify_rejects_v034_snapshot_as_unsupported_by_modern_verifier():
    db = _db()
    snapshot = {
        "schema": "v0.34",
        "competence": "2026-12-01",
        "period_end": "2027-01-01",
        "contributions_paid": "100.00",
        "expenses_posted": "5.00",
        "interest_received": "20.00",
        "ledger_credits_in_period": "0.00",
        "ledger_debits_in_period": "0.00",
        "ledger_balance_at_close": "115.00",
    }
    row = _closing(db, snapshot=snapshot)

    result = verify_month(date(2026, 12, 1), admin=object(), db=db)

    assert "closing_schema" not in json.loads(row.snapshot_json)
    assert result["status"] == "FAIL"


@pytest.mark.parametrize("mutation", [
    lambda snapshot: snapshot.pop("competence"),
    lambda snapshot: snapshot.update(competence=None),
    lambda snapshot: snapshot.update(competence="not-a-date"),
])
def test_verify_rejects_missing_or_invalid_snapshot_competence(mutation):
    db = _db()
    row = _closing(db)
    snapshot = json.loads(row.snapshot_json)
    mutation(snapshot)
    _rewrite_snapshot(db, row, snapshot)

    result = verify_month(date(2026, 12, 1), admin=object(), db=db)

    assert result["status"] == "FAIL"
