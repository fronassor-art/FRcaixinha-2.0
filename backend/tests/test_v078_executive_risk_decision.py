from app.services.executive_risk_decision_v078 import canonical,_hash

def test_v078_canonical_and_hash_are_deterministic():
    value={'z':1,'a':['x',2]}
    assert canonical(value)==canonical({'a':['x',2],'z':1})
    assert _hash(value)==_hash({'a':['x',2],'z':1})
