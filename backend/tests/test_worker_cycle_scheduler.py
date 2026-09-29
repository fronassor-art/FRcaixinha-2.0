"""Durable replay and transaction contract for cycle participation runs."""
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import (
    AuditLog, Cycle, CycleParticipation, Group, Member, SchedulerRun,
    SchedulerRunUnit, User,
)
from app.services import scheduler_runs
from app.worker import tasks


JOB_KEY = "worker_cycle_participation_daily"
SCHEDULED_FOR = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
FINANCIAL_DATE = date(2026, 9, 27)
EFFECT_ACTION = "TEST_CYCLE_SCHEDULER_EFFECT"
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


def _add_participation(db, *, cycle_id, group_id, token):
    index = uuid.uuid4().hex[:8]
    cpf = f"{(int(token[:12], 16) + int(index, 16)) % (10 ** 11):011d}"
    user = User(
        name=f"Cycle Test {index}", email=f"cycle-{token}-{index}@example.test",
        cpf=cpf, password_hash="test-only",
    )
    db.add(user)
    db.flush()
    member = Member(user_id=user.id, group_id=group_id, status="ACTIVE")
    db.add(member)
    db.flush()
    participation = CycleParticipation(cycle_id=cycle_id, member_id=member.id, status="ACTIVE")
    db.add(participation)
    db.flush()
    return participation.id


def _create_participations(db, count, *, start_date=date(2026, 1, 1)):
    rows = []
    token = uuid.uuid4().hex
    cycle = Cycle(
        start_date=start_date,
        entry_deadline=start_date + timedelta(days=30),
        closing_reference_date=start_date + timedelta(days=365),
        monthly_amount=Decimal("150.00"), months=12, max_quotas=50,
        status="OPEN",
    )
    group = Group(name=f"cycle-scheduler-{token}")
    db.add_all([cycle, group])
    db.flush()
    for _ in range(count):
        rows.append(_add_participation(db, cycle_id=cycle.id, group_id=group.id, token=token))
    db.commit()
    return rows


def _patch_cycle_services(monkeypatch, factory, *, fail_ids=(), on_effect=None, result_status="ACTIVE"):
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    calls = []

    def ensure(db, *, member_id, cycle_id, entry_date):
        participation_id = db.query(CycleParticipation.id).filter_by(
            member_id=member_id, cycle_id=cycle_id
        ).one()[0]
        calls.append(participation_id)
        db.add(AuditLog(
            action=EFFECT_ACTION, entity_type="cycle_participation",
            entity_id=str(participation_id), details=f"financial_date={entry_date.isoformat()}",
        ))
        if on_effect is not None:
            on_effect(db, participation_id)
        if participation_id in fail_ids:
            raise RuntimeError("injected participation failure")
        return []

    def charges(*args, **kwargs):
        return 0

    def delinquency(*args, **kwargs):
        return SimpleNamespace(status=result_status)

    monkeypatch.setattr(tasks, "ensure_contributions_for_entry", ensure)
    monkeypatch.setattr(tasks, "materialize_active_charges", charges)
    monkeypatch.setattr(tasks, "evaluate_delinquency", delinquency)
    return calls


def _run_state(factory, financial_date=FINANCIAL_DATE):
    with factory() as db:
        run = db.query(SchedulerRun).filter_by(
            job_key=JOB_KEY, financial_date=financial_date,
        ).one_or_none()
        units = [] if run is None else db.query(SchedulerRunUnit).filter_by(
            run_id=run.id,
        ).order_by(SchedulerRunUnit.id).all()
        effects = db.query(AuditLog).filter_by(action=EFFECT_ACTION).order_by(
            AuditLog.id,
        ).all()
        return run, units, effects


def _expire_run_and_units(factory, run_id, *, expire_units=True):
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    with factory() as db:
        run = db.get(SchedulerRun, run_id)
        run.lease_expires_at = expired
        if expire_units:
            for unit in db.query(SchedulerRunUnit).filter_by(run_id=run_id):
                if unit.status == "RUNNING":
                    unit.lease_expires_at = expired
        db.commit()


