from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import event, text

from app.models import LedgerEntry, MemberFinancialEntry, MonthlyClosing
from app.services.ledger import reverse_entry

from app.services.payment_reversal import reverse_payment
from app.services.reports_v042 import monthly_accountability

from test_payment_reversal_contribution_v104 import _db as contribution_db, _setup as setup_contribution
from test_payment_reversal_loan_v105 import _loan_payment
from test_payment_reversal_agreement_v106 import _agreement_payment
from test_payment_reversal_loan_v105 import _admin as loan_admin
from test_payment_settlement_v103 import _installment, _member, _payment, _settle
from test_agreement_payment_settlement_v104 import _agreement, _payment as agreement_payment, _settle as agreement_settle


def _inflows(report):
    return report["inflows"]


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def test_monthly_shape_is_preserved_and_events_replace_current_obligation_state():
    db = contribution_db()
    _admin, _contribution, _payment, _settlement = setup_contribution(db, suffix="h2b2-shape")
    report = monthly_accountability(db, date(2026, 9, 1))
    assert set(report) == {"schema", "report_type", "competence", "period_end", "inflows", "expenses", "operating_result", "ledger", "collections", "loans"}
    assert set(report["inflows"]) == {"contributions_paid", "loan_principal_received", "interest_received", "penalties_received", "agreement_installments_received"}
    assert _inflows(report)["contributions_paid"] == "100.00"


def test_contribution_reversal_stays_in_reversal_month_and_invalid_reversal_does_not_reduce():
    db = contribution_db()
    admin, _contribution, payment, settlement = setup_contribution(db, suffix="h2b2-contribution")
    settlement.confirmed_at = datetime(2027, 1, 10, tzinfo=timezone.utc)
    db.commit()
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B2", now=datetime(2027, 3, 15, tzinfo=timezone.utc))
    db.commit()
    january = monthly_accountability(db, date(2027, 1, 1))
    february = monthly_accountability(db, date(2027, 2, 1))
    march = monthly_accountability(db, date(2027, 3, 1))
    assert _inflows(january)["contributions_paid"] == "100.00"
    assert _inflows(february)["contributions_paid"] == "0.00"
    assert _inflows(march)["contributions_paid"] == "-100.00"
    db.execute(text("UPDATE payment_reversals SET receipt_hash = 'invalid' WHERE id = :id"), {"id": reversal.id})
    db.commit()
    assert _inflows(march)["contributions_paid"] == "-100.00"
    assert _inflows(monthly_accountability(db, date(2027, 3, 1)))["contributions_paid"] == "0.00"


def test_loan_partial_components_and_reversal_are_not_integral_installment_values():
    db = contribution_db()
    admin, _member, _loan, _installment, payment, settlement = _loan_payment(
        db, "h2b2-loan", amount="130.00", interest="20.00", penalty="10.00"
    )
    when = settlement.confirmed_at
    report = monthly_accountability(db, when.date())
    assert _inflows(report)["loan_principal_received"] == "100.00"
    assert _inflows(report)["interest_received"] == "20.00"
    assert _inflows(report)["penalties_received"] == "10.00"
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B2", now=_aware(when))
    db.commit()
    reversed_report = monthly_accountability(db, when.date())
    assert _inflows(reversed_report)["loan_principal_received"] == "0.00"
    assert _inflows(reversed_report)["interest_received"] == "0.00"
    assert _inflows(reversed_report)["penalties_received"] == "0.00"


def test_agreement_partial_and_reversal_are_event_based_and_not_operating_revenue():
    db = contribution_db()
    admin, _agreement, _installment, payment, settlement = _agreement_payment(
        db, "h2b2-agreement", received="25.00"
    )
    report = monthly_accountability(db, settlement.confirmed_at.date())
    assert _inflows(report)["agreement_installments_received"] == "25.00"
    assert Decimal(report["operating_result"]) == Decimal("0.00")
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B2", now=_aware(settlement.confirmed_at))
    db.commit()
    reversed_report = monthly_accountability(db, settlement.confirmed_at.date())
    assert _inflows(reversed_report)["agreement_installments_received"] == "0.00"
    assert Decimal(reversed_report["operating_result"]) == Decimal("0.00")


