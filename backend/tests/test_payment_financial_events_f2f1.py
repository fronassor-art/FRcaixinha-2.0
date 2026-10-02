import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models import LedgerEntry, MemberFinancialEntry, PaymentReversal
from app.services.late_charge_v1 import financial_civil_date
from app.services.payment_event_periods import (
    PaymentEventEvidenceError,
    UnsupportedPaymentEventVersion,
    classify_settlement_event,
)
from app.services.payment_financial_events import payment_financial_events
from app.services.payment_reversal import reverse_payment
from app.services.payment_settlement import _canonical_json
from app.services.temporal_event_receipts import (
    build_reversal_v2_snapshot,
    build_settlement_v6_snapshot,
)
from test_payment_financial_events_h2b1 import setup_contribution
from test_payment_reversal_loan_v105 import _loan_payment
from test_agreement_payment_settlement_v104 import (
    _agreement,
    _payment as _agreement_create_payment,
    _settle as _agreement_settle,
)
from test_payment_reversal_contribution_v104 import _db as reversal_db


def _aware_utc(value):
    # SQLite drops tzinfo on persisted timezone-aware columns; the project
    # contract interprets those stored values as UTC.
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _make_settlement_v6(db, payment, settlement):
    financial_date = financial_civil_date(_aware_utc(settlement.confirmed_at))
    db.execute(
        type(settlement).__table__.update()
        .where(type(settlement).id == settlement.id)
        .values(
            receipt_version="v6",
            receipt_number=f"PIX-V6-{payment.id:012d}",
            financial_date=financial_date,
        )
    )
    db.refresh(settlement)
    snapshot = build_settlement_v6_snapshot(db, payment, settlement)
    raw = _canonical_json(snapshot)
    db.execute(
        type(settlement).__table__.update()
        .where(type(settlement).id == settlement.id)
        .values(receipt_snapshot_json=raw, receipt_hash=hashlib.sha256(raw.encode()).hexdigest())
    )
    db.refresh(settlement)
    return financial_date


def _make_reversal_v2(db, payment, settlement, reversal):
    legacy_evidence = json.loads(reversal.receipt_snapshot_json)
    financial_date = financial_civil_date(_aware_utc(reversal.reversed_at))
    db.execute(
        PaymentReversal.__table__.update()
        .where(PaymentReversal.id == reversal.id)
        .values(
            receipt_version="v2",
            receipt_number=f"PIX-REV-V2-{payment.id}",
            financial_date=financial_date,
        )
    )
    db.refresh(reversal)
    snapshot = build_reversal_v2_snapshot(
        db, reversal, payment, settlement, legacy_evidence
    )
    raw = _canonical_json(snapshot)
    db.execute(
        PaymentReversal.__table__.update()
        .where(PaymentReversal.id == reversal.id)
        .values(receipt_snapshot_json=raw, receipt_hash=hashlib.sha256(raw.encode()).hexdigest())
    )
    db.refresh(reversal)
    return financial_date


def test_legacy_event_identity_has_no_fabricated_financial_date():
    db = reversal_db()
    _admin, _contribution, payment, settlement = setup_contribution(db, suffix="f2f1-legacy")
    identity = classify_settlement_event(db, payment, settlement)
    assert identity.semantics == "LEGACY"
    assert identity.financial_date is None
    assert identity.timestamp_utc == settlement.confirmed_at.replace(tzinfo=timezone.utc)
    assert identity.evidence_status == "LEGACY_COMPATIBILITY"
    db.close()


def test_legacy_unsupported_settlement_version_is_not_estimated_by_reader():
    db = reversal_db()
    _admin, _contribution, payment, settlement = setup_contribution(db, suffix="f2f1-unknown")
    settlement.receipt_version = "v99"
    assert payment_financial_events(db) == ()
    with pytest.raises(UnsupportedPaymentEventVersion, match="unknown or unsupported"):
        classify_settlement_event(db, payment, settlement)
    db.close()


