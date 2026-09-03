from decimal import Decimal
from types import SimpleNamespace
import app.services.allocation_governance_v055 as mod

def test_governance_prefers_payment_history(monkeypatch):
    monkeypatch.setattr(mod,'allocate_resources',lambda *a,**k:{'capacity':Decimal('100'),'items':[{'member_id':1,'quota_units':Decimal('1'),'risk_decision':'ALLOW'},{'member_id':2,'quota_units':Decimal('1'),'risk_decision':'ALLOW'}]})
    class Q:
      def __init__(self,rows): self.rows=rows
      def filter(self,*a,**k): return self
      def all(self): return self.rows
      def first(self): return None
    members=[SimpleNamespace(id=1,joined_at=None),SimpleNamespace(id=2,joined_at=None)]
    class DB:
      def query(self,c): return Q(members if c.__name__=='Member' else [])
    old=mod._payment_score
    mod._payment_score=lambda db,i: Decimal('1') if i==1 else Decimal('0')
    out=mod.build_governance_allocation(DB(),group_id=1,capacity=Decimal('100'))
    mod._payment_score=old
    assert out['items'][0]['governed_amount']>out['items'][1]['governed_amount']

def test_blocked_risk_gets_zero(monkeypatch):
    monkeypatch.setattr(mod,'allocate_resources',lambda *a,**k:{'capacity':Decimal('10'),'items':[{'member_id':1,'quota_units':Decimal('1'),'risk_decision':'BLOCKED'}]})
    class Q:
      def __init__(self, rows): self.rows=rows
      def filter(self,*a,**k): return self
      def all(self): return self.rows
      def first(self): return None
    class DB:
      def query(self,c):
        if c.__name__=='Member': return Q([SimpleNamespace(id=1,joined_at=None)])
        return Q([])
    out=mod.build_governance_allocation(DB(),group_id=1,capacity=Decimal('10'))
    assert out['items'][0]['governed_amount']==Decimal('0.00')

def test_snapshot_hash_deterministic():
    assert mod.snapshot_hash({'x':1,'y':2})==mod.snapshot_hash({'y':2,'x':1})
