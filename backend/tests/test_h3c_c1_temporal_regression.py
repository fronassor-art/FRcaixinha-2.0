from datetime import date, datetime, timezone
from decimal import Decimal

from app.models import LedgerEntry, PaymentReversalComponent
from app.services.payment_financial_events import payment_financial_events
from app.services.payment_reversal import reverse_payment
from app.services.reconciliation_v040 import build_advanced_reconciliation
from app.services.reports_v042 import annual_accountability, monthly_accountability
from app.services.reports_v10 import monthly_report

from test_payment_reversal_contribution_v104 import _db, _setup
from test_payment_reversal_loan_v105 import _loan_payment


UTC = timezone.utc


def _at(year, month, day, hour=0, minute=0, second=0, microsecond=0):
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)


def _put_settlement_in_period(db, settlement, payment, when):
    settlement.confirmed_at = when
    payment.confirmed_at = when
    payment.created_at = when
    payment.ledger_posted_at = when
    db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment.id)).update(
        {LedgerEntry.created_at: when}, synchronize_session=False
    )


def _put_reversal_in_period(db, reversal, when):
    compensating_ids = [row.compensating_ledger_entry_id for row in db.query(PaymentReversalComponent).filter_by(
        payment_reversal_id=reversal.id
    ).all()]
    db.query(LedgerEntry).filter(LedgerEntry.id.in_(compensating_ids)).update(
        {LedgerEntry.created_at: when}, synchronize_session=False
    )


def test_h3c_c1_event_engine_explicit_utc_year_boundaries():
    cases = (
        (_at(2026, 11, 30, 23, 59, 59, 999999), date(2026, 11, 1), date(2026, 12, 1)),
        (_at(2026, 12, 1), date(2026, 12, 1), date(2027, 1, 1)),
        (_at(2026, 12, 31, 23, 59, 59, 999999), date(2026, 12, 1), date(2027, 1, 1)),
        (_at(2027, 1, 1), date(2027, 1, 1), date(2027, 2, 1)),
    )
    for index, (occurred_at, competence, next_competence) in enumerate(cases):
        db = _db()
        _admin, _contribution, _payment, settlement = _setup(db, suffix=f"h3c1-event-{index}")
        settlement.confirmed_at = occurred_at
        db.commit()

        included = payment_financial_events(
            db,
            start=datetime.combine(competence, datetime.min.time(), tzinfo=UTC),
            end=datetime.combine(next_competence, datetime.min.time(), tzinfo=UTC),
        )
        assert [event.amount for event in included] == [Decimal("100.00")]

        excluded = payment_financial_events(
            db,
            start=datetime.combine(next_competence, datetime.min.time(), tzinfo=UTC),
            end=datetime.combine(
                date(next_competence.year + (next_competence.month == 12), next_competence.month % 12 + 1, 1),
                datetime.min.time(),
                tzinfo=UTC,
            ),
        )
        assert excluded == ()


def test_h3c_c1_modern_cross_year_event_accountability_and_annual_totals():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h3c1-modern-cross-year")
    original_at = _at(2026, 12, 31, 23, 59, 59, 999999)
    reversal_at = _at(2027, 1, 1)
    _put_settlement_in_period(db, settlement, payment, original_at)
    db.commit()

    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Estorno H3C1 cross-year",
        now=reversal_at,
    )
    db.commit()

    december_events = payment_financial_events(
        db, start=_at(2026, 12, 1), end=_at(2027, 1, 1)
    )
    january_events = payment_financial_events(
        db, start=_at(2027, 1, 1), end=_at(2027, 2, 1)
    )
    assert [event.amount for event in december_events] == [Decimal("100.00")]
    assert [event.amount for event in january_events] == [Decimal("-100.00")]
    assert reversal.reversal_competence == date(2027, 1, 1)

    december = monthly_accountability(db, date(2026, 12, 1))
    january = monthly_accountability(db, date(2027, 1, 1))
    assert december["inflows"]["contributions_paid"] == "100.00"
    assert january["inflows"]["contributions_paid"] == "-100.00"

    annual_2026 = annual_accountability(db, 2026)
    annual_2027 = annual_accountability(db, 2027)
    assert annual_2026["months"][11]["inflows"]["contributions_paid"] == "100.00"
    assert annual_2026["totals"]["contributions_paid"] == "100.00"
    assert annual_2027["months"][0]["inflows"]["contributions_paid"] == "-100.00"
    assert annual_2027["totals"]["contributions_paid"] == "-100.00"


