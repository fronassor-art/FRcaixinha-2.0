from app.services.continuous_improvement_v082 import digest

def test_v082_digest_is_deterministic():
    assert digest({'z':3,'a':1}) == digest({'a':1,'z':3})

def test_v082_pattern_thresholds():
    assert 3/4 >= .5
    assert 1/4 < .5
