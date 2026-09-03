import os
os.environ.setdefault('DATABASE_URL','sqlite:///./test.db'); os.environ.setdefault('JWT_SECRET','testsecret')
from app.services.continuous_improvement_priority_v085 import _score
class R:
    id=1; pattern_code='INEFFECTIVE_PATTERN'; sample_size=3
class P: id=1; recommendation_id=1; status='OPEN'; due_at=None
class M: plan_id=1; result='INEFFECTIVE'
def test_priority_scoring_is_explainable_and_bounded():
    score, priority, breakdown = _score(R(), [P()], [M()], 100)
    assert score == 75
    assert priority == 'CRITICAL'
    assert breakdown['risk'] == 30
    assert sum(breakdown[k] for k in ['risk','impact','urgency','recurrence','effectiveness','sla']) == 75

def test_low_risk_open_item_has_deterministic_priority():
    R.pattern_code='PARTIAL_PATTERN'; R.sample_size=1
    score, priority, breakdown = _score(R(), [], [], 0)
    assert score == 20
    assert priority == 'LOW'
