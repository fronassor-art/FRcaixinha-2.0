from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import SchedulerRun
from app.api import metrics as metrics_api


@pytest.fixture
def sessions():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _run(factory, *, job_key, day, status, **overrides):
    now = datetime.now(timezone.utc)
    values = {
        "job_key": job_key,
        "financial_date": day,
        "scheduled_for": now,
        "status": status,
        "attempt_count": 1,
    }
    values.update(overrides)
    with factory() as db:
        row = SchedulerRun(**values)
        db.add(row)
        db.commit()
        return row.id


def test_scheduler_metrics_report_durable_statuses_and_last_success(monkeypatch, sessions):
    from datetime import date

    now = datetime.now(timezone.utc)
    _run(
        sessions,
        job_key="worker_daily_tasks",
        day=date(2026, 9, 20),
        status="SUCCEEDED",
        completed_at=now - timedelta(hours=2),
    )
    _run(
        sessions,
        job_key="worker_daily_tasks",
        day=date(2026, 9, 21),
        status="FAILED",
        last_error="sensitive-error-detail",
    )
    _run(
        sessions,
        job_key="worker_daily_tasks",
        day=date(2026, 9, 22),
        status="RUNNING",
        lease_owner="secret-owner-token",
        lease_expires_at=now - timedelta(minutes=1),
    )
    _run(
        sessions,
        job_key="worker_cycle_participation_daily",
        day=date(2026, 9, 23),
        status="PENDING",
    )
    monkeypatch.setattr(metrics_api, "SessionLocal", sessions, raising=False)

    response = metrics_api.metrics()
    body = response.body.decode()

    assert response.status_code == 200
    assert re.search(
        r'frcaixinha_scheduler_runs_failed\{job_key="worker_daily_tasks"\} 1(?:\.0)?',
        body,
    )
    assert re.search(
        r'frcaixinha_scheduler_runs_expired_lease\{job_key="worker_daily_tasks"\} 1(?:\.0)?',
        body,
    )
    assert re.search(
        r'frcaixinha_scheduler_runs_pending\{job_key="worker_cycle_participation_daily"\} 1(?:\.0)?',
        body,
    )
    assert re.search(
        r'frcaixinha_scheduler_runs_last_succeeded_timestamp_seconds\{job_key="worker_daily_tasks"\} [1-9][0-9]*(?:\.0+)?',
        body,
    )
    assert "sensitive-error-detail" not in body
    assert "secret-owner-token" not in body
    assert "DATABASE_URL" not in body
    assert "REDIS_URL" not in body


def test_scheduler_metrics_return_zero_when_no_runs_exist(monkeypatch, sessions):
    monkeypatch.setattr(metrics_api, "SessionLocal", sessions, raising=False)

    response = metrics_api.metrics()
    body = response.body.decode()

    assert re.search(
        r'frcaixinha_scheduler_runs_failed\{job_key="worker_daily_tasks"\} 0(?:\.0)?',
        body,
    )
    assert re.search(
        r'frcaixinha_scheduler_runs_pending\{job_key="worker_cycle_participation_daily"\} 0(?:\.0)?',
        body,
    )
    assert re.search(
        r'frcaixinha_scheduler_runs_last_succeeded_timestamp_seconds\{job_key="worker_daily_tasks"\} 0(?:\.0)?',
        body,
    )


def test_daily_run_counter_does_not_call_claim_rejection_success():
    from app.worker import main as worker_main

    before_success = worker_main.WORKER_RUNS.labels("success")._value.get()
    before_rejected = worker_main.WORKER_RUNS.labels("claim_rejected")._value.get()

    worker_main._record_daily_result({"executed": False, "scheduler_run": "RUNNING"})

    assert worker_main.WORKER_RUNS.labels("success")._value.get() == before_success
    assert worker_main.WORKER_RUNS.labels("claim_rejected")._value.get() == before_rejected + 1


def test_worker_process_start_metric_is_process_local_and_restarts(monkeypatch):
    from app.worker import main as worker_main

    monkeypatch.setattr(worker_main.time, "time", lambda: 1_800_000_000)
    worker_main._set_worker_process_started_at()
    assert worker_main.WORKER_PROCESS_START_TIMESTAMP._value.get() == 1_800_000_000

    # A new worker process records a new timestamp; a restart starts a fresh
    # first-success grace period by design.
    monkeypatch.setattr(worker_main.time, "time", lambda: 1_800_000_100)
    worker_main._set_worker_process_started_at()
    assert worker_main.WORKER_PROCESS_START_TIMESTAMP._value.get() == 1_800_000_100


