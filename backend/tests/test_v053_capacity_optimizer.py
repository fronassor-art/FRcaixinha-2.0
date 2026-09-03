from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import app.services.capacity_optimizer_v053 as mod

def test_optimizer_finds_projection_capacity(monkeypatch):
    monkeypatch.setattr(mod, 'cash_balance', lambda db: Decimal('1000'))
    monkeypatch.setattr(mod, 'exposure', lambda db, member_id=None: Decimal('0'))
    def sim(db, **kw):
        x=Decimal(str(kw['new_loan_disbursements']))
        status='PASS' if x <= Decimal('60') else 'BLOCKED'
        return {'status':status,'as_of_date':'2026-09-03'}
    monkeypatch.setattr(mod, 'simulate', sim)
    group=SimpleNamespace(min_cash_reserve=Decimal('100'),max_global_exposure=None,max_member_exposure=None,max_loan_amount=None)
    member=SimpleNamespace(group_id=1)
    class DB:
        def get(self, cls, ident): return group if ident==1 else member
    out=mod.optimize_capacity(DB(),group_id=1,horizon_months=12)
    assert out['capacity'] == Decimal('60.00')
    assert out['decision']=='ALLOW'
    assert 'PROJECTED_CASH' in out['bottlenecks']

def test_optimizer_respects_global_exposure(monkeypatch):
    monkeypatch.setattr(mod, 'cash_balance', lambda db: Decimal('5000'))
    monkeypatch.setattr(mod, 'exposure', lambda db, member_id=None: Decimal('950'))
    monkeypatch.setattr(mod, 'simulate', lambda db, **kw: {'status':'PASS','as_of_date':'2026-09-03'})
    group=SimpleNamespace(min_cash_reserve=Decimal('100'),max_global_exposure=Decimal('1000'),max_member_exposure=None,max_loan_amount=None)
    class DB:
        def get(self, cls, ident): return group
    out=mod.optimize_capacity(DB(),group_id=1)
    assert out['capacity'] == Decimal('50.00')
    assert 'GLOBAL_EXPOSURE' in out['bottlenecks']

def test_optimizer_blocks_if_baseline_is_unsafe(monkeypatch):
    monkeypatch.setattr(mod, 'cash_balance', lambda db: Decimal('100'))
    monkeypatch.setattr(mod, 'exposure', lambda db, member_id=None: Decimal('0'))
    monkeypatch.setattr(mod, 'simulate', lambda db, **kw: {'status':'BLOCKED','as_of_date':'2026-09-03'})
    group=SimpleNamespace(min_cash_reserve=Decimal('200'),max_global_exposure=None,max_member_exposure=None,max_loan_amount=None)
    class DB:
        def get(self, cls, ident): return group
    out=mod.optimize_capacity(DB(),group_id=1)
    assert out['decision']=='BLOCKED'
    assert out['capacity']==Decimal('0.00')

def test_snapshot_hash_is_deterministic():
    a={'b':2,'a':1}; assert mod.snapshot_hash(a)==mod.snapshot_hash({'a':1,'b':2})
