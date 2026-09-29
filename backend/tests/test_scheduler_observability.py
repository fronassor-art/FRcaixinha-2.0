from datetime import datetime, timedelta, timezone
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