def test_never_succeeded_rule_uses_daily_schedule_upper_bound_and_db_guard():
    from app.core.metrics import WORKER_FIRST_SUCCESS_GRACE_SECONDS

    rules_path = Path(__file__).parents[2] / "ops/prometheus/rules/frcaixinha.yml"
    rules = rules_path.read_text(encoding="utf-8")
    start = rules.index("alert: FRcaixinhaSchedulerJobNeverSucceeded")
    end = rules.index("alert: FRcaixinhaSchedulerJobNoRecentSuccess", start)
    never_succeeded = rules[start:end]

    # Both jobs recur every 24 hours. A process can start just after a slot,
    # so the next opportunity may be almost 24 hours away; add the existing
    # six-hour operational tolerance used by the stale-success alert.
    assert WORKER_FIRST_SUCCESS_GRACE_SECONDS == 24 * 60 * 60 + 6 * 60 * 60
    assert f"> {WORKER_FIRST_SUCCESS_GRACE_SECONDS}" in never_succeeded
    assert 'frcaixinha_scheduler_runs_last_succeeded_timestamp_seconds{job="frcaixinha-api",job_key=~"worker_daily_tasks|worker_cycle_participation_daily"} == 0' in never_succeeded
    assert 'frcaixinha_worker_process_start_timestamp_seconds{job="frcaixinha-worker"}' in never_succeeded
    assert 'frcaixinha_scheduler_metrics_database_up{job="frcaixinha-api"} == 1' in never_succeeded
    assert 'up{job="frcaixinha-worker"} == 1' in never_succeeded
    assert "for: 5m" in never_succeeded


def test_first_success_and_last_success_are_distinct_alert_modes():
    rules_path = Path(__file__).parents[2] / "ops/prometheus/rules/frcaixinha.yml"
    rules = rules_path.read_text(encoding="utf-8")

    never_start = rules.index("alert: FRcaixinhaSchedulerJobNeverSucceeded")
    stale_start = rules.index("alert: FRcaixinhaSchedulerJobNoRecentSuccess")
    next_rule = rules.index("alert: FRcaixinhaOverdueInstallments", stale_start)
    never_rule = rules[never_start:stale_start]
    stale_rule = rules[stale_start:next_rule]

    assert 'last_succeeded_timestamp_seconds{job="frcaixinha-api",job_key=~"worker_daily_tasks|worker_cycle_participation_daily"} == 0' in never_rule
    assert 'last_succeeded_timestamp_seconds{job="frcaixinha-api"} > 0' in stale_rule
    assert "108000" in stale_rule


def test_postgres_metrics_failure_cannot_be_read_as_never_succeeded(monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.core.metrics import (
        SCHEDULER_METRICS_DATABASE_UP,
        SCHEDULER_RUNS_LAST_SUCCEEDED,
    )
    from app.worker import main as worker_main

    def unavailable():
        raise OperationalError("SELECT", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(metrics_api, "SessionLocal", unavailable, raising=False)
    response = metrics_api.metrics()
    body = response.body.decode()

    assert response.status_code == 200
    assert SCHEDULER_METRICS_DATABASE_UP._value.get() == 0
    assert re.search(
        r'frcaixinha_scheduler_runs_last_succeeded_timestamp_seconds\{job_key="worker_daily_tasks"\} 0(?:\.0)?',
        body,
    )
    assert "alert: FRcaixinhaSchedulerJobNeverSucceeded" in Path(
        __file__
    ).parents[2].joinpath("ops/prometheus/rules/frcaixinha.yml").read_text(encoding="utf-8")
    assert worker_main.WORKER_PROCESS_START_TIMESTAMP is not None


def test_runtime_smoke_trigger_covers_worker_runtime_dependencies():
    workflow_path = Path(__file__).parents[2] / ".github/workflows/oci-a1-runtime-smoke.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    for path in (
        "backend/app/worker/**",
        "backend/app/services/**",
        "backend/app/core/**",
        "backend/app/api/metrics.py",
        "backend/app/models/**",
        "backend/app/db/**",
        "backend/requirements.txt",
        "backend/Dockerfile",
        "backend/alembic/**",
        "ops/oci-a1/**",
        ".github/workflows/oci-a1-runtime-smoke.yml",
    ):
        assert workflow.count(f"'{path}'") == 2


def test_dashboard_and_observability_docs_use_worker_process_heartbeat():
    root = Path(__file__).parents[2]
    dashboard = json.loads(
        (root / "ops/grafana/dashboard.json").read_text(encoding="utf-8")
    )
    docs = (root / "docs/v0.22-observability.md").read_text(encoding="utf-8")

    heartbeat_expr = dashboard["panels"][3]["targets"][0]["expr"]
    assert heartbeat_expr == (
        'time()-frcaixinha_worker_heartbeat_timestamp_seconds{job="frcaixinha-worker"}'
    )
    assert "process-local" in docs
    assert "compartilhado via Redis" not in docs


def test_worker_exporter_down_alert_is_preserved():
    rules_path = Path(__file__).parents[2] / "ops/prometheus/rules/frcaixinha.yml"
    rules = rules_path.read_text(encoding="utf-8")
    start = rules.index("alert: FRcaixinhaWorkerMetricsDown")
    end = rules.index("alert: FRcaixinhaSchedulerRunFailed", start)
    worker_down = rules[start:end]

    assert 'up{job="frcaixinha-worker"} == 0' in worker_down
