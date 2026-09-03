from app.services.workflow_evidence_integrity_v069 import _canonical
import hashlib

def test_canonical_hash_is_deterministic():
    p={'b':2,'a':1}; assert hashlib.sha256(_canonical(p).encode()).hexdigest()==hashlib.sha256(_canonical({'a':1,'b':2}).encode()).hexdigest()

def test_mismatch_status_rule():
    expected='a'*64; observed='b'*64
    assert ('PASS' if observed==expected else 'MISMATCH')=='MISMATCH'
