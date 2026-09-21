from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import LedgerEntry, Loan, LoanInstallment, MemberFinancialEntry
from app.services import loan_amortization
from app.services.member_financial import get_member_financial_position
from app.services.payment_reversal import reverse_payment
from app.services.payment_settlement import settle_confirmed_pix_payment
from test_payment_reversal_loan_v105 import _admin
from test_payment_settlement_v103 import _db, _member, _payment


PRICE_ROWS = (
    ("15.11", "30.00", "45.11"),
    ("18.13", "26.98", "45.11"),
    ("21.76", "23.35", "45.11"),
    ("26.11", "19.00", "45.11"),
    ("31.33", "13.78", "45.11"),
    ("37.56", "7.51", "45.07"),
)


def _price_loan(db, suffix="price", *, version="price_amortization_v1"):
    member = _member(db, suffix)
    loan = Loan(
        member_id=member.id,
        principal=Decimal("150.00"),
        monthly_rate=Decimal("0.20"),
        installments=6,
        calculation_version=version,
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    for number, (principal, interest, amount) in enumerate(PRICE_ROWS, start=1):
        db.add(
            LoanInstallment(
                loan_id=loan.id,
                number=number,
                due_date=date.today() + timedelta(days=30 * number),
                principal=Decimal(principal),
                interest=Decimal(interest),
                amount=Decimal(amount),
                paid_amount=Decimal("0.00"),
                penalty_amount=Decimal("0.00"),
                paid_penalty_amount=Decimal("0.00"),
                status="OPEN",
            )
        )
    db.flush()
    return member, loan, db.query(LoanInstallment).filter_by(loan_id=loan.id).order_by(LoanInstallment.number).all()


def _settle(db, payment):
    settlement = settle_confirmed_pix_payment(
        db,
        payment,
        confirmation_source="WEBHOOK",
        confirmed_at=datetime.now(timezone.utc),
    )
    db.commit()
    return settlement


def test_price_payment_uses_persisted_first_installment_components_and_is_idempotent():
    db = _db()
    member, loan, installments = _price_loan(db, "first")
    installment = installments[0]
    before_contract = (installment.principal, installment.interest, installment.amount)
    payment = _payment(
        db,
        suffix="price-first",
        amount="45.11",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )

    settlement = _settle(db, payment)

    assert (installment.principal, installment.interest, installment.amount) == before_contract
    assert installment.paid_amount == Decimal("45.11")
    assert installment.status == "PAID"
    assert settlement.principal_applied == Decimal("15.11")
    assert settlement.interest_applied == Decimal("30.00")
    assert settlement.amount_applied == Decimal("45.11")
    assert [entry.reference_type for entry in db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).all()] == [
        "LOAN_INTEREST_PAYMENT",
    ]
    assert db.query(MemberFinancialEntry).filter_by(reference_id=str(payment.id)).one().amount == Decimal("15.11")

    repeated = _settle(db, payment)
    assert repeated.id == settlement.id
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == 1
    assert db.query(MemberFinancialEntry).filter_by(reference_id=str(payment.id)).count() == 1
    assert loan.calculation_version == "price_amortization_v1"
    db.close()


def test_price_payment_uses_persisted_final_installment_components():
    db = _db()
    _, _, installments = _price_loan(db, "final")
    installment = installments[-1]
    payment = _payment(
        db,
        suffix="price-final",
        amount="45.07",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )

    settlement = _settle(db, payment)

    assert installment.principal == Decimal("37.56")
    assert installment.interest == Decimal("7.51")
    assert installment.amount == Decimal("45.07")
    assert installment.paid_amount == Decimal("45.07")
    assert settlement.principal_applied + settlement.interest_applied == Decimal("45.07")
    assert settlement.principal_applied == Decimal("37.56")
    assert settlement.interest_applied == Decimal("7.51")
    db.close()