def test_ledger_section_is_preserved_without_double_counting_event_metrics():
    db = contribution_db()
    admin, _contribution, payment, settlement = setup_contribution(db, suffix="h2b2-ledger")
    before = monthly_accountability(db, settlement.confirmed_at.date())
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno H2B2", now=_aware(settlement.confirmed_at))
    db.commit()
    after = monthly_accountability(db, settlement.confirmed_at.date())
    assert Decimal(before["ledger"]["credits"]) == Decimal("100.00")
    assert Decimal(after["ledger"]["credits"]) == Decimal("100.00")
    assert Decimal(after["ledger"]["debits"]) == Decimal("100.00")
    assert _inflows(after)["contributions_paid"] == "0.00"


def test_monthly_accountability_does_not_mutate_closing_or_flush_pending_state():
    db = contribution_db()
    _admin, contribution, _payment, _settlement = setup_contribution(db, suffix="h2b2-readonly")
    flushes = []

    @event.listens_for(db, "before_flush")
    def before_flush(*_args):
        flushes.append(True)

    pending = contribution.__class__(
        member_id=contribution.member_id,
        competence=date(2027, 1, 1),
        amount=Decimal("1.00"),
        status="PENDING",
    )
    db.add(pending)
    monthly_accountability(db, date(2026, 9, 1))
    assert flushes == []
    assert pending.id is None
    db.rollback()


def test_integrated_months_preserve_originals_and_post_valid_reversals_in_march():
    db = contribution_db()
    contribution_admin, _contribution, contribution_payment, contribution_settlement = setup_contribution(
        db, suffix="h2b2-integrated-contribution"
    )
    contribution_settlement.confirmed_at = datetime(2027, 1, 10, tzinfo=timezone.utc)

    loan_member = _member(db, "h2b2-integrated-loan")
    _loan, loan_installment = _installment(db, loan_member, amount="120.00", interest="20.00", penalty="0.00")
    loan_payment = _payment(db, suffix="h2b2-integrated-loan", amount="60.00", reference_type="LOAN_INSTALLMENT", reference_id=str(loan_installment.id))
    loan_settlement = _settle(db, loan_payment, when=datetime(2027, 1, 11, tzinfo=timezone.utc))

    _agreement_member, _agreement_row, agreement_installments = _agreement(db, principal="100.00", status="OPEN")
    agreement_payment_row = agreement_payment(
        db, _agreement_member, agreement_installments[0], amount="30.00", suffix="h2b2-integrated-agreement"
    )
    agreement_payment_row.confirmed_at = datetime(2027, 1, 12, tzinfo=timezone.utc)
    agreement_settlement = agreement_settle(db, agreement_payment_row)
    db.commit()

    reverse_payment(db, payment_id=contribution_payment.id, admin_id=contribution_admin.id, reason="Estorno integrado", now=datetime(2027, 3, 15, tzinfo=timezone.utc))
    reverse_payment(db, payment_id=loan_payment.id, admin_id=contribution_admin.id, reason="Estorno integrado", now=datetime(2027, 3, 16, tzinfo=timezone.utc))
    reverse_payment(db, payment_id=agreement_payment_row.id, admin_id=contribution_admin.id, reason="Estorno integrado", now=datetime(2027, 3, 17, tzinfo=timezone.utc))
    db.commit()

    january = monthly_accountability(db, date(2027, 1, 1))
    february = monthly_accountability(db, date(2027, 2, 1))
    march = monthly_accountability(db, date(2027, 3, 1))

    assert january["inflows"] == {
        "contributions_paid": "100.00",
        "loan_principal_received": "40.00",
        "interest_received": "20.00",
        "penalties_received": "0.00",
        "agreement_installments_received": "30.00",
    }
    assert february["inflows"] == {
        "contributions_paid": "0.00",
        "loan_principal_received": "0.00",
        "interest_received": "0.00",
        "penalties_received": "0.00",
        "agreement_installments_received": "0.00",
    }
    assert march["inflows"] == {
        "contributions_paid": "-100.00",
        "loan_principal_received": "-40.00",
        "interest_received": "-20.00",
        "penalties_received": "0.00",
        "agreement_installments_received": "-30.00",
    }
    assert january["operating_result"] == "120.00"
    assert march["operating_result"] == "-120.00"