def _cleanup_postgres(factory, financial_date, ids, *, effect_ids=None, effect_token=None):
    with factory() as db:
        run = db.query(SchedulerRun).filter_by(
            job_key=JOB_KEY, financial_date=financial_date,
        ).one_or_none()
        if run is not None:
            db.query(SchedulerRunUnit).filter_by(run_id=run.id).delete(synchronize_session=False)
            db.delete(run)
        effects = db.query(AuditLog).filter(AuditLog.action == EFFECT_ACTION)
        if effect_token is not None:
            effects = effects.filter(AuditLog.entity_id == effect_token)
        elif effect_ids:
            effects = effects.filter(AuditLog.entity_id.in_([str(value) for value in effect_ids]))
        effects.delete(synchronize_session=False)
        if ids:
            member_ids = [member_id for (member_id,) in db.query(
                CycleParticipation.member_id,
            ).filter(CycleParticipation.id.in_(ids)).all()]
            user_ids = [user_id for (user_id,) in db.query(Member.user_id).filter(
                Member.id.in_(member_ids),
            ).all()]
            group_ids = [group_id for (group_id,) in db.query(Member.group_id).filter(
                Member.id.in_(member_ids),
            ).distinct().all()]
            cycle_ids = [cycle_id for (cycle_id,) in db.query(
                CycleParticipation.cycle_id,
            ).filter(CycleParticipation.id.in_(ids)).distinct().all()]
            db.query(CycleParticipation).filter(CycleParticipation.id.in_(ids)).delete(synchronize_session=False)
            db.query(Member).filter(Member.id.in_(member_ids)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
            db.query(Cycle).filter(Cycle.id.in_(cycle_ids)).delete(synchronize_session=False)
            db.query(Group).filter(Group.id.in_(group_ids)).delete(synchronize_session=False)
        db.commit()


def test_cycle_run_happy_path_has_one_durable_unit_per_active_participation(monkeypatch, sessions):
    ids = _create_participations(sessions(), 2)
    calls = _patch_cycle_services(monkeypatch, sessions)

    result = tasks.run_cycle_participation_tasks(SCHEDULED_FOR)

    run, units, effects = _run_state(sessions)
    assert result == {"processed": 2, "blocked": 0}
    assert run.job_key == JOB_KEY and run.status == "SUCCEEDED"
    assert [unit.unit_key for unit in units] == [
        f"cycle:1:participation:{ids[0]}", f"cycle:1:participation:{ids[1]}"
    ]
    assert [unit.status for unit in units] == ["SUCCEEDED", "SUCCEEDED"]
    assert len(effects) == 2 and calls == ids


def test_same_date_duplicate_does_not_replay_succeeded_units(monkeypatch, sessions):
    _create_participations(sessions(), 1)
    calls = _patch_cycle_services(monkeypatch, sessions)

    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 1, "blocked": 0}
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 0, "blocked": 0}

    run, units, effects = _run_state(sessions)
    assert run.status == "SUCCEEDED" and run.attempt_count == 1
    assert len(units) == len(effects) == len(calls) == 1


def test_different_financial_date_creates_a_new_run_and_snapshot(monkeypatch, sessions):
    _create_participations(sessions(), 1)
    calls = _patch_cycle_services(monkeypatch, sessions)
    next_run = datetime(2026, 9, 29, 4, 5, tzinfo=timezone.utc)

    tasks.run_cycle_participation_tasks(SCHEDULED_FOR)
    tasks.run_cycle_participation_tasks(next_run)

    with sessions() as db:
        runs = db.query(SchedulerRun).filter_by(job_key=JOB_KEY).order_by(
            SchedulerRun.financial_date,
        ).all()
        assert [(r.financial_date, r.status) for r in runs] == [
            (date(2026, 9, 27), "SUCCEEDED"), (date(2026, 9, 29), "SUCCEEDED")
        ]
    assert len(calls) == 2


def test_snapshot_is_frozen_when_participation_is_added_after_first_materialization(monkeypatch, sessions):
    initial_ids = _create_participations(sessions(), 2)
    added = []

    def add_participation(db, participation_id):
        if not added:
            row = db.get(CycleParticipation, participation_id)
            member = db.get(Member, row.member_id)
            new_id = _add_participation(
                db, cycle_id=row.cycle_id, group_id=member.group_id, token=uuid.uuid4().hex,
            )
            added.append(new_id)

    calls = _patch_cycle_services(monkeypatch, sessions, fail_ids={initial_ids[1]}, on_effect=add_participation)
    first = tasks.run_cycle_participation_tasks(SCHEDULED_FOR)
    run, units, effects = _run_state(sessions)
    assert first == {"processed": 1, "blocked": 0}
    assert run.status == "FAILED"
    assert [unit.unit_key for unit in units] == [
        f"cycle:1:participation:{initial_ids[0]}",
        f"cycle:1:participation:{initial_ids[1]}",
    ]
    assert len(effects) == 1

    calls.clear()
    _patch_cycle_services(monkeypatch, sessions, on_effect=add_participation)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 1, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "SUCCEEDED"
    assert len(units) == 2 and len(effects) == 2
    assert added[0] not in [int(unit.unit_key.rsplit(":", 1)[1]) for unit in units]


