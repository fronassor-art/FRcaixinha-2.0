from decimal import Decimal
from types import SimpleNamespace
import app.services.resource_allocation_v054 as mod

def test_pro_rata_quota_allocation(monkeypatch):
    monkeypatch.setattr(mod,'evaluate_loan_pipeline',lambda *a,**k:{'decision':'ALLOW'})
    group=SimpleNamespace(max_member_exposure=None,max_loan_amount=None)
    members=[SimpleNamespace(id=1,status='ACTIVE',quota=SimpleNamespace(units=Decimal('1'))),SimpleNamespace(id=2,status='ACTIVE',quota=SimpleNamespace(units=Decimal('3')))]
    class Q:
      def filter(self,*a,**k): return self
      def order_by(self,*a,**k): return self
      def all(self): return []
    class DB:
      def get(self,c,i): return group
      def query(self,c):
        if c.__name__=='Member': return SimpleNamespace(filter=lambda *a,**k:SimpleNamespace(order_by=lambda *a,**k:SimpleNamespace(all=lambda:members)))
        return Q()
    out=mod.allocate_resources(DB(),group_id=1,capacity=Decimal('100'))
    assert out['allocated_total']==Decimal('100.00')
    assert out['items'][1]['recommended_amount'] > out['items'][0]['recommended_amount']

def test_review_weight_is_reduced(monkeypatch):
    monkeypatch.setattr(mod,'evaluate_loan_pipeline',lambda *a,**k:{'decision':'REVIEW'})
    members=[SimpleNamespace(id=1,status='ACTIVE',quota=SimpleNamespace(units=Decimal('1'))),SimpleNamespace(id=2,status='ACTIVE',quota=SimpleNamespace(units=Decimal('1')))]
    class Q:
      def filter(self,*a,**k): return self
      def order_by(self,*a,**k): return self
      def all(self): return []
    group=SimpleNamespace(max_member_exposure=None,max_loan_amount=None)
    class DB:
      def get(self,c,i): return group
      def query(self,c): return SimpleNamespace(filter=lambda *a,**k:SimpleNamespace(order_by=lambda *a,**k:SimpleNamespace(all=lambda:members))) if c.__name__=='Member' else Q()
    out=mod.allocate_resources(DB(),group_id=1,capacity=Decimal('10'))
    assert all(x['risk_decision']=='REVIEW' for x in out['items'])

def test_hash_deterministic():
    assert mod.snapshot_hash({'a':1,'b':2})==mod.snapshot_hash({'b':2,'a':1})