def test_same_month_reversal_nets_contribution_and_composed_loan_without_double_counting():
    db = contribution_db()
    contribution_admin, _contribution, contribution_payment, contribution_settlement = setup_contribution(
        db, suffix="h2b2-same-month-contribution"
    )
    loan_member = _member(db, "h2b2-same-month-loan")
    _loan, loan_installment = _installment(db, loan_member, amount="130.00", interest="20.00", penalty="10.00")
    loan_payment = _payment(db, suffix="h2b2-same-month-loan", amount="130.00", reference_type="LOAN_INSTALLMENT", reference_id=str(loan_installment.id))
    loan_settlement = _settle(db, loan_payment, when=datetime(2027, 4, 20, tzinfo=timezone.utc))
    when = datetime(2027, 4, 20, tzinfo=timezone.utc)
    contribution_settlement.confirmed_at = when
    db.commit()
    reverse_payment(db, payment_id=contribution_payment.id, admin_id=contribution_admin.id, reason="Estorno mesmo mês", now=when)
    reverse_payment(db, payment_id=loan_payment.id, admin_id=contribution_admin.id, reason="Estorno mesmo mês", now=when)
    db.commit()
    report = monthly_accountability(db, date(2027, 4, 1))
    assert report["inflows"] == {
        "contributions_paid": "0.00",
        "loan_principal_received": "0.00",
        "interest_received": "0.00",
        "penalties_received": "0.00",
        "agreement_installments_received": "0.00",
    }
    assert report["operating_result"] == "0.00"


def test_multiple_loan_payments_are_independent_and_only_one_reversal_is_netted():
    db = contribution_db()
    member = _member(db, "h2b2-multi-loan")
    _loan, installment = _installment(db, member, amount="120.00", interest="20.00", penalty="0.00")
    payment_one = _payment(db, suffix="h2b2-multi-loan-one", amount="60.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement_one = _settle(db, payment_one, when=datetime(2027, 5, 1, tzinfo=timezone.utc))
    payment_two = _payment(db, suffix="h2b2-multi-loan-two", amount="30.00", reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id))
    settlement_two = _settle(db, payment_two, when=datetime(2027, 5, 2, tzinfo=timezone.utc))
    assert settlement_one.principal_applied == Decimal("40.00")
    assert settlement_two.principal_applied == Decimal("30.00")
    admin = loan_admin(db, "h2b2-multi-loan-admin")
    db.commit()
    reverse_payment(db, payment_id=payment_two.id, admin_id=admin.id, reason="Estorno parcial", now=datetime(2027, 5, 3, tzinfo=timezone.utc))
    db.commit()
    report = monthly_accountability(db, date(2027, 5, 1))
    assert report["inflows"]["loan_principal_received"] == "40.00"
    assert report["inflows"]["interest_received"] == "20.00"
    assert report["inflows"]["penalties_received"] == "0.00"
    assert installment.paid_amount == Decimal("60.00")