def test_partial_failure_preserves_completed_unit_and_retry_skips_it(monkeypatch, sessions):
    ids = _create_participations(sessions(), 2)
    calls = _patch_cycle_services(monkeypatch, sessions, fail_ids={ids[1]})

    result = tasks.run_cycle_participation_tasks(SCHEDULED_FOR)
    run, units, effects = _run_state(sessions)
    assert result == {"processed": 1, "blocked": 0}
    assert run.status == "FAILED"
    assert [unit.status for unit in units] == ["SUCCEEDED", "FAILED"]
    assert len(effects) == 1

    calls = _patch_cycle_services(monkeypatch, sessions)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 1, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "SUCCEEDED" and run.attempt_count == 2
    assert [unit.status for unit in units] == ["SUCCEEDED", "SUCCEEDED"]
    assert len(effects) == 2 and len(calls) == 1 and calls[0] == ids[1]


def test_unit_effect_and_succeeded_checkpoint_roll_back_together_on_crash(monkeypatch, sessions):
    class SimulatedCrash(BaseException):
        pass

    _create_participations(sessions(), 1)
    _patch_cycle_services(monkeypatch, sessions, on_effect=lambda *args: (_ for _ in ()).throw(SimulatedCrash()))
    with pytest.raises(SimulatedCrash):
        tasks.run_cycle_participation_tasks(SCHEDULED_FOR)
    run, units, effects = _run_state(sessions)
    assert run.status == "RUNNING" and units[0].status == "RUNNING"
    assert effects == []

    _expire_run_and_units(sessions, run.id)
    calls = _patch_cycle_services(monkeypatch, sessions)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 1, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "SUCCEEDED" and units[0].status == "SUCCEEDED"
    assert len(effects) == len(calls) == 1


def test_crash_after_unit_commit_retry_does_not_repeat_effect(monkeypatch, sessions):
    class SimulatedCrash(BaseException):
        pass

    ids = _create_participations(sessions(), 2)

    def crash_on_second_unit(db, participation_id):
        if participation_id == ids[1]:
            raise SimulatedCrash("process stopped after first unit commit")

    calls = _patch_cycle_services(monkeypatch, sessions, on_effect=crash_on_second_unit)
    with pytest.raises(SimulatedCrash):
        tasks.run_cycle_participation_tasks(SCHEDULED_FOR)
    run, units, effects = _run_state(sessions)
    assert run.status == "RUNNING"
    assert [unit.status for unit in units] == ["SUCCEEDED", "RUNNING"]
    assert len(effects) == 1 and calls == ids
    _expire_run_and_units(sessions, run.id)

    calls = _patch_cycle_services(monkeypatch, sessions)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 1, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "SUCCEEDED" and [unit.status for unit in units] == ["SUCCEEDED", "SUCCEEDED"]
    assert len(effects) == 2 and calls == [ids[1]]


def test_utc_belem_boundary_identity_is_preserved(monkeypatch, sessions):
    _create_participations(sessions(), 1)
    _patch_cycle_services(monkeypatch, sessions)
    result = tasks.run_cycle_participation_tasks(SCHEDULED_FOR)
    run, _, _ = _run_state(sessions)
    assert SCHEDULED_FOR.date() == date(2026, 9, 28)
    assert run.financial_date == FINANCIAL_DATE == date(2026, 9, 27)
    assert result["processed"] == 1


