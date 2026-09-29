from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.worker import tasks


class _Query:
    def filter(self, *args, **kwargs):
        return self

    def filter_by(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []

    def first(self):
        return None

    def populate_existing(self):
        return self

    def one_or_none(self):
        return None


class _Session:
    def query(self, *args, **kwargs):
        return _Query()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _row_data(status="OK", **extra):
    return SimpleNamespace(id=1), {"status": status, **extra}


def _stub_daily_services(monkeypatch, calls):
    monkeypatch.setattr(tasks, "SessionLocal", _Session)
    run = SimpleNamespace(id=1, status="PENDING")

    def claim(_db, _run_id, **kwargs):
        run.status = "RUNNING"
        return True

    def succeeded(_db, _run_id, **kwargs):
        run.status = "SUCCEEDED"
        return run

    monkeypatch.setattr(tasks.scheduler_runs, "get_or_create_run", lambda *a, **k: (run, False))
    monkeypatch.setattr(tasks.scheduler_runs, "claim_run", claim)
    monkeypatch.setattr(tasks, "lock_run_for_execution", lambda *a, **k: datetime.now(timezone.utc))
    monkeypatch.setattr(tasks.scheduler_runs, "mark_run_succeeded_after_locked_execution", succeeded)
    monkeypatch.setattr(tasks.scheduler_runs, "mark_run_failed", lambda *a, **k: run)

    def reminders(db, days_ahead=3, *, financial_date=None):
        calls["reminders"].append(financial_date)
        return 0

    def penalties(db, financial_date, rate):
        calls["penalties"].append(financial_date)
        return {"installments": 0, "penalty_total": 0}

    def dated(name, result):
        def call(db, *args, **kwargs):
            supplied = kwargs.get("snapshot_date")
            if supplied is None:
                supplied = next((arg for arg in args if isinstance(arg, date)), None)
            calls[name].append(supplied)
            return result
        return call

    monkeypatch.setattr(tasks, "queue_installment_reminders", reminders)
    monkeypatch.setattr(tasks, "accrue_overdue_penalties", penalties)
    monkeypatch.setattr(tasks, "run_collection_cycle", dated("collections", {}))
    monkeypatch.setattr(tasks, "sync_cases", dated("recovery", {}))
    monkeypatch.setattr(tasks, "sync_workflow_escalations", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_workflow_orchestration", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_execution_states", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "verify_all", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_incidents", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_capa_recurrence", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "persist_risk_snapshot", dated("risk", _row_data(risk_score=0)))
    monkeypatch.setattr(tasks, "sync_alerts", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "sync_response_plans", lambda *a, **k: {})
    monkeypatch.setattr(tasks, "persist_compliance_snapshot", dated("compliance", _row_data()))
    monkeypatch.setattr(tasks, "persist_executive_dashboard", dated("executive", _row_data()))
    monkeypatch.setattr(tasks, "persist_dashboard", dated("executive_risk", _row_data()))
    monkeypatch.setattr(tasks, "persist_improvement_dashboard", dated("improvement", _row_data()))
    monkeypatch.setattr(tasks, "persist_improvement_priority", lambda *a, **k: _row_data(counts={}))
    monkeypatch.setattr(tasks, "persist_improvement_balancing", dated("balancing", _row_data(unassigned=[])))
    monkeypatch.setattr(tasks, "analyze_improvement", lambda *a, **k: [])
    monkeypatch.setattr(tasks, "persist_improvement_audit", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "persist_executive_improvement_audit", lambda *a, **k: _row_data())

    def finalization(db, actor_id=None, *, financial_date=None):
        calls["finalization"].append(financial_date)
        return {}

    monkeypatch.setattr(tasks, "persist_finalization", finalization)


def _stub_cycle_scheduler(monkeypatch, calls, participation, cycle):
    units = []
    run = SimpleNamespace(id=17)

    class Rows(_Query):
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return list(self.rows)

    class ParticipationRow(_Query):
        def one_or_none(self):
            return participation

    class CycleSession(_Session):
        def query(self, *models, **kwargs):
            model = models[0]
            if model is tasks.SchedulerRunUnit:
                return Rows(units)
            if model is tasks.SchedulerRunUnit.status:
                return Rows([(unit.status,) for unit in units])
            if model is tasks.CycleParticipation.id:
                return Rows([(5, 8)])
            if model is tasks.CycleParticipation:
                return ParticipationRow()
            raise AssertionError(f"unexpected query model: {model}")

        def get(self, model, identity):
            return cycle if model is tasks.Cycle else None

    def new_unit(_db, *, run_id, unit_key):
        unit = SimpleNamespace(id=len(units) + 1, unit_key=unit_key, status="PENDING")
        units.append(unit)
        return unit, True

    def claim_unit(_db, unit_id, **kwargs):
        unit = next(unit for unit in units if unit.id == unit_id)
        unit.status = "RUNNING"
        return True

    def mark_unit_succeeded(_db, unit_id, **kwargs):
        unit = next(unit for unit in units if unit.id == unit_id)
        unit.status = "SUCCEEDED"
        return unit

    monkeypatch.setattr(tasks, "SessionLocal", CycleSession)
    monkeypatch.setattr(tasks.scheduler_runs, "get_or_create_run", lambda *a, **k: (run, False))
    monkeypatch.setattr(tasks.scheduler_runs, "claim_run", lambda *a, **k: True)
    monkeypatch.setattr(tasks.scheduler_runs, "lock_run_for_execution", lambda *a, **k: datetime.now(timezone.utc))
    monkeypatch.setattr(tasks.scheduler_runs, "renew_run_lease", lambda *a, **k: None)
    monkeypatch.setattr(tasks.scheduler_runs, "get_or_create_unit", new_unit)
    monkeypatch.setattr(tasks.scheduler_runs, "claim_unit", claim_unit)
    monkeypatch.setattr(tasks.scheduler_runs, "lock_unit_for_execution", lambda *a, **k: datetime.now(timezone.utc))
    monkeypatch.setattr(tasks.scheduler_runs, "mark_unit_succeeded_after_locked_execution", mark_unit_succeeded)
    monkeypatch.setattr(tasks.scheduler_runs, "mark_run_succeeded_after_locked_execution", lambda *a, **k: run)
    monkeypatch.setattr(tasks.scheduler_runs, "mark_run_failed", lambda *a, **k: run)
    monkeypatch.setattr(
        tasks, "ensure_contributions_for_entry",
        lambda db, **kwargs: calls.append(("contribution", kwargs["entry_date"])),
    )
    monkeypatch.setattr(
        tasks, "materialize_active_charges",
        lambda db, **kwargs: calls.append(("charges", kwargs["effective_at"])),
    )
    monkeypatch.setattr(
        tasks, "evaluate_delinquency",
        lambda db, **kwargs: calls.append(("delinquency", kwargs["effective_at"]))
        or SimpleNamespace(status="OK"),
    )


def _daily_calls():
    names = (
        "reminders", "penalties", "collections", "recovery", "risk", "compliance",
        "executive", "executive_risk", "improvement", "balancing", "finalization",
    )
    return {name: [] for name in names}


def test_worker_daily_uses_one_explicit_financial_date(monkeypatch):
    calls = _daily_calls()
    _stub_daily_services(monkeypatch, calls)
    scheduled_for = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)

    # Simulate the process clock moving after this run was scheduled.
    class ShiftedClock:
        current = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(tasks, "datetime", ShiftedClock)

    reminders = tasks.queue_installment_reminders

    def advance_clock(db, **kwargs):
        ShiftedClock.current = datetime(2026, 9, 29, 0, 5, tzinfo=timezone.utc)
        return reminders(db, **kwargs)

    monkeypatch.setattr(tasks, "queue_installment_reminders", advance_clock)
    tasks.run_daily_tasks(scheduled_for=scheduled_for)

    expected = date(2026, 9, 27)  # 00:05 UTC is still 21:05 in America/Belem.
    assert calls["reminders"] == [expected]
    assert calls["penalties"] == [expected]
    assert calls["collections"] == [expected]
    assert calls["recovery"] == [expected]
    for name in ("risk", "compliance", "executive", "executive_risk", "improvement", "balancing", "finalization"):
        assert calls[name] == [expected]


