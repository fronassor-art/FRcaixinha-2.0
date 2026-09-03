from app.services.admin_workflow_v062 import STATUSES, PRIORITIES, _hash

def test_workflow_states_and_priorities():
    assert STATUSES == ('PENDING','IN_ANALYSIS','ASSIGNED','IN_EXECUTION','COMPLETED')
    assert PRIORITIES == ('LOW','MEDIUM','HIGH','CRITICAL')

def test_hash_is_deterministic():
    p={'task_id':1,'status':'COMPLETED','evidence':'reconciliation:123'}
    assert _hash(p)==_hash(p)
    assert _hash(p)!=_hash({**p,'task_id':2})
