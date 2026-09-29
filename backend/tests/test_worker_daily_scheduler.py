import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import AuditLog, SchedulerRun
from app.services import scheduler_runs
from app.worker import tasks


JOB_KEY = "worker_daily_tasks"
SCHEDULED_FOR = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
FINANCIAL_DATE = date(2026, 9, 27)
DATABASE_URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
def sessions():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _patch_daily_services(monkeypatch, factory, effect, *, on_finalization=None):
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "queue_installment_reminders", lambda *a, **k: 0)
    monkeypatch.setattr(
        tasks, "accrue_overdue_penalties",
        lambda *a, **k: {"installments": 0, "penalty_total": 0},
    )

    def collection(db, financial_date):
        effect(db, financial_date)
        return {"installments_scanned": 0, "events_created": 0}

    monkeypatch.setattr(tasks, "run_collection_cycle", collection)
    monkeypatch.setattr(tasks, "sync_cases", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_workflow_escalations", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_workflow_orchestration", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_execution_states", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "verify_all", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_incidents", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_capa_recurrence", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "persist_risk_snapshot", lambda *a, **k: _row_data(risk_score=0))
    monkeypatch.setattr(tasks, "sync_alerts", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_response_plans", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "persist_compliance_snapshot", lambda *a, **k: _row_data())
    monkeypatch.setattr(tasks, "persist_executive_dashboard", lambda *a, **k: _row_data())
    monkeypatch.setattr(tasks, "persist_dashboard", lambda *a, **k: _row_data())
    monkeypatch.setattr(tasks, "build_governance", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "create_execution", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "create_effectiveness", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "analyze_improvement", lambda *a, **k: [])
    monkeypatch.setattr(tasks, "create_improvement_plan", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "persist_improvement_dashboard", lambda *a, **k: _row_data())
    monkeypatch.setattr(tasks, "persist_improvement_priority", lambda *a, **k: _row_data(counts={}))
    monkeypatch.setattr(tasks, "persist_improvement_balancing", lambda *a, **k: _row_data(unassigned=[]))
    monkeypatch.setattr(tasks, "create_improvement_execution", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "certify_improvement", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "persist_improvement_audit", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "persist_executive_improvement_audit", lambda *a, **k: _row_data())

    def finalization(*args, **kwargs):
        if on_finalization is not None:
            return on_finalization(*args, **kwargs)
        return {}

    monkeypatch.setattr(tasks, "persist_finalization", finalization)


def _row_data(status="OK", **extra):
    return SimpleNamespace(id=1), {"status": status, **extra}


def _effect(db, financial_date):
    db.add(AuditLog(
        actor_user_id=None,
        action="TEST_DAILY_SCHEDULER_EFFECT",
        entity_type="scheduler_test",
        entity_id=financial_date.isoformat(),
        details="transaction sentinel",
    ))


def _state(factory, financial_date=FINANCIAL_DATE):
    with factory() as db:
        run = db.query(SchedulerRun).filter_by(
            job_key=JOB_KEY, financial_date=financial_date
        ).one_or_none()
        effects = db.query(AuditLog).filter_by(
            action="TEST_DAILY_SCHEDULER_EFFECT",
            entity_id=financial_date.isoformat(),
        ).count()
        return run, effects


def _require_unused_postgres_run(factory, financial_date):
    with factory() as db:
        existing = db.query(SchedulerRun).filter_by(
            job_key=JOB_KEY, financial_date=financial_date
        ).one_or_none()
    if existing is not None:
        pytest.skip("reserved worker_daily_tasks test date already exists in PostgreSQL")


def test_daily_run_happy_path_is_durable_and_effects_share_success_commit(monkeypatch, sessions):
    _patch_daily_services(monkeypatch, sessions, _effect)

    result = tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)

    run, effects = _state(sessions)
    assert result["scheduler_run"] == "SUCCEEDED"
    assert (run.job_key, run.financial_date, run.status) == (JOB_KEY, FINANCIAL_DATE, "SUCCEEDED")
    assert run.attempt_count == 1
    assert effects == 1


def test_same_date_duplicate_does_not_repeat_effect(monkeypatch, sessions):
    _patch_daily_services(monkeypatch, sessions, _effect)

    tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)
    duplicate = tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)

    run, effects = _state(sessions)
    assert duplicate["executed"] is False
    assert run.status == "SUCCEEDED" and run.attempt_count == 1
    assert effects == 1


