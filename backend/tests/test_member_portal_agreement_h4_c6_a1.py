from datetime import date
from decimal import Decimal

from app.models import LoanInstallment
from app.services.member_portal_v043 import portal_dashboard

from test_h4_c1_exposure_contract import (
    add_original_agreed_installment,
    make_agreement,
    make_db,
)


def add_loan_installment(db, loan, *, status="OPEN", due_date=date(2026, 1, 1)):
    row = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=due_date,
        principal=Decimal("100.00"),
        interest=Decimal("0.00"),
        amount=Decimal("100.00"),
        paid_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def summary_for(*, agreement_status="APPROVED", installment_status="OPEN"):
    db = make_db()
    _group, user, member, loan, agreement, installment = make_agreement(
        db,
        status=agreement_status,
        installment_status=installment_status,
    )
    db.commit()
    return db, user, member, loan, agreement, installment


def test_agreed_loan_installment_is_excluded_from_portal_loan_outstanding():
    db, user, _member, loan, _agreement, _installment = summary_for()
    add_original_agreed_installment(db, loan)
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["loan_outstanding"] == "0.00"


def test_agreed_loan_installment_is_excluded_from_portal_loan_overdue():
    db, user, _member, loan, _agreement, _installment = summary_for()
    add_original_agreed_installment(db, loan)
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["overdue_balance"] == "0.00"
    assert summary["overdue_installments"] == 0


def test_open_loan_installment_preserves_existing_portal_balance():
    db, user, _member, loan, _agreement, _installment = summary_for()
    add_loan_installment(db, loan)
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["loan_outstanding"] == "100.00"


def test_approved_agreement_exposes_remaining_principal_and_penalty_separately():
    db, user, _member, _loan, _agreement, _installment = summary_for()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["agreement_outstanding"] == "67.00"


def test_agreement_outstanding_clamps_each_component():
    db, user, _member, _loan, _agreement, installment = summary_for()
    installment.paid_amount = Decimal("110.00")
    installment.paid_penalty_amount = Decimal("12.00")
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["agreement_outstanding"] == "0.00"


def test_agreement_outstanding_does_not_cross_compensate_components():
    db, user, _member, _loan, _agreement, installment = summary_for()
    installment.paid_amount = Decimal("110.00")
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["agreement_outstanding"] == "7.00"


def test_only_approved_agreements_enter_operational_outstanding():
    for status in ("SETTLED", "REQUESTED", "REJECTED"):
        db, user, _member, _loan, _agreement, _installment = summary_for(
            agreement_status=status,
            installment_status="PAID" if status == "SETTLED" else "OPEN",
        )

        summary = portal_dashboard(db, user.id)["summary"]

        assert summary["agreement_outstanding"] == "0.00"


def test_approved_agreement_overdue_balance_uses_open_past_due_installments():
    db, user, _member, _loan, _agreement, _installment = summary_for()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["agreement_overdue_balance"] == "67.00"
    assert summary["agreement_overdue_installments"] == 1


def test_approved_agreement_future_installment_is_not_overdue():
    db, user, _member, _loan, _agreement, installment = summary_for()
    installment.due_date = date(2099, 1, 1)
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["agreement_overdue_balance"] == "0.00"
    assert summary["agreement_overdue_installments"] == 0


def test_paid_past_due_agreement_installment_is_not_overdue():
    db, user, _member, _loan, _agreement, installment = summary_for(
        installment_status="PAID",
    )
    installment.paid_amount = Decimal("100.00")
    installment.paid_penalty_amount = Decimal("10.00")
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["agreement_overdue_balance"] == "0.00"
    assert summary["agreement_overdue_installments"] == 0


def test_restructured_loan_and_agreement_are_not_double_counted_in_portal():
    db, user, _member, loan, _agreement, _installment = summary_for()
    add_original_agreed_installment(db, loan)
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["loan_outstanding"] == "0.00"
    assert summary["agreement_outstanding"] == "67.00"


def test_agreement_payments_are_separate_from_loan_payment_history():
    db, user, _member, loan, _agreement, installment = summary_for()
    loan_installment = add_loan_installment(db, loan, status="PAID")
    loan_installment.paid_amount = Decimal("25.00")
    installment.paid_amount = Decimal("40.00")
    installment.paid_penalty_amount = Decimal("3.00")
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["loan_payments"] == "25.00"
    assert summary["agreement_payments"] == "43.00"


def test_settled_agreement_payments_remain_historical_without_operational_balance():
    db, user, _member, _loan, _agreement, installment = summary_for(
        agreement_status="SETTLED",
        installment_status="PAID",
    )
    installment.paid_amount = Decimal("100.00")
    installment.paid_penalty_amount = Decimal("10.00")
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["agreement_payments"] == "110.00"
    assert summary["agreement_outstanding"] == "0.00"


def test_agreement_does_not_change_loan_only_on_time_ratio():
    db, user, _member, loan, _agreement, _installment = summary_for()
    loan_installment = add_loan_installment(db, loan, status="PAID")
    loan_installment.paid_amount = Decimal("100.00")
    loan_installment.paid_at = loan_installment.due_date
    db.commit()

    summary = portal_dashboard(db, user.id)["summary"]

    assert summary["on_time_ratio"] == 1.0
