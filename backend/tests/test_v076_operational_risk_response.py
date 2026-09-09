from app.services.operational_risk_response_v076 import _hash
def test_v076_hash_stable():
    assert _hash({'b':2,'a':1}) == _hash({'a':1,'b':2})
def test_v076_priority_mapping():
    from app.services.operational_risk_response_v076 import PRIORITY
    assert PRIORITY['CRITICAL']=='CRITICAL' and PRIORITY['HIGH']=='HIGH' and PRIORITY['ATTENTION']=='MEDIUM'