def test_run_lease_renewal_extends_only_current_unexpired_owner(sessions):
    now = datetime.now(timezone.utc)
    with sessions() as db:
        run, _ = scheduler_runs.get_or_create_run(
            db, job_key=JOB_KEY, financial_date=FINANCIAL_DATE,
            scheduled_for=SCHEDULED_FOR,
        )
        assert scheduler_runs.claim_run(
            db, run.id, lease_owner="cycle-owner-a", now=now, lease_seconds=300,
        )
        run_id = run.id
        db.commit()

    with sessions() as db:
        expiry = scheduler_runs.renew_run_lease(
            db, run_id, lease_owner="cycle-owner-a", lease_seconds=300,
        )
        assert expiry > now + timedelta(seconds=299)
        run = db.get(SchedulerRun, run_id)
        run.lease_expires_at = now
        db.commit()
        with pytest.raises(scheduler_runs.SchedulerLeaseLost):
            scheduler_runs.renew_run_lease(
                db, run_id, lease_owner="cycle-owner-a", lease_seconds=300,
            )


def test_inactive_after_snapshot_completes_noop_unit_without_financial_effect(monkeypatch, sessions):
    ids = _create_participations(sessions(), 1)
    calls = _patch_cycle_services(monkeypatch, sessions)
    original = scheduler_runs.lock_unit_for_execution
    changed = []

    def deactivate_before_lock(db, unit_id, *, lease_owner):
        if not changed:
            with sessions() as other:
                row = other.get(CycleParticipation, ids[0])
                row.status = "VOLUNTARILY_EXITED"
                row.voluntary_exit_at = SCHEDULED_FOR
                other.commit()
            changed.append(True)
        return original(db, unit_id, lease_owner=lease_owner)

    monkeypatch.setattr(scheduler_runs, "lock_unit_for_execution", deactivate_before_lock, raising=False)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 0, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "SUCCEEDED" and units[0].status == "SUCCEEDED"
    assert effects == [] and calls == []


def test_pre_start_cycle_is_a_successful_noop_and_empty_snapshot_is_terminal(monkeypatch, sessions):
    _create_participations(sessions(), 1, start_date=date(2026, 10, 1))
    calls = _patch_cycle_services(monkeypatch, sessions)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 0, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "SUCCEEDED" and units[0].status == "SUCCEEDED"
    assert effects == [] and calls == []


def test_blocked_counter_counts_only_newly_processed_blocked_result(monkeypatch, sessions):
    _create_participations(sessions(), 1)
    _patch_cycle_services(monkeypatch, sessions, result_status="BLOCKED_DELINQUENCY")
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 1, "blocked": 1}
    _patch_cycle_services(monkeypatch, sessions, result_status="BLOCKED_DELINQUENCY")
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 0, "blocked": 0}


def test_stale_owner_is_rejected_before_participation_effect(monkeypatch, sessions):
    ids = _create_participations(sessions(), 1)
    calls = _patch_cycle_services(monkeypatch, sessions)
    original = scheduler_runs.lock_unit_for_execution
    reclaimed = []

    def reclaim_before_effect(db, unit_id, *, lease_owner):
        if not reclaimed:
            run_id = db.get(SchedulerRunUnit, unit_id).run_id
            _expire_run_and_units(sessions, run_id, expire_units=False)
            with sessions() as other:
                assert scheduler_runs.claim_run(other, run_id, lease_owner="worker-b", lease_seconds=300)
                other.commit()
                unit = other.query(SchedulerRunUnit).filter_by(run_id=run_id).one()
                assert scheduler_runs.claim_unit(other, unit.id, lease_owner="worker-b", lease_seconds=300)
                other.commit()
            db.expire_all()
            reclaimed.append(True)
        return original(db, unit_id, lease_owner=lease_owner)

    monkeypatch.setattr(scheduler_runs, "lock_unit_for_execution", reclaim_before_effect, raising=False)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 0, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "RUNNING" and run.lease_owner == "worker-b"
    assert units[0].lease_owner == "worker-b"
    assert effects == [] and calls == []


def test_zero_participation_snapshot_stays_empty_after_later_arrival(monkeypatch, sessions):
    _patch_cycle_services(monkeypatch, sessions)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 0, "blocked": 0}
    _create_participations(sessions(), 1)
    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 0, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "SUCCEEDED" and units == [] and effects == []


def test_pending_unit_prevents_run_success(monkeypatch, sessions):
    _create_participations(sessions(), 1)
    calls = _patch_cycle_services(monkeypatch, sessions)
    monkeypatch.setattr(scheduler_runs, "claim_unit", lambda *args, **kwargs: False)

    assert tasks.run_cycle_participation_tasks(SCHEDULED_FOR) == {"processed": 0, "blocked": 0}
    run, units, effects = _run_state(sessions)
    assert run.status == "FAILED"
    assert [unit.status for unit in units] == ["PENDING"]
    assert effects == [] and calls == []


