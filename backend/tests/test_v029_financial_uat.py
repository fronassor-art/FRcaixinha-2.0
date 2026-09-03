from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.loan_engine_v17 import add_months, apply_payment, installment_due


def make_inst(amount='100.00', penalty='0.00'):
    return SimpleNamespace(
        amount=Decimal(amount), paid_amount=Decimal('0.00'),
        penalty_amount=Decimal(penalty), paid_penalty_amount=Decimal('0.00'),
        due_date=date(2026, 1, 10), status='OPEN', last_penalty_date=None,
    )


def test_uat_loan_schedule_is_monthly_and_exact():
    base = date(2026, 1, 31)
    dates = [add_months(base, n) for n in range(1, 4)]
    assert dates == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_uat_full_payment_closes_installment_and_no_balance_remains():
    inst = make_inst('120.00')
    result = apply_payment(inst, Decimal('120.00'))
    assert result['applied'] == Decimal('120.00')
    assert inst.status == 'PAID'
    assert installment_due(inst) == Decimal('0.00')


def test_uat_overpayment_is_not_created_as_ledger_value():
    inst = make_inst('100.00')
    result = apply_payment(inst, Decimal('130.00'))
    assert result['applied'] == Decimal('100.00')
    assert result['excess'] == Decimal('30.00')
    assert installment_due(inst) == Decimal('0.00')


def test_uat_penalty_is_explicitly_separated_from_contractual_amount():
    inst = make_inst('100.00', '8.00')
    result = apply_payment(inst, Decimal('8.00'))
    assert result['penalty_applied'] == Decimal('8.00')
    assert result['base_applied'] == Decimal('0.00')
    assert inst.paid_amount == Decimal('0.00')
    assert inst.paid_penalty_amount == Decimal('8.00')
    assert installment_due(inst) == Decimal('100.00')
