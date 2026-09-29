import os
os.environ.setdefault('DATABASE_URL','sqlite:///./test.db'); os.environ.setdefault('JWT_SECRET','testsecret')
from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import ContinuousImprovementPrioritySnapshot
from app.services import continuous_improvement_priority_v085 as priority
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


def test_priority_snapshot_explicit_date_reuses_same_row(monkeypatch):
    clock = [datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr(priority, 'now', lambda: clock[0])
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    explicit_date = date(2026, 9, 27)
    try:
        with factory() as db:
            first, _ = priority.persist(db, snapshot_date=explicit_date)
            db.commit()
            first_id = first.id
            first_hash = first.snapshot_hash

            clock[0] = datetime(2026, 9, 28, 0, 6, tzinfo=timezone.utc)
            second, _ = priority.persist(db, snapshot_date=explicit_date)
            db.commit()

            assert second.id == first_id
            assert second.snapshot_date == explicit_date
            assert second.snapshot_hash != first_hash
            assert db.query(ContinuousImprovementPrioritySnapshot).count() == 1
    finally:
        engine.dispose()


def test_priority_snapshot_omitted_date_preserves_utc_default(monkeypatch):
    observed_now = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(priority, 'now', lambda: observed_now)
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as db:
            row, _ = priority.persist(db, actor_id=7)
            db.commit()
            assert row.snapshot_date == date(2026, 9, 28)
            assert row.generated_by == 7
            assert db.query(ContinuousImprovementPrioritySnapshot).count() == 1
    finally:
        engine.dispose()