@pytest.mark.skipif(not DATABASE_URL.startswith("postgresql"), reason="requires real PostgreSQL scheduler row locks")
def test_postgresql_two_workers_same_cycle_run_commit_effect_once(monkeypatch):
    engine = create_engine(DATABASE_URL, pool_size=6, max_overflow=0)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    scheduled_for = datetime(2098, 9, 28, 0, 5, tzinfo=timezone.utc)
    financial_date = date(2098, 9, 27)
    started = threading.Event()
    release = threading.Event()
    second_claim_started = threading.Event()
    owner_context = threading.local()
    second_pid = []
    token = uuid.uuid4().hex
    ids = []
    try:
        with factory() as db:
            if db.query(SchedulerRun).filter_by(job_key=JOB_KEY, financial_date=financial_date).count():
                pytest.skip("reserved PostgreSQL test run date already exists")
            ids = _create_participations(db, 1, start_date=date(2098, 1, 1))
        calls = []
        monkeypatch.setattr(tasks, "SessionLocal", factory)

        def ensure(db, *, member_id, cycle_id, entry_date):
            calls.append(member_id)
            started.set()
            assert release.wait(timeout=20)
            db.add(AuditLog(action=EFFECT_ACTION, entity_type="cycle_participation",
                            entity_id=token, details=entry_date.isoformat()))

        monkeypatch.setattr(tasks, "ensure_contributions_for_entry", ensure)
        monkeypatch.setattr(tasks, "materialize_active_charges", lambda *a, **k: 0)
        monkeypatch.setattr(tasks, "evaluate_delinquency", lambda *a, **k: SimpleNamespace(status="ACTIVE"))
        actual_claim = scheduler_runs.claim_run

        def traced_claim(db, run_id, *, lease_owner, now=None, lease_seconds=300):
            if getattr(owner_context, "second", False):
                second_pid.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
                second_claim_started.set()
            return actual_claim(
                db, run_id, lease_owner=lease_owner, now=now, lease_seconds=lease_seconds,
            )

        monkeypatch.setattr(scheduler_runs, "claim_run", traced_claim)

        def invoke(second):
            owner_context.second = second
            return tasks.run_cycle_participation_tasks(scheduled_for)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(invoke, False)
            assert started.wait(timeout=15)
            second = pool.submit(invoke, True)
            assert second_claim_started.wait(timeout=15)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                with factory() as probe:
                    wait_type = probe.execute(
                        text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                        {"pid": second_pid[0]},
                    ).scalar_one_or_none()
                if wait_type == "Lock":
                    break
                time.sleep(0.01)
            else:
                pytest.fail("second PostgreSQL worker did not wait on the SchedulerRun lock")
            release.set()
            first_result = first.result(timeout=30)
            second_result = second.result(timeout=30)
        with factory() as db:
            run = db.query(SchedulerRun).filter_by(job_key=JOB_KEY, financial_date=financial_date).one()
            units = db.query(SchedulerRunUnit).filter_by(run_id=run.id).all()
            effects = db.query(AuditLog).filter_by(action=EFFECT_ACTION, entity_id=token).count()
        assert first_result == {"processed": 1, "blocked": 0}
        assert second_result == {"processed": 0, "blocked": 0}
        assert run.status == "SUCCEEDED" and run.attempt_count == 1
        assert len(units) == effects == len(calls) == 1
    finally:
        release.set()
        if ids:
            _cleanup_postgres(factory, financial_date, ids, effect_token=token)
        engine.dispose()


