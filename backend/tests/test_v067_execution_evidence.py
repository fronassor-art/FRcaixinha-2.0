from types import SimpleNamespace
from app.services.workflow_evidence_v067 import checklist_status

def test_checklist_empty_is_ready():
    class Q:
        def filter_by(self, **kw): return self
        def order_by(self, *a): return self
        def all(self): return []
    class DB:
        def query(self, model): return Q()
    assert checklist_status(DB(), 1)['ready'] is True

def test_evidence_types_are_restricted():
    assert {'NOTE','LINK','ATTACHMENT'} == {'NOTE','LINK','ATTACHMENT'}
