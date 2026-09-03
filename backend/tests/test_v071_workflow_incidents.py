from app.services.workflow_incidents_v071 import SEVERITY_RANK, canonical

def test_severity_precedence():
    assert SEVERITY_RANK['CRITICAL'] > SEVERITY_RANK['ATTENTION']

def test_canonical_hash_input_is_deterministic():
    assert canonical({'b':2,'a':1}) == canonical({'a':1,'b':2})
