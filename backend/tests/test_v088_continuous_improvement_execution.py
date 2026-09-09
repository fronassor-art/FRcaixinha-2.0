from app.services.continuous_improvement_execution_v088 import canonical, digest

def test_execution_hash_is_stable_and_order_independent():
    a={'decision_id':1,'status':'PENDING','assigned_to':2,'resolution_note':None}
    b={'assigned_to':2,'resolution_note':None,'status':'PENDING','decision_id':1}
    assert canonical(a)==canonical(b)
    assert digest(a)==digest(b)
    assert len(digest(a))==64

def test_execution_hash_changes_with_evidence():
    base={'decision_id':1,'status':'COMPLETED','evidence_note':'evidence','assigned_to':2}
    assert digest(base)!=digest({**base,'evidence_note':'different'})
