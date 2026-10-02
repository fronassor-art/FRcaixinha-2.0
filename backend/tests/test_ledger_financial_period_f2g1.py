from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib

import pytest
from sqlalchemy import text

from app.services import ledger as ledger_service
from app.services.cycle_closing_workflow import _ledger_cash_at_cutoff
from app.services.ledger import ledger_entries_for_financial_period, post_entry, post_entry_v2
from app.services.monthly_closing_v034 import build_snapshot
from app.services.reconciliation_v040 import build_advanced_reconciliation
from app.services.reports_v042 import _period_ledger, monthly_accountability
from app.services.reports_v10 import monthly_report, member_statement
from app.services.payment_settlement import _canonical_json
from app.services.temporal_event_receipts import build_settlement_v6_snapshot

from test_payment_settlement_v103 import (
    _contribution, _db, _member, _payment, _set_temporal_settlement, _settle,
)


class FrozenLedgerClock(datetime):
    current = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


def _mixed_ledger(db, monkeypatch):
    FrozenLedgerClock.current = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(ledger_service, "datetime", FrozenLedgerClock)
    legacy = post_entry(db, "CAIXINHA", "CREDIT", Decimal("8.00"), "F2G1_TEST", "legacy")
    belem_september = post_entry_v2(
        db, "CAIXINHA", "CREDIT", Decimal("12.34"), "F2G1_TEST", "v2-september",
        financial_date=date(2026, 9, 30),
    )
    FrozenLedgerClock.current = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
    belem_october = post_entry_v2(
        db, "CAIXINHA", "CREDIT", Decimal("3.00"), "F2G1_TEST", "v2-october",
        financial_date=date(2026, 10, 1),
    )
    db.flush()
    return legacy, belem_september, belem_october


def _period(db, month):
    start = datetime(2026, month, 1, tzinfo=timezone.utc)
    end = datetime(2026, month + 1, 1, tzinfo=timezone.utc)
    first = date(2026, month, 1)
    last_exclusive = date(2026, month + 1, 1)
    return ledger_entries_for_financial_period(
        db, start=start, end=end, financial_start=first,
        financial_end_exclusive=last_exclusive,
    )


def test_mixed_ledger_period_keeps_v1_utc_and_selects_v2_by_civil_date(monkeypatch):
    db = _db()
    legacy, v2_september, v2_october = _mixed_ledger(db, monkeypatch)

    september = _period(db, 9)
    october = _period(db, 10)

    assert [row.id for row in september] == [v2_september.id]
    assert [row.id for row in october] == [legacy.id, v2_october.id]
    assert sum((row.amount for row in september), Decimal("0")) == Decimal("12.34")
    assert sum((row.amount for row in october), Decimal("0")) == Decimal("11.00")
    assert legacy.hash_version is None and legacy.financial_date is None
    assert v2_september.created_at.replace(tzinfo=timezone.utc) == datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    assert v2_september.financial_date == date(2026, 9, 30)
    assert v2_october.created_at.replace(tzinfo=timezone.utc) == datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
    assert v2_october.financial_date == date(2026, 10, 1)
    db.close()


def test_period_reader_fails_closed_for_v2_hash_date_tampering(monkeypatch):
    db = _db()
    _legacy, temporal, _next = _mixed_ledger(db, monkeypatch)
    db.execute(
        text("UPDATE ledger_entries SET financial_date = :financial_date WHERE id = :id"),
        {"financial_date": "2026-10-01", "id": temporal.id},
    )
    db.expire_all()

    with pytest.raises(ValueError, match="invalid ledger chain"):
        _period(db, 9)
    db.close()


def test_monthly_report_reconciliation_and_closing_use_mixed_period_once(monkeypatch):
    db = _db()
    _mixed_ledger(db, monkeypatch)

    assert _period_ledger(db, date(2026, 9, 1), date(2026, 9, 30)) == (
        Decimal("12.34"), Decimal("0.00")
    )
    report_v042 = monthly_accountability(db, date(2026, 9, 1))
    report_v10 = monthly_report(db, date(2026, 9, 1))
    reconciliation = build_advanced_reconciliation(db, date(2026, 9, 1))["snapshot"]
    closing, _digest = build_snapshot(db, date(2026, 9, 1))

    assert report_v042["ledger"] == {
        "credits": "12.34", "debits": "0.00", "net": "12.34"
    }
    assert report_v10["ledger_credits_in_period"] == "12.34"
    assert report_v10["ledger_debits_in_period"] == "0.00"
    assert reconciliation["ledger_credits"] == "12.34"
    assert reconciliation["ledger_debits"] == "0.00"
    assert closing["ledger_credits_in_period"] == "12.34"
    assert closing["ledger_debits_in_period"] == "0.00"

    october_v042 = monthly_accountability(db, date(2026, 10, 1))
    october_v10 = monthly_report(db, date(2026, 10, 1))
    assert october_v042["ledger"]["credits"] == "11.00"
    assert october_v10["ledger_credits_in_period"] == "11.00"
    db.close()


def test_annual_cash_cutoff_remains_created_at_plus_id(monkeypatch):
    db = _db()
    _legacy, _v2_september, v2_october = _mixed_ledger(db, monkeypatch)

    balance, trace = _ledger_cash_at_cutoff(
        db, datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    )

    assert balance == Decimal("20.34")
    assert [item["id"] for item in trace] == [1, 2]
    assert v2_october.id not in {item["id"] for item in trace}
    db.close()


def test_statement_keeps_technical_occurred_at_and_adds_authenticated_temporal_date():
    db = _db()
    member = _member(db, "statement-v6")
    contribution = _contribution(db, member, amount="100.00")
    payment = _payment(
        db, suffix="statement-v6", amount="100.00",
        reference_type="CONTRIBUTION", reference_id=str(contribution.id),
    )
    confirmed_at = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    settlement = _settle(db, payment, when=confirmed_at)
    _set_temporal_settlement(db, settlement, date(2026, 9, 30))
    snapshot = build_settlement_v6_snapshot(db, payment, settlement)
    canonical = _canonical_json(snapshot)
    settlement.receipt_snapshot_json = canonical
    settlement.receipt_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    db.flush()

    movement = next(
        item for item in member_statement(db, member.id)["movements"]
        if item["payment_id"] == payment.id
    )
    assert movement["occurred_at"] == "2026-10-01T02:59:59+00:00"
    assert movement["financial_date"] == "2026-09-30"
    db.close()
