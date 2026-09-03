from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from app.services.loan_engine_v17 import add_months, installment_due, apply_payment, calculate_daily_penalty

def inst(**kw):
    d={'amount':Decimal('100.00'),'penalty_amount':Decimal('0.00'),'paid_penalty_amount':Decimal('0.00'),'paid_amount':Decimal('0.00'),'due_date':date(2026,1,10),'status':'OPEN','last_penalty_date':None}
    d.update(kw); return SimpleNamespace(**d)

def test_add_months_handles_month_end():
    assert add_months(date(2026,1,31),1) == date(2026,2,28)

def test_partial_payment_then_paid():
    x=inst()
    r=apply_payment(x,Decimal('40'))
    assert r['applied']==Decimal('40.00') and x.status=='PARTIAL'
    r=apply_payment(x,Decimal('60'))
    assert r['applied']==Decimal('60.00') and x.status=='PAID'

def test_penalty_is_incremental_and_idempotent_by_date():
    x=inst()
    first=calculate_daily_penalty(x,date(2026,1,11),Decimal('0.01'))
    second=calculate_daily_penalty(x,date(2026,1,11),Decimal('0.01'))
    assert first==Decimal('1.00') and second==Decimal('0.00')

def test_penalty_changes_due_balance():
    x=inst(penalty_amount=Decimal('2.00'))
    assert installment_due(x)==Decimal('102.00')


def test_payment_allocates_penalty_before_base_without_corrupting_base():
    x=inst(penalty_amount=Decimal('10.00'))
    r=apply_payment(x,Decimal('15.00'))
    assert r['penalty_applied']==Decimal('10.00')
    assert r['base_applied']==Decimal('5.00')
    assert x.paid_penalty_amount==Decimal('10.00')
    assert x.paid_amount==Decimal('5.00')
    assert installment_due(x)==Decimal('95.00')
