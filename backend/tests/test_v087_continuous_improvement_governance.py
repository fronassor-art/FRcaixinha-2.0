import hashlib,json
from app.services.continuous_improvement_governance_v087 import canonical,digest

def test_governance_hash_is_canonical_and_stable():
    a={'b':2,'a':1}; b={'a':1,'b':2}
    assert canonical(a)==canonical(b)
    assert digest(a)==digest(b)
    assert len(digest(a))==64

def test_governance_decision_payload_changes_hash():
    from app.services.continuous_improvement_governance_v087 import digest
    base={'snapshot_id':1,'recommendation_id':2,'decision':'ACCEPT','target_user_id':3,'actor_id':4,'note':'ok'}
    assert digest(base)!=digest({**base,'target_user_id':5})
