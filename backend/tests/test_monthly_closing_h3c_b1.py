import sqlite3
from types import SimpleNamespace

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api import admin_finance
from app.models import MonthlyClosing
from app.services import monthly_closing_v034, monthly_closing_v040
from app.services.monthly_closing_concurrency import (
    MonthlyClosingCreationConflict,
    _is_competence_unique_violation,
    create_monthly_closing_or_raise_conflict,
)
from test_payment_reversal_contribution_v104 import _db, _setup


def _closed(db, competence=date(2026, 12, 1)):
    row = MonthlyClosing(
        competence=competence,
        status="CLOSED",
        total_contributions=Decimal("10.00"),
        total_expenses=Decimal("2.00"),
        total_interest_received=Decimal("1.00"),
        ledger_balance=Decimal("9.00"),
        snapshot_json="{}",
        snapshot_hash="a" * 64,
    )
    db.add(row)
    db.commit()
    return row


def test_missing_row_is_created_inside_nested_transaction():
    db = _db()

    row = create_monthly_closing_or_raise_conflict(db, date(2026, 12, 1))

    assert row.id is not None
    assert db.query(MonthlyClosing).count() == 1


def test_existing_open_row_remains_available_to_v040(monkeypatch):
    db = _db()
    _setup(db, suffix="h3c-b1-open")
    existing = MonthlyClosing(competence=date(2026, 12, 1), status="OPEN")
    db.add(existing)
    db.commit()

    monkeypatch.setattr(
        monthly_closing_v040,
        "build_advanced_reconciliation",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "snapshot_hash": "a" * 64,
            "snapshot": {
                "contributions_paid": "0.00",
                "expenses_posted": "0.00",
                "interest_received": "0.00",
                "ledger_net": "0.00",
            },
        },
    )

    closing, _snapshot, _hash = monthly_closing_v040.close_month_v040(
        db, existing.competence, admin_id=1
    )

    assert closing.id == existing.id
    assert closing.status == "CLOSED"


def test_existing_closed_row_keeps_h3c_a2_value_error():
    db = _db()
    row = _closed(db)

    with pytest.raises(ValueError, match=r"^Competência já encerrada\.$"):
        monthly_closing_v040.close_month_v040(db, row.competence, admin_id=1)


def test_unique_collision_is_classified_and_outer_session_remains_usable():
    db = _db()
    winner = _closed(db, date(2026, 12, 1))

    with pytest.raises(MonthlyClosingCreationConflict) as error:
        create_monthly_closing_or_raise_conflict(db, winner.competence)

    assert str(error.value) == MonthlyClosingCreationConflict.MESSAGE
    assert db.get(MonthlyClosing, winner.id).status == "CLOSED"

    other = create_monthly_closing_or_raise_conflict(db, date(2027, 1, 1))
    db.commit()
    assert db.get(MonthlyClosing, other.id).competence == date(2027, 1, 1)


def test_non_unique_integrity_error_propagates_without_conflict_mapping():
    db = _db()

    with pytest.raises(IntegrityError):
        create_monthly_closing_or_raise_conflict(db, None)

    db.rollback()


@pytest.mark.parametrize(
    "constraint_name",
    ["uq_monthly_closing_competence", "ix_monthly_closings_competence"],
)
def test_postgresql_competence_unique_names_are_classified(constraint_name):
    original = SimpleNamespace(
        sqlstate="23505",
        diag=SimpleNamespace(constraint_name=constraint_name),
    )
    assert _is_competence_unique_violation(IntegrityError("insert", {}, original))


@pytest.mark.parametrize(
    "sqlstate,constraint_name",
    [("23505", "other_unique"), ("23503", "ix_monthly_closings_competence")],
)
def test_postgresql_other_integrity_diagnostics_are_not_classified(
    sqlstate, constraint_name
):
    original = SimpleNamespace(
        sqlstate=sqlstate,
        diag=SimpleNamespace(constraint_name=constraint_name),
    )
    assert not _is_competence_unique_violation(IntegrityError("insert", {}, original))


def test_postgresql_missing_diagnostics_are_not_classified():
    original = SimpleNamespace(sqlstate="23505")
    assert not _is_competence_unique_violation(IntegrityError("insert", {}, original))


def test_sqlite_competence_message_is_classified_without_generic_fallback():
    original = sqlite3.IntegrityError(
        "UNIQUE constraint failed: monthly_closings.competence"
    )
    assert _is_competence_unique_violation(
        IntegrityError("insert", {}, original)
    )


def test_sqlite_other_unique_message_is_not_classified():
    original = sqlite3.IntegrityError("UNIQUE constraint failed: users.email")
    assert not _is_competence_unique_violation(IntegrityError("insert", {}, original))


def test_v040_loser_does_not_run_reconciliation_or_overwrite_winner(monkeypatch):
    db = _db()
    competence = date(2026, 12, 1)
    winner = _closed(db, date(2027, 1, 1))
    monkeypatch.setattr(
        monthly_closing_v040,
        "create_monthly_closing_or_raise_conflict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MonthlyClosingCreationConflict()),
    )
    monkeypatch.setattr(
        monthly_closing_v040,
        "build_advanced_reconciliation",
        lambda *_args, **_kwargs: pytest.fail("loser must not reconcile"),
    )

    with pytest.raises(MonthlyClosingCreationConflict):
        monthly_closing_v040.close_month_v040(db, competence, admin_id=999)

    db.rollback()
    loaded = db.get(MonthlyClosing, winner.id)
    assert loaded.status == "CLOSED"
    assert loaded.closed_by is None


def test_v034_uses_creation_conflict_protection(monkeypatch):
    db = _db()
    competence = date(2026, 12, 1)
    winner = _closed(db, date(2027, 1, 1))
    monkeypatch.setattr(
        monthly_closing_v034,
        "create_monthly_closing_or_raise_conflict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MonthlyClosingCreationConflict()),
    )
    monkeypatch.setattr(
        monthly_closing_v034,
        "reconcile",
        lambda *_args, **_kwargs: pytest.fail("loser must not reconcile"),
    )

    with pytest.raises(MonthlyClosingCreationConflict):
        monthly_closing_v034.close_month(db, competence, admin_id=999)

    db.rollback()
    assert db.get(MonthlyClosing, winner.id).status == "CLOSED"


def test_endpoint_maps_creation_conflict_to_http_409(monkeypatch):
    db = _db()

    def conflict(*_args, **_kwargs):
        raise MonthlyClosingCreationConflict()

    monkeypatch.setattr(monthly_closing_v040, "close_month_v040", conflict)

    with pytest.raises(HTTPException) as error:
        admin_finance.close_month(
            date(2026, 12, 1), admin=type("Admin", (), {"id": 1})(), db=db
        )

    assert error.value.status_code == 409
    assert error.value.detail == MonthlyClosingCreationConflict.MESSAGE


def test_endpoint_preserves_value_error_http_409(monkeypatch):
    db = _db()

    def closed(*_args, **_kwargs):
        raise ValueError("Competência já encerrada.")

    monkeypatch.setattr(monthly_closing_v040, "close_month_v040", closed)

    with pytest.raises(HTTPException) as error:
        admin_finance.close_month(
            date(2026, 12, 1), admin=type("Admin", (), {"id": 1})(), db=db
        )

    assert error.value.status_code == 409
    assert error.value.detail == "Competência já encerrada."