def test_worker_daily_financial_date_is_stable_for_same_scheduled_for(monkeypatch):
    scheduled_for = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
    seen = []
    for process_date in (date(2026, 9, 27), date(2026, 9, 29)):
        calls = _daily_calls()
        _stub_daily_services(monkeypatch, calls)

        class ShiftedClock:
            @classmethod
            def now(cls, tz=None):
                return datetime.combine(process_date, datetime.min.time(), tzinfo=timezone.utc)

        monkeypatch.setattr(tasks, "datetime", ShiftedClock)
        tasks.run_daily_tasks(scheduled_for=scheduled_for)
        seen.append(calls["penalties"][0])

    assert seen == [date(2026, 9, 27), date(2026, 9, 27)]


def test_worker_cycle_daily_uses_financial_civil_date(monkeypatch):
    calls = []
    participation = SimpleNamespace(status="ACTIVE", cycle_id=8, member_id=13)
    cycle = SimpleNamespace(start_date=date(2026, 1, 1))
    _stub_cycle_scheduler(monkeypatch, calls, participation, cycle)

    scheduled_for = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
    tasks.run_cycle_participation_tasks(scheduled_for=scheduled_for)

    assert calls == [
        ("contribution", date(2026, 9, 27)),
        ("charges", scheduled_for),
        ("delinquency", scheduled_for),
    ]


@pytest.mark.parametrize(
    ("scheduled_for", "expected"),
    [
        (datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc), date(2026, 9, 27)),
        (datetime(2026, 9, 28, 4, 5, tzinfo=timezone.utc), date(2026, 9, 28)),
    ],
)
def test_cycle_run_uses_belem_civil_date_for_utc_boundaries(monkeypatch, scheduled_for, expected):
    calls = []
    participation = SimpleNamespace(status="ACTIVE", cycle_id=8, member_id=13)
    cycle = SimpleNamespace(start_date=date(2026, 1, 1))
    _stub_cycle_scheduler(monkeypatch, calls, participation, cycle)
    monkeypatch.setattr(
        tasks, "ensure_contributions_for_entry",
        lambda db, **kwargs: calls.append(kwargs["entry_date"]),
    )

    tasks.run_cycle_participation_tasks(scheduled_for=scheduled_for)
    assert calls == [
        expected,
        ("charges", scheduled_for),
        ("delinquency", scheduled_for),
    ]
