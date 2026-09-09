from app.services.executive_risk_response_v077 import canonical

def test_v077_canonical_stable():
    assert canonical({'b':2,'a':1}) == '{"a":1,"b":2}'

def test_v077_status_thresholds():
    from app.services.executive_risk_response_v077 import _status
    assert _status(0)=='PASS'; assert _status(30)=='ATTENTION'; assert _status(70)=='CRITICAL'
