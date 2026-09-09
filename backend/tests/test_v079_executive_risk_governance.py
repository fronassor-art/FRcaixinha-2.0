from app.services.executive_risk_governance_v079 import required_approvals, canonical, _hash

def test_v079_competence_rules():
    assert required_approvals('LOW') == 1
    assert required_approvals('MEDIUM') == 1
    assert required_approvals('HIGH') == 2
    assert required_approvals('CRITICAL') == 2

def test_v079_hash_deterministic():
    payload={'decision_id':1,'required_approvals':2,'status':'PENDING'}
    assert canonical(payload)==canonical({'status':'PENDING','required_approvals':2,'decision_id':1})
    assert _hash(payload)==_hash(payload)
