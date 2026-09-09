import os
os.environ.setdefault('DATABASE_URL','postgresql://test:test@localhost/test')
os.environ.setdefault('JWT_SECRET','test-secret')
from app.services.continuous_improvement_executive_audit_v092 import canonical, digest

def test_executive_report_hash_ignores_generated_at():
    a={'schema':'v0.92','status':'PASS','generated_at':'2026-01-01T00:00:00+00:00','indicators':{'executions':2}}
    b=dict(a); b['generated_at']='2026-02-01T00:00:00+00:00'
    for x in (a,b): x.pop('generated_at',None)
    assert digest(a)==digest(b)

def test_executive_report_hash_changes_on_integrity_failure():
    a={'schema':'v0.92','status':'PASS','indicators':{'integrity_failures':0}}
    b={'schema':'v0.92','status':'CRITICAL','indicators':{'integrity_failures':1}}
    assert digest(a)!=digest(b)