def test_multiple_agreement_payments_are_independent_and_only_one_reversal_is_netted():
    db = contribution_db()
    _member_row, agreement, installments = _agreement(db, principal="100.00", status="OPEN")
    first = agreement_payment(db, _member_row, installments[0], amount="25.00", suffix="h2b2-multi-agreement-one")
    first.confirmed_at = datetime(2027, 6, 1, tzinfo=timezone.utc)
    first_settlement = agreement_settle(db, first)
    second = agreement_payment(db, _member_row, installments[0], amount="30.00", suffix="h2b2-multi-agreement-two")
    second.confirmed_at = datetime(2027, 6, 2, tzinfo=timezone.utc)
    second_settlement = agreement_settle(db, second)
    admin = loan_admin(db, "h2b2-multi-agreement-admin")
    db.commit()
    reverse_payment(db, payment_id=second.id, admin_id=admin.id, reason="Estorno parcial", now=datetime(2027, 6, 3, tzinfo=timezone.utc))
    db.commit()
    report = monthly_accountability(db, date(2027, 6, 1))
    assert report["inflows"]["agreement_installments_received"] == "25.00"
    assert db.query(LedgerEntry).filter(LedgerEntry.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT").count() == 2


def test_penalty_only_and_zero_components_are_reported_without_fictitious_events():
    db = contribution_db()
    admin, _member_row, _loan, _installment_row, payment, settlement = _loan_payment(
        db, "h2b2-penalty-only", amount="10.00", interest="0.00", penalty="10.00"
    )
    report = monthly_accountability(db, settlement.confirmed_at.date())
    assert report["inflows"]["loan_principal_received"] == "0.00"
    assert report["inflows"]["interest_received"] == "0.00"
    assert report["inflows"]["penalties_received"] == "10.00"
    reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Estorno multa", now=_aware(settlement.confirmed_at))
    db.commit()
    reversed_report = monthly_accountability(db, settlement.confirmed_at.date())
    assert reversed_report["inflows"]["penalties_received"] == "0.00"
    assert reversed_report["inflows"]["loan_principal_received"] == "0.00"
    assert reversed_report["inflows"]["interest_received"] == "0.00"


def test_generic_ledger_reversal_does_not_reduce_event_inflows_or_become_expense():
    db = contribution_db()
    _admin, _contribution, payment, settlement = setup_contribution(db, suffix="h2b2-generic")
    original = db.query(LedgerEntry).filter_by(reference_type="CONTRIBUTION_PAYMENT", reference_id=str(payment.id)).one()
    reverse_entry(db, original, "Ajuste manual H2B2")
    db.commit()
    report = monthly_accountability(db, settlement.confirmed_at.date())
    assert report["inflows"]["contributions_paid"] == "100.00"
    assert report["expenses"] == "0.00"
    assert report["ledger"]["debits"] == "100.00"


def test_month_boundaries_are_inclusive_at_last_microsecond_and_exclusive_at_next_month():
    db = contribution_db()
    _admin, _contribution, _payment, settlement = setup_contribution(db, suffix="h2b2-boundary")
    settlement.confirmed_at = datetime(2027, 3, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    db.commit()
    march = monthly_accountability(db, date(2027, 3, 1))
    april = monthly_accountability(db, date(2027, 4, 1))
    assert march["inflows"]["contributions_paid"] == "100.00"
    assert april["inflows"]["contributions_paid"] == "0.00"


def test_monthly_report_preserves_financial_rows_and_closed_closing():
    db = contribution_db()
    admin, _contribution, payment, settlement = setup_contribution(db, suffix="h2b2-immutable")
    closing = MonthlyClosing(competence=settlement.confirmed_at.date(), status="CLOSED", total_contributions=Decimal("7.00"))
    db.add(closing)
    db.commit()
    payment_before = (payment.status, payment.amount, payment.reference_id)
    settlement_before = (settlement.receipt_snapshot_json, settlement.receipt_hash, settlement.amount_applied)
    ledger_before = [(row.id, row.amount, row.direction, row.entry_hash) for row in db.query(LedgerEntry).all()]
    mfe_before = [(row.id, row.amount, row.direction, row.entry_type) for row in db.query(MemberFinancialEntry).all()]
    monthly_accountability(db, settlement.confirmed_at.date())
    assert (payment.status, payment.amount, payment.reference_id) == payment_before
    assert (settlement.receipt_snapshot_json, settlement.receipt_hash, settlement.amount_applied) == settlement_before
    assert [(row.id, row.amount, row.direction, row.entry_hash) for row in db.query(LedgerEntry).all()] == ledger_before
    assert [(row.id, row.amount, row.direction, row.entry_type) for row in db.query(MemberFinancialEntry).all()] == mfe_before
    assert db.get(MonthlyClosing, closing.id).status == "CLOSED"
