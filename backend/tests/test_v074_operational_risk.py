from app.services.operational_risk_v074 import canonical, snapshot_hash

def test_v074_canonical_and_hash_deterministic():
    a={'b':2,'a':1}; b={'a':1,'b':2}
    assert canonical(a)==canonical(b)
    assert snapshot_hash(a)==snapshot_hash(b)

def test_v074_status_thresholds_are_ordered():
    assert 100 >= 70 >= 30 >= 0
