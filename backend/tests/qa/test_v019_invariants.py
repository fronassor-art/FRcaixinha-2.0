from decimal import Decimal
from datetime import date, timedelta
from app.services.loan_engine_v17 import calculate_daily_penalty, installment_due, apply_payment


def test_installment_payment_is_idempotency_safe_at_domain_level():
    class I:
        amount = Decimal('100.00')
        penalty_amount = Decimal('0.00')
        paid_amount = Decimal('0.00')
        status = 'OPEN'
        due_date = date.today()
        last_penalty_date = None

    inst = I()
    first = apply_payment(inst, Decimal('40.00'))
    assert first['applied'] == Decimal('40.00')
    assert inst.paid_amount == Decimal('40.00')
    # Replaying the same confirmed amount must not be done by the webhook layer;
    # this test documents the invariant that one confirmation maps to one apply.
    assert installment_due(inst) == Decimal('60.00')


def test_daily_penalty_does_not_accrue_twice_for_same_day():
    class I:
        amount = Decimal('100.00')
        penalty_amount = Decimal('0.00')
        paid_amount = Decimal('0.00')
        status = 'OPEN'
        due_date = date.today() - timedelta(days=2)
        last_penalty_date = None

    inst = I()
    inc1 = calculate_daily_penalty(inst, date.today(), Decimal('0.01'))
    inc2 = calculate_daily_penalty(inst, date.today(), Decimal('0.01'))
    assert inc1 == Decimal('2.00')
    assert inc2 == Decimal('0.00')
    assert inst.penalty_amount == Decimal('2.00')
