from app.services.executive_risk_effectiveness_v081 import digest

def test_v081_digest_is_deterministic():
    assert digest({'b':2,'a':1}) == digest({'a':1,'b':2})

def test_v081_score_delta():
    baseline, followup = 70, 45
    assert followup-baseline == -25