@pytest.mark.parametrize(
    ("confirmed_at", "expected_date", "legacy_month"),
    [
        (datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc), date(2026, 9, 30), 10),
        (datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc), date(2026, 10, 1), 10),
        (datetime(2027, 1, 1, 2, 59, 59, tzinfo=timezone.utc), date(2026, 12, 31), 1),
        (datetime(2027, 1, 1, 3, 0, 0, tzinfo=timezone.utc), date(2027, 1, 1), 1),
    ],
)
def test_legacy_and_temporal_period_boundaries_are_intentionally_distinct(
    confirmed_at, expected_date, legacy_month
):
    db = reversal_db()
    _admin, _contribution, payment, settlement = setup_contribution(
        db, suffix=f"f2f1-boundary-{confirmed_at.isoformat()}"
    )
    settlement.confirmed_at = confirmed_at
    db.commit()

    if confirmed_at.year == 2026:
        start = datetime(2026, legacy_month, 1, tzinfo=timezone.utc)
        end = datetime(2026, legacy_month + 1, 1, tzinfo=timezone.utc)
        civil_start = date(2026, legacy_month, 1)
        civil_end = date(2026, legacy_month + 1, 1)
    else:
        start = datetime(2027, legacy_month, 1, tzinfo=timezone.utc)
        end = datetime(2027, legacy_month + 1, 1, tzinfo=timezone.utc)
        civil_start = date(2027, legacy_month, 1)
        civil_end = date(2027, legacy_month + 1, 1)

    legacy_events = payment_financial_events(db, start=start, end=end)
    # These instants both lie in the UTC month selected above, even when the
    # Belem civil date belongs to the preceding month/year.
    assert [event.amount for event in legacy_events] == [Decimal("100.00")]

    # Convert only this test fixture to the authenticated F2-E2 form.
    date_value = _make_settlement_v6(db, payment, settlement)
    assert date_value == expected_date
    temporal_events = payment_financial_events(
        db, start=start, end=end,
        financial_start=civil_start,
        financial_end_exclusive=civil_end,
    )
    assert bool(temporal_events) is (expected_date >= civil_start and expected_date < civil_end)
    if temporal_events:
        assert temporal_events[0].semantics == "TEMPORAL"
        assert temporal_events[0].financial_date == expected_date
    db.close()


