from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api import admin_finance
from app.models import MonthlyClosing
from app.services import monthly_closing_v034, monthly_closing_v040
from app.services.monthly_closing_guard import ensure_monthly_closing_open
from test_payment_reversal_contribution_v104 import _db, _setup


def _closed(db, competence=date(2026, 12, 1)):
    row = MonthlyClosing(
        competence=competence,
        status="CLOSED",
        total_contributions=Decimal("10.00"),
        total_expenses=Decimal("2.00"),
        total_interest_received=Decimal("1.00"),
        ledger_balance=Decimal("9.00"),
        snapshot_json='{"closing_schema":"v0.40"}',
        snapshot_hash="a" * 64,
    )
    db.add(row)
    db.commit()
    return row


def test_guard_accepts_open_and_does_not_mutate_object():
    row = MonthlyClosing(competence=date(2026, 12, 1), status="OPEN")
    before = row.status

    assert ensure_monthly_closing_open(row) is None
    assert row.status == before


def test_guard_rejects_closed_without_mutating_object():
    row = MonthlyClosing(competence=date(2026, 12, 1), status="CLOSED")
    before = row.status

    with pytest.raises(ValueError, match="^Competência já encerrada\\.$"):
        ensure_monthly_closing_open(row)

    assert row.status == before


def test_v040_rejects_closed_before_reconciliation(monkeypatch):
    db = _db()
    row = _closed(db)

    monkeypatch.setattr(
        monthly_closing_v040,
        "build_advanced_reconciliation",
        lambda *_args, **_kwargs: pytest.fail("reconciliation must not run"),
    )

    with pytest.raises(ValueError, match="^Competência já encerrada\\.$"):
        monthly_closing_v040.close_month_v040(db, row.competence, admin_id=1)

    db.rollback()
    loaded = db.get(MonthlyClosing, row.id)
    assert loaded.status == "CLOSED"
    assert loaded.snapshot_hash == "a" * 64


def test_v034_rejects_closed_before_reconciliation(monkeypatch):
    db = _db()
    row = _closed(db)

    monkeypatch.setattr(
        monthly_closing_v034,
        "reconcile",
        lambda *_args, **_kwargs: pytest.fail("reconciliation must not run"),
    )

    with pytest.raises(ValueError, match="^Competência já encerrada\\.$"):
        monthly_closing_v034.close_month(db, row.competence, admin_id=1)

    db.rollback()
    loaded = db.get(MonthlyClosing, row.id)
    assert loaded.status == "CLOSED"
    assert loaded.snapshot_hash == "a" * 64


def test_endpoint_preserves_http_409_for_closed_competence():
    db = _db()
    row = _closed(db)

    with pytest.raises(HTTPException) as error:
        admin_finance.close_month(row.competence, admin=type("Admin", (), {"id": 1})(), db=db)

    assert error.value.status_code == 409
    assert error.value.detail == "Competência já encerrada."


def test_open_closing_flow_remains_available():
    db = _db()
    admin, _contribution, _payment, _settlement = _setup(db, suffix="h3c-a2-open")

    closing, _snapshot, _snapshot_hash = monthly_closing_v040.close_month_v040(
        db, date(2026, 12, 1), admin.id
    )

    assert closing.status == "CLOSED"
