from app.services.executive_risk_execution_v080 import canonical, digest

def test_v080_hash_is_deterministic():
    assert digest({'b': 2, 'a': 1}) == digest({'a': 1, 'b': 2})

def test_v080_canonical_is_stable():
    assert canonical({'b': 2, 'a': 1}) == '{"a":1,"b":2}'