def test_mixed_legacy_and_temporal_settlements_share_one_period():
    db = reversal_db()
    admin1, _contribution1, _payment1, legacy = setup_contribution(db, suffix="f2f1-mixed-legacy")
    admin1.is_master = False
    db.flush()
    _admin2, _contribution2, payment2, temporal = setup_contribution(db, suffix="f2f1-mixed-temporal")
    legacy.confirmed_at = datetime(2026, 9, 15, tzinfo=timezone.utc)
    temporal.confirmed_at = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    db.commit()
    _make_settlement_v6(db, payment2, temporal)

    events = payment_financial_events(
        db,
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert [(event.semantics, event.amount) for event in events] == [
        ("LEGACY", Decimal("100.00")),
        ("TEMPORAL", Decimal("100.00")),
    ]
    assert all(event.financial_date is None for event in events if event.semantics == "LEGACY")
    db.close()


def test_legacy_original_and_temporal_reversal_are_independent_period_events():
    db = reversal_db()
    admin, _contribution, payment, settlement = setup_contribution(db, suffix="f2f1-legacy-v2-reversal")
    settlement.confirmed_at = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    db.commit()
    reversal = reverse_payment(
        db, payment_id=payment.id, admin_id=admin.id, reason="F2-F1 temporal reversal",
        now=datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc),
    )
    db.flush()
    _make_reversal_v2(db, payment, settlement, reversal)

    september = payment_financial_events(
        db, start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    october = payment_financial_events(
        db, start=datetime(2026, 10, 1, tzinfo=timezone.utc),
        end=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    assert [(event.source, event.amount) for event in september] == [
        ("ORIGINAL", Decimal("100.00"))
    ]
    assert [(event.source, event.amount) for event in october] == [
        ("REVERSAL", Decimal("-100.00"))
    ]
    assert october[0].semantics == "TEMPORAL"
    assert october[0].financial_date == date(2026, 10, 1)
    db.close()


@pytest.mark.parametrize(
    ("kind", "amount", "interest", "penalty", "expected"),
    [
        ("principal", "100.00", "0.00", "0.00", {("LOAN_PRINCIPAL", Decimal("100.00"))}),
        ("components", "130.00", "20.00", "10.00", {
            ("LOAN_PRINCIPAL", Decimal("100.00")),
            ("LOAN_INTEREST", Decimal("20.00")),
            ("LOAN_PENALTY", Decimal("10.00")),
        }),
    ],
)
def test_temporal_loan_events_include_mfe_principal_and_ledger_components(
    kind, amount, interest, penalty, expected
):
    db = reversal_db()
    _admin, _member, _loan, _installment, payment, settlement = _loan_payment(
        db, f"f2f1-loan-{kind}", amount=amount, interest=interest, penalty=penalty
    )
    settlement.confirmed_at = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    db.commit()
    _make_settlement_v6(db, payment, settlement)
    events = payment_financial_events(
        db,
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert {(event.component, event.amount) for event in events} == expected
    assert all(event.financial_date == date(2026, 9, 30) for event in events)
    db.close()


def test_temporal_agreement_event_is_period_selected_by_financial_date():
    db = reversal_db()
    member, _agreement_row, installments = _agreement(db, principal="25.00")
    payment = _agreement_create_payment(
        db, member, installments[0], amount="25.00", suffix="f2f1-agreement"
    )
    settlement = _agreement_settle(db, payment)
    settlement.confirmed_at = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    db.commit()
    _make_settlement_v6(db, payment, settlement)
    events = payment_financial_events(
        db,
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert [(event.component, event.amount, event.financial_date) for event in events] == [
        ("AGREEMENT", Decimal("25.00"), date(2026, 9, 30))
    ]
    db.close()


def test_temporal_original_and_reversal_in_same_month_are_both_emitted():
    db = reversal_db()
    admin, _contribution, payment, settlement = setup_contribution(db, suffix="f2f1-v2-same-month")
    settlement.confirmed_at = datetime(2026, 10, 5, tzinfo=timezone.utc)
    db.commit()
    reversal = reverse_payment(
        db, payment_id=payment.id, admin_id=admin.id, reason="F2-F1 same-month reversal",
        now=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )
    db.flush()
    _make_settlement_v6(db, payment, settlement)
    _make_reversal_v2(db, payment, settlement, reversal)

    events = payment_financial_events(
        db,
        start=datetime(2026, 10, 1, tzinfo=timezone.utc),
        end=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    assert [(event.source, event.amount) for event in events] == [
        ("ORIGINAL", Decimal("100.00")),
        ("REVERSAL", Decimal("-100.00")),
    ]
    assert all(event.semantics == "TEMPORAL" for event in events)
    db.close()


def test_temporal_original_and_temporal_reversal_cross_month_remain_independent():
    db = reversal_db()
    admin, _contribution, payment, settlement = setup_contribution(db, suffix="f2f1-v2-cross-month")
    settlement.confirmed_at = datetime(2026, 9, 30, 23, 55, tzinfo=timezone.utc)
    db.commit()
    reversal = reverse_payment(
        db, payment_id=payment.id, admin_id=admin.id, reason="F2-F1 temporal cross-month",
        now=datetime(2026, 10, 1, 3, 5, tzinfo=timezone.utc),
    )
    db.flush()
    _make_settlement_v6(db, payment, settlement)
    _make_reversal_v2(db, payment, settlement, reversal)

    september = payment_financial_events(
        db, start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    october = payment_financial_events(
        db, start=datetime(2026, 10, 1, tzinfo=timezone.utc),
        end=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    assert [(event.source, event.amount, event.financial_date) for event in september] == [
        ("ORIGINAL", Decimal("100.00"), date(2026, 9, 30))
    ]
    assert [(event.source, event.amount, event.financial_date) for event in october] == [
        ("REVERSAL", Decimal("-100.00"), date(2026, 10, 1))
    ]
    db.close()


def test_temporal_evidence_tamper_fails_closed_in_event_reader():
    db = reversal_db()
    _admin, _contribution, payment, settlement = setup_contribution(db, suffix="f2f1-tamper")
    settlement.confirmed_at = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    db.commit()
    _make_settlement_v6(db, payment, settlement)
    settlement.receipt_hash = "0" * 64
    db.commit()
    with pytest.raises(PaymentEventEvidenceError, match="evidence invalid"):
        payment_financial_events(
            db,
            start=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
    db.close()


@pytest.mark.parametrize("tamper", ["missing_date", "column_date", "confirmed_at"])
def test_temporal_settlement_identity_mismatch_fails_closed(tamper):
    db = reversal_db()
    _admin, _contribution, payment, settlement = setup_contribution(
        db, suffix=f"f2f1-settlement-{tamper}"
    )
    settlement.confirmed_at = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    db.commit()
    _make_settlement_v6(db, payment, settlement)
    if tamper == "missing_date":
        settlement.financial_date = None
    elif tamper == "column_date":
        settlement.financial_date = date(2026, 10, 1)
    else:
        settlement.confirmed_at = datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(PaymentEventEvidenceError, match="evidence invalid|no financial_date"):
        payment_financial_events(
            db,
            start=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
    db.close()


def test_temporal_reversal_receipt_tamper_fails_closed_in_event_reader():
    db = reversal_db()
    admin, _contribution, payment, settlement = setup_contribution(
        db, suffix="f2f1-reversal-tamper"
    )
    reversal = reverse_payment(
        db, payment_id=payment.id, admin_id=admin.id, reason="tampered temporal reversal",
        now=datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc),
    )
    db.flush()
    _make_reversal_v2(db, payment, settlement, reversal)
    reversal.receipt_hash = "0" * 64
    db.flush()
    with pytest.raises(PaymentEventEvidenceError, match="reversal v2 evidence invalid"):
        payment_financial_events(
            db,
            start=datetime(2026, 10, 1, tzinfo=timezone.utc),
            end=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
    db.close()


def test_temporal_reversal_without_financial_date_fails_closed():
    db = reversal_db()
    admin, _contribution, payment, settlement = setup_contribution(
        db, suffix="f2f1-reversal-no-date"
    )
    reversal = reverse_payment(
        db, payment_id=payment.id, admin_id=admin.id, reason="missing temporal date",
        now=datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc),
    )
    db.flush()
    _make_reversal_v2(db, payment, settlement, reversal)
    reversal.financial_date = None
    with pytest.raises(PaymentEventEvidenceError, match="no financial_date"):
        payment_financial_events(
            db,
            start=datetime(2026, 10, 1, tzinfo=timezone.utc),
            end=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
    db.close()


@pytest.mark.parametrize("evidence", ["mfe", "ledger"])
def test_temporal_settlement_mfe_or_ledger_evidence_tamper_fails_closed(evidence):
    db = reversal_db()
    _admin, _contribution, payment, settlement = setup_contribution(
        db, suffix=f"f2f1-{evidence}-tamper"
    )
    settlement.confirmed_at = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    db.commit()
    _make_settlement_v6(db, payment, settlement)
    if evidence == "mfe":
        entry = db.query(MemberFinancialEntry).filter_by(
            payment_settlement_id=settlement.id
        ).one()
        entry.amount = Decimal("99.00")
    else:
        entry = db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).one()
        entry.entry_hash = "0" * 64
    with pytest.raises(PaymentEventEvidenceError, match="evidence invalid"):
        payment_financial_events(
            db,
            start=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
    db.close()


def test_temporal_date_window_requires_explicit_civil_bounds_for_subday_range():
    db = reversal_db()
    _admin, _contribution, payment, settlement = setup_contribution(db, suffix="f2f1-subday")
    settlement.confirmed_at = datetime(2026, 10, 1, 2, 59, 59, tzinfo=timezone.utc)
    db.commit()
    _make_settlement_v6(db, payment, settlement)
    with pytest.raises(ValueError, match="civil date bounds"):
        payment_financial_events(
            db,
            start=datetime(2026, 10, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 10, 1, 2, tzinfo=timezone.utc),
        )
    db.close()
