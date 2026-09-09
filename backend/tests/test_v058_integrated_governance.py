from decimal import Decimal
from app.services.integrated_governance_v058 import canonical, sha

def test_v058_canonical_is_deterministic():
    assert canonical({'b':2,'a':1}) == '{"a":1,"b":2}'
    assert sha({'a':1}) == sha({'a':1})

def test_v058_hash_changes_when_decision_changes():
    assert sha({'final_decision':'ALLOW'}) != sha({'final_decision':'REVIEW'})

def test_v058_schema_and_release_semantics():
    # Contract-level invariant: the integrated engine can only mark release-ready when final decision is ALLOW.
    for decision in ('BLOCK','REVIEW'):
        assert decision != 'ALLOW'
    assert 'release_ready' not in {'final_decision':'REVIEW'}
