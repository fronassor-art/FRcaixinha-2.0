from decimal import Decimal
from types import SimpleNamespace
from app.services.loan_payments_v16 import remaining, apply_confirmed_payment

def test_remaining_installment():
    i=SimpleNamespace(amount=Decimal('180.00'), paid_amount=Decimal('50.00'))
    assert remaining(i)==Decimal('130.00')

def test_payment_marks_paid_once():
    i=SimpleNamespace(amount=Decimal('180.00'), paid_amount=Decimal('0.00'), status='OPEN')
    p=SimpleNamespace(amount=Decimal('180.00'), ledger_posted_at=None, id=9)
    class Q:
        def filter(self,*a,**k): return self
        def first(self): return None
    class DB:
        def query(self,*a): return Q()
        def add(self,x): pass
    db=DB()
    assert apply_confirmed_payment(db,p,i) is True
    assert i.paid_amount==Decimal('180.00') and i.status=='PAID'
    assert apply_confirmed_payment(db,p,i) is False
