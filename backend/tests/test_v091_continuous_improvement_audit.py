import os
os.environ.setdefault('DATABASE_URL','postgresql://test:test@localhost/test')
os.environ.setdefault('JWT_SECRET','test-secret')
from app.services.continuous_improvement_audit_v091 import canonical, digest

def test_canonical_hash_is_stable():
    a={'b':2,'a':1}; b={'a':1,'b':2}
    assert canonical(a)==canonical(b)
    assert digest(a)==digest(b)

def test_hash_changes_on_cycle_mutation():
    a={'schema':'v0.91','checks':{'certified':True}}
    b={'schema':'v0.91','checks':{'certified':False}}
    assert digest(a)!=digest(b)
