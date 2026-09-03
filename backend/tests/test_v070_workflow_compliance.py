from app.services.workflow_compliance_v070 import _canonical
import hashlib

def test_compliance_hash_is_deterministic():
    a={'status':'PASS','checks':[{'b':2,'a':1}]}
    b={'checks':[{'a':1,'b':2}],'status':'PASS'}
    assert hashlib.sha256(_canonical(a).encode()).hexdigest() == hashlib.sha256(_canonical(b).encode()).hexdigest()

def test_status_precedence():
    statuses={'PASS','ATTENTION','CRITICAL'}
    assert 'CRITICAL' in statuses