def test_price_partial_and_completion_preserve_persisted_schedule():
    db = _db()
    _, _, installments = _price_loan(db, "partial")
    installment = installments[0]
    payment_a = _payment(
        db,
        suffix="price-partial-a",
        amount="10.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    first = _settle(db, payment_a)
    assert first.interest_applied == Decimal("10.00")
    assert first.principal_applied == Decimal("0.00")
    assert installment.paid_amount == Decimal("10.00")
    assert installment.status == "PARTIAL"

    payment_b = _payment(
        db,
        suffix="price-partial-b",
        amount="35.11",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    second = _settle(db, payment_b)
    assert second.interest_applied == Decimal("20.00")
    assert second.principal_applied == Decimal("15.11")
    assert installment.paid_amount == Decimal("45.11")
    assert installment.status == "PAID"
    assert (installment.principal, installment.interest, installment.amount) == (
        Decimal("15.11"), Decimal("30.00"), Decimal("45.11")
    )
    assert db.query(MemberFinancialEntry).filter_by(reference_id=str(payment_a.id)).count() == 0
    assert db.query(MemberFinancialEntry).filter_by(reference_id=str(payment_b.id)).one().amount == Decimal("15.11")
    db.close()


def test_price_reversal_restores_persisted_state_without_recalculation():
    db = _db()
    member, loan, installments = _price_loan(db, "reversal")
    installment = installments[0]
    payment = _payment(
        db,
        suffix="price-reversal",
        amount="45.11",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    settlement = _settle(db, payment)
    admin = _admin(db, "price-reversal")
    db.commit()
    contract = (installment.principal, installment.interest, installment.amount)
    original_ledger = db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count()
    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Reversão Price válida")
    db.commit()

    assert reversal.id
    assert installment.status == "OPEN"
    assert installment.paid_amount == Decimal("0.00")
    assert (installment.principal, installment.interest, installment.amount) == contract
    assert loan.calculation_version == "price_amortization_v1"
    assert db.query(MemberFinancialEntry).filter_by(payment_reversal_id=reversal.id).one().amount == Decimal("15.11")
    assert db.query(LedgerEntry).filter_by(reference_id=str(payment.id)).count() == original_ledger + 1
    assert db.query(LedgerEntry).filter(LedgerEntry.reversal_of_id.is_not(None)).count() == 1
    assert get_member_financial_position(db, member)["own_balance"] == Decimal("0.00")
    repeated = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Outra reversão")
    assert repeated.id == reversal.id
    assert db.query(MemberFinancialEntry).filter_by(payment_reversal_id=reversal.id).count() == 1
    db.close()


def test_partial_price_payment_reversal_restores_persisted_state_without_new_mfe():
    db = _db()
    _, _, installments = _price_loan(db, "partial-reversal")
    installment = installments[0]
    payment = _payment(
        db,
        suffix="price-partial-reversal",
        amount="10.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )
    settlement = _settle(db, payment)
    admin = _admin(db, "partial-reversal")
    db.commit()

    reversal = reverse_payment(db, payment_id=payment.id, admin_id=admin.id, reason="Reversão parcial válida")
    db.commit()

    assert settlement.principal_applied == Decimal("0.00")
    assert settlement.interest_applied == Decimal("10.00")
    assert installment.status == "OPEN"
    assert installment.paid_amount == Decimal("0.00")
    assert (installment.principal, installment.interest, installment.amount) == (
        Decimal("15.11"), Decimal("30.00"), Decimal("45.11")
    )
    assert db.query(MemberFinancialEntry).filter_by(payment_reversal_id=reversal.id).count() == 0
    db.close()


@pytest.mark.parametrize("version", ["linear_amortization_v1", None])
def test_persisted_installment_payment_does_not_call_any_amortization_engine(monkeypatch, version):
    db = _db()
    member = _member(db, f"persisted-{version or 'null'}")
    loan = Loan(
        member_id=member.id,
        principal=Decimal("150.00"),
        monthly_rate=Decimal("0.20"),
        installments=1,
        calculation_version=version,
        status="ACTIVE",
    )
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id,
        number=1,
        due_date=date.today(),
        principal=Decimal("25.00"),
        interest=Decimal("30.00"),
        amount=Decimal("55.00"),
        paid_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"),
        paid_penalty_amount=Decimal("0.00"),
        status="OPEN",
    )
    db.add(installment)
    db.flush()
    payment = _payment(
        db,
        suffix=f"persisted-{version or 'null'}",
        amount="55.00",
        reference_type="LOAN_INSTALLMENT",
        reference_id=str(installment.id),
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("amortization engine must not run for persisted installment payment")

    monkeypatch.setattr(loan_amortization, "calculate_amortization", fail_if_called)
    monkeypatch.setattr(loan_amortization, "calculate_price_amortization", fail_if_called)
    monkeypatch.setattr(loan_amortization, "calculate_linear_amortization", fail_if_called)

    settlement = _settle(db, payment)
    assert settlement.principal_applied == Decimal("25.00")
    assert settlement.interest_applied == Decimal("30.00")
    assert installment.status == "PAID"
    assert installment.amount == Decimal("55.00")
    db.close()