def test_h3c_c1_reconciliation_keeps_december_and_january_upper_bound_exclusive():
    db = _db()
    _admin, _contribution, _payment, settlement = _setup(db, suffix="h3c1-recon-december-end")
    settlement.confirmed_at = _at(2026, 12, 31, 23, 59, 59, 999999)
    db.commit()
    assert build_advanced_reconciliation(db, date(2026, 12, 1))["snapshot"]["contributions_paid"] == "100.00"
    assert build_advanced_reconciliation(db, date(2027, 1, 1))["snapshot"]["contributions_paid"] == "0.00"

    db2 = _db()
    _admin, _contribution, _payment, settlement = _setup(db2, suffix="h3c1-recon-january-start")
    settlement.confirmed_at = _at(2027, 1, 1)
    db2.commit()
    assert build_advanced_reconciliation(db2, date(2026, 12, 1))["snapshot"]["contributions_paid"] == "0.00"
    assert build_advanced_reconciliation(db2, date(2027, 1, 1))["snapshot"]["contributions_paid"] == "100.00"


def test_h3c_c1_reports_v10_cross_year_reversal_is_correctly_reflected():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h3c1-v10-red")
    original_at = _at(2026, 12, 31, 23, 59, 59, 999999)
    _put_settlement_in_period(db, settlement, payment, original_at)
    db.commit()

    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Estorno v10 cross-year",
        now=_at(2027, 1, 1),
    )
    _put_reversal_in_period(db, reversal, _at(2027, 1, 1))
    db.commit()

    december = monthly_report(db, date(2026, 12, 1))
    january = monthly_report(db, date(2027, 1, 1))
    assert december["contributions_paid"] == "100.00"
    assert december["operating_result"] == "100.00"
    assert january["contributions_paid"] == "-100.00"
    assert january["operating_result"] == "-100.00"
    assert january["ledger_debits_in_period"] == "100.00"


def test_h3c_c1_reports_v10_preserves_shape_and_rejects_invalid_reversal_effect():
    db = _db()
    admin, _contribution, payment, settlement = _setup(db, suffix="h3c1-v10-invalid")
    when = _at(2027, 2, 10)
    _put_settlement_in_period(db, settlement, payment, when)
    db.commit()

    reversal = reverse_payment(
        db,
        payment_id=payment.id,
        admin_id=admin.id,
        reason="Estorno v10 inválido",
        now=_at(2027, 2, 11),
    )
    _put_reversal_in_period(db, reversal, _at(2027, 2, 11))
    db.commit()
    reversal.receipt_hash = "invalid"
    db.commit()

    report = monthly_report(db, date(2027, 2, 1))
    assert set(report) == {
        "competence", "period_end", "contributions_paid", "expenses",
        "interest_received", "penalties_received", "operating_result",
        "ledger_credits_in_period", "ledger_debits_in_period",
    }
    assert report["contributions_paid"] == "100.00"
    assert report["operating_result"] == "100.00"
    assert report["ledger_debits_in_period"] == "100.00"


def test_h3c_c1_reports_v10_includes_loan_interest_and_penalty_but_not_principal():
    db = _db()
    _admin, _member, _loan, _installment, _payment, settlement = _loan_payment(
        db, "h3c1-v10-loan", amount="130.00", interest="20.00", penalty="10.00"
    )

    report = monthly_report(db, settlement.confirmed_at.date())
    assert report["contributions_paid"] == "0.00"
    assert report["interest_received"] == "20.00"
    assert report["penalties_received"] == "10.00"
    assert report["operating_result"] == "30.00"