def test_different_financial_dates_create_distinct_runs(monkeypatch, sessions):
    _patch_daily_services(monkeypatch, sessions, _effect)
    next_day = datetime(2026, 9, 29, 4, 5, tzinfo=timezone.utc)

    tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)
    tasks.run_daily_tasks(scheduled_for=next_day)

    with sessions() as db:
        runs = db.query(SchedulerRun).filter_by(job_key=JOB_KEY).order_by(
            SchedulerRun.financial_date
        ).all()
        effects = db.query(AuditLog).filter_by(action="TEST_DAILY_SCHEDULER_EFFECT").count()
    assert [(run.financial_date, run.status) for run in runs] == [
        (date(2026, 9, 27), "SUCCEEDED"),
        (date(2026, 9, 29), "SUCCEEDED"),
    ]
    assert effects == 2


def test_dml_failure_rolls_back_effect_then_failed_retry_succeeds(monkeypatch, sessions):
    def fail_after_effect(*args, **kwargs):
        raise RuntimeError("injected failure before commit")

    _patch_daily_services(monkeypatch, sessions, _effect, on_finalization=fail_after_effect)
    with pytest.raises(RuntimeError, match="injected failure"):
        tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)
    run, effects = _state(sessions)
    assert run.status == "FAILED" and run.attempt_count == 1
    assert effects == 0

    _patch_daily_services(monkeypatch, sessions, _effect)
    tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)
    run, effects = _state(sessions)
    assert run.status == "SUCCEEDED" and run.attempt_count == 2
    assert effects == 1


def test_process_crash_before_commit_rolls_back_and_expired_run_is_retryable(monkeypatch, sessions):
    class SimulatedCrash(BaseException):
        pass

    _patch_daily_services(
        monkeypatch, sessions, _effect,
        on_finalization=lambda *a, **k: (_ for _ in ()).throw(SimulatedCrash()),
    )
    with pytest.raises(SimulatedCrash):
        tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)

    run, effects = _state(sessions)
    assert run.status == "RUNNING" and run.attempt_count == 1
    assert effects == 0

    with sessions() as db:
        run = db.query(SchedulerRun).filter_by(
            job_key=JOB_KEY, financial_date=FINANCIAL_DATE
        ).one()
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    _patch_daily_services(monkeypatch, sessions, _effect)
    tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)
    run, effects = _state(sessions)
    assert run.status == "SUCCEEDED" and run.attempt_count == 2
    assert effects == 1


def test_post_commit_process_crash_retry_does_not_repeat_effect(monkeypatch, sessions):
    class SimulatedCrash(BaseException):
        pass

    _patch_daily_services(monkeypatch, sessions, _effect)
    try:
        tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)
        raise SimulatedCrash("process stopped after task commit")
    except SimulatedCrash:
        pass

    _patch_daily_services(monkeypatch, sessions, _effect)
    duplicate = tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)
    run, effects = _state(sessions)
    assert duplicate["executed"] is False
    assert run.status == "SUCCEEDED" and run.attempt_count == 1
    assert effects == 1


def test_stale_owner_reclaimed_before_execution_cannot_write_effect(monkeypatch, sessions):
    _patch_daily_services(monkeypatch, sessions, _effect)
    lock_execution = tasks.lock_run_for_execution

    def reclaim_before_lock(db, run_id, *, lease_owner):
        now = tasks._utc_now()
        with sessions() as other:
            row = other.get(SchedulerRun, run_id)
            row.lease_expires_at = now - timedelta(seconds=1)
            other.commit()
            assert scheduler_runs.claim_run(
                other, run_id, lease_owner="worker-b", now=now, lease_seconds=300
            )
            other.commit()
        # SQLite has no SELECT FOR UPDATE/refresh behavior; force this
        # simulator session to read the reclaimed owner from the database.
        db.expire_all()
        return lock_execution(db, run_id, lease_owner=lease_owner)

    monkeypatch.setattr(tasks, "lock_run_for_execution", reclaim_before_lock)
    with pytest.raises(scheduler_runs.SchedulerLeaseLost):
        tasks.run_daily_tasks(scheduled_for=SCHEDULED_FOR)

    run, effects = _state(sessions)
    assert run.status == "RUNNING" and run.lease_owner == "worker-b"
    assert effects == 0


@pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires real PostgreSQL for worker claim/fencing transactions",
)
def test_postgresql_two_workers_one_daily_effect_and_expired_lease_waits_for_row_lock(monkeypatch):
    engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=0)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    financial_date = date(2099, 12, 1)
    scheduled_for = datetime(2099, 12, 1, 4, 5, tzinfo=timezone.utc)
    owner_context = threading.local()
    base_time = scheduled_for
    entered_effect = threading.Event()
    release_effect = threading.Event()
    second_claim_started = threading.Event()
    second_pid = []
    effect_count = [0]
    count_lock = threading.Lock()
    token = uuid.uuid4().hex
    try:
        _require_unused_postgres_run(factory, financial_date)

        monkeypatch.setattr(tasks, "_utc_now", lambda: owner_context.now, raising=False)

        def effect(db, day):
            with count_lock:
                effect_count[0] += 1
            entered_effect.set()
            assert release_effect.wait(timeout=15)
            db.add(AuditLog(
                action="TEST_DAILY_SCHEDULER_EFFECT", entity_type="scheduler_test",
                entity_id=day.isoformat() + ":" + token, details="postgres sentinel",
            ))

        _patch_daily_services(monkeypatch, factory, effect)
        actual_claim = scheduler_runs.claim_run

        def traced_claim(db, run_id, *, lease_owner, now=None, lease_seconds=300):
            if getattr(owner_context, "second", False):
                second_pid.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
                second_claim_started.set()
            return actual_claim(
                db, run_id, lease_owner=lease_owner, now=now, lease_seconds=lease_seconds
            )

        monkeypatch.setattr(scheduler_runs, "claim_run", traced_claim)

        def invoke(first):
            owner_context.second = not first
            owner_context.now = base_time if first else base_time + timedelta(seconds=301)
            if not first:
                return tasks.run_daily_tasks(scheduled_for=scheduled_for)
            return tasks.run_daily_tasks(scheduled_for=scheduled_for)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(invoke, True)
            assert entered_effect.wait(timeout=15)
            second = pool.submit(invoke, False)
            assert second_claim_started.wait(timeout=15)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with factory() as probe:
                    wait_type = probe.execute(
                        text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                        {"pid": second_pid[0]},
                    ).scalar_one_or_none()
                if wait_type == "Lock":
                    break
                threading.Event().wait(0.01)
            else:
                pytest.fail("second worker did not block on PostgreSQL row ownership")
            assert effect_count[0] == 1
            release_effect.set()
            first_result = first.result(timeout=30)
            second_result = second.result(timeout=30)

        assert effect_count[0] == 1
        with factory() as db:
            run = db.query(SchedulerRun).filter_by(
                job_key=JOB_KEY, financial_date=financial_date
            ).one()
            count = db.query(AuditLog).filter_by(
                action="TEST_DAILY_SCHEDULER_EFFECT",
                entity_id=financial_date.isoformat() + ":" + token,
            ).count()
        assert run.status == "SUCCEEDED" and run.attempt_count == 1
        assert count == 1
        assert first_result.get("scheduler_run") == "SUCCEEDED"
        assert second_result["executed"] is False
    finally:
        release_effect.set()
        with factory() as db:
            db.execute(text(
                "DELETE FROM audit_logs WHERE action='TEST_DAILY_SCHEDULER_EFFECT' AND entity_id LIKE :token"
            ), {"token": "%" + token})
            db.execute(text(
                "DELETE FROM scheduler_runs WHERE job_key=:job AND financial_date=:day"
            ), {"job": JOB_KEY, "day": financial_date})
            db.commit()
        engine.dispose()


@pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires real PostgreSQL for daily transaction rollback/retry",
)
def test_postgresql_daily_effect_and_success_rollback_together_then_retry(monkeypatch):
    engine = create_engine(DATABASE_URL)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    financial_date = date(2099, 12, 2)
    scheduled_for = datetime(2099, 12, 2, 4, 5, tzinfo=timezone.utc)
    token = uuid.uuid4().hex

    def fail_before_commit(*args, **kwargs):
        raise RuntimeError("postgres transaction failure sentinel")

    try:
        _require_unused_postgres_run(factory, financial_date)

        _patch_daily_services(
            monkeypatch, factory,
            lambda db, day: db.add(AuditLog(
                action="TEST_DAILY_SCHEDULER_EFFECT", entity_type="scheduler_test",
                entity_id=day.isoformat() + ":" + token, details="postgres rollback sentinel",
            )),
            on_finalization=fail_before_commit,
        )
        with pytest.raises(RuntimeError, match="transaction failure sentinel"):
            tasks.run_daily_tasks(scheduled_for=scheduled_for)
        with factory() as db:
            run = db.query(SchedulerRun).filter_by(
                job_key=JOB_KEY, financial_date=financial_date
            ).one()
            count = db.query(AuditLog).filter_by(
                action="TEST_DAILY_SCHEDULER_EFFECT",
                entity_id=financial_date.isoformat() + ":" + token,
            ).count()
        assert run.status == "FAILED" and run.attempt_count == 1
        assert count == 0

        _patch_daily_services(
            monkeypatch, factory,
            lambda db, day: db.add(AuditLog(
                action="TEST_DAILY_SCHEDULER_EFFECT", entity_type="scheduler_test",
                entity_id=day.isoformat() + ":" + token, details="postgres retry sentinel",
            )),
        )
        tasks.run_daily_tasks(scheduled_for=scheduled_for)
        with factory() as db:
            run = db.query(SchedulerRun).filter_by(
                job_key=JOB_KEY, financial_date=financial_date
            ).one()
            count = db.query(AuditLog).filter_by(
                action="TEST_DAILY_SCHEDULER_EFFECT",
                entity_id=financial_date.isoformat() + ":" + token,
            ).count()
        assert run.status == "SUCCEEDED" and run.attempt_count == 2
        assert count == 1
    finally:
        with factory() as db:
            db.execute(text(
                "DELETE FROM audit_logs WHERE action='TEST_DAILY_SCHEDULER_EFFECT' AND entity_id LIKE :token"
            ), {"token": "%" + token})
            db.execute(text(
                "DELETE FROM scheduler_runs WHERE job_key=:job AND financial_date=:day"
            ), {"job": JOB_KEY, "day": financial_date})
            db.commit()
        engine.dispose()


@pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires real PostgreSQL to verify stale worker is fenced before DML",
)
def test_postgresql_stale_owner_reclaimed_before_daily_effect_is_rejected(monkeypatch):
    engine = create_engine(DATABASE_URL)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    financial_date = date(2099, 12, 3)
    scheduled_for = datetime(2099, 12, 3, 4, 5, tzinfo=timezone.utc)
    token = uuid.uuid4().hex
    try:
        _require_unused_postgres_run(factory, financial_date)

        _patch_daily_services(
            monkeypatch, factory,
            lambda db, day: db.add(AuditLog(
                action="TEST_DAILY_SCHEDULER_EFFECT", entity_type="scheduler_test",
                entity_id=day.isoformat() + ":" + token, details="must not persist",
            )),
        )
        lock_execution = tasks.lock_run_for_execution

        def reclaim_before_lock(db, run_id, *, lease_owner):
            now = tasks._utc_now()
            with factory() as other:
                row = other.get(SchedulerRun, run_id)
                row.lease_expires_at = now - timedelta(seconds=1)
                other.commit()
                assert scheduler_runs.claim_run(
                    other, run_id, lease_owner="replacement-owner",
                    now=now, lease_seconds=300,
                )
                other.commit()
            return lock_execution(db, run_id, lease_owner=lease_owner)

        monkeypatch.setattr(tasks, "lock_run_for_execution", reclaim_before_lock)
        with pytest.raises(scheduler_runs.SchedulerLeaseLost):
            tasks.run_daily_tasks(scheduled_for=scheduled_for)

        with factory() as db:
            run = db.query(SchedulerRun).filter_by(
                job_key=JOB_KEY, financial_date=financial_date
            ).one()
            count = db.query(AuditLog).filter_by(
                action="TEST_DAILY_SCHEDULER_EFFECT",
                entity_id=financial_date.isoformat() + ":" + token,
            ).count()
        assert run.status == "RUNNING" and run.lease_owner == "replacement-owner"
        assert count == 0
    finally:
        with factory() as db:
            db.execute(text(
                "DELETE FROM audit_logs WHERE action='TEST_DAILY_SCHEDULER_EFFECT' AND entity_id LIKE :token"
            ), {"token": "%" + token})
            db.execute(text(
                "DELETE FROM scheduler_runs WHERE job_key=:job AND financial_date=:day"
            ), {"job": JOB_KEY, "day": financial_date})
            db.commit()
        engine.dispose()