@pytest.mark.skipif(not DATABASE_URL.startswith("postgresql"), reason="requires real PostgreSQL transaction semantics")
def test_postgresql_unit_effect_rollback_retry_and_succeeded_unit_not_replayed(monkeypatch):
    # The SQLite cases cover state-machine behavior; this live database case
    # proves per-unit DML and unit success share a PostgreSQL commit boundary.
    engine = create_engine(DATABASE_URL)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    scheduled_for = datetime(2098, 9, 29, 0, 5, tzinfo=timezone.utc)
    financial_date = date(2098, 9, 28)
    token = uuid.uuid4().hex
    ids = []
    try:
        with factory() as db:
            if db.query(SchedulerRun).filter_by(
                job_key=JOB_KEY, financial_date=financial_date,
            ).one_or_none() is not None:
                pytest.skip("reserved PostgreSQL test run date already exists")
            ids = _create_participations(db, 2, start_date=date(2098, 2, 1))
        _patch_cycle_services(monkeypatch, factory, fail_ids={ids[1]})
        result = tasks.run_cycle_participation_tasks(scheduled_for)
        with factory() as db:
            run = db.query(SchedulerRun).filter_by(job_key=JOB_KEY, financial_date=financial_date).one()
            units = db.query(SchedulerRunUnit).filter_by(run_id=run.id).order_by(SchedulerRunUnit.id).all()
            effects_before = db.query(AuditLog).filter(
                AuditLog.action == EFFECT_ACTION,
                AuditLog.entity_id.in_([str(value) for value in ids]),
            ).count()
        assert result["processed"] == 1 and run.status == "FAILED"
        assert [unit.status for unit in units] == ["SUCCEEDED", "FAILED"]
        assert effects_before == 1

        _patch_cycle_services(monkeypatch, factory)
        result = tasks.run_cycle_participation_tasks(scheduled_for)
        with factory() as db:
            run = db.query(SchedulerRun).filter_by(job_key=JOB_KEY, financial_date=financial_date).one()
            units = db.query(SchedulerRunUnit).filter_by(run_id=run.id).all()
            effects_after = db.query(AuditLog).filter(
                AuditLog.action == EFFECT_ACTION,
                AuditLog.entity_id.in_([str(value) for value in ids]),
            ).count()
        assert result["processed"] == 1 and run.status == "SUCCEEDED"
        assert [unit.status for unit in units] == ["SUCCEEDED", "SUCCEEDED"]
        assert effects_after == 2
    finally:
        if ids:
            _cleanup_postgres(factory, financial_date, ids, effect_ids=ids)
        engine.dispose()


@pytest.mark.skipif(not DATABASE_URL.startswith("postgresql"), reason="requires real PostgreSQL owner fencing")
def test_postgresql_reclaimed_parent_rejects_stale_unit_before_effect(monkeypatch):
    engine = create_engine(DATABASE_URL)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    scheduled_for = datetime(2098, 10, 1, 0, 5, tzinfo=timezone.utc)
    financial_date = date(2098, 9, 30)
    ids = []
    try:
        with factory() as db:
            existing = db.query(SchedulerRun).filter_by(
                job_key=JOB_KEY, financial_date=financial_date,
            ).one_or_none()
            if existing is not None:
                pytest.skip("reserved PostgreSQL test run date already exists")
            ids = _create_participations(db, 1, start_date=date(2098, 3, 1))
        calls = _patch_cycle_services(monkeypatch, factory)
        original = scheduler_runs.lock_unit_for_execution

        def reclaim_then_validate(db, unit_id, *, lease_owner):
            unit = db.get(SchedulerRunUnit, unit_id)
            run_id = unit.run_id
            now = datetime.now(timezone.utc)
            with factory() as other:
                run = other.get(SchedulerRun, run_id)
                run.lease_expires_at = now - timedelta(seconds=1)
                other.commit()
                assert scheduler_runs.claim_run(
                    other, run_id, lease_owner="worker-b", now=now, lease_seconds=300,
                )
                other.commit()
                assert scheduler_runs.claim_unit(
                    other, unit_id, lease_owner="worker-b", now=now, lease_seconds=300,
                )
                other.commit()
            return original(db, unit_id, lease_owner=lease_owner)

        monkeypatch.setattr(scheduler_runs, "lock_unit_for_execution", reclaim_then_validate)
        assert tasks.run_cycle_participation_tasks(scheduled_for) == {"processed": 0, "blocked": 0}
        if ids:
            with factory() as db:
                run = db.query(SchedulerRun).filter_by(
                    job_key=JOB_KEY, financial_date=financial_date,
                ).one()
                unit = db.query(SchedulerRunUnit).filter_by(run_id=run.id).one()
                effect_count = db.query(AuditLog).filter_by(
                    action=EFFECT_ACTION, entity_id=str(ids[0]),
                ).count()
            assert run.status == "RUNNING" and run.lease_owner == "worker-b"
            assert unit.status == "RUNNING" and unit.lease_owner == "worker-b"
            assert effect_count == 0 and calls == []
    finally:
        if ids:
            _cleanup_postgres(factory, financial_date, ids, effect_ids=ids)
        engine.dispose()
