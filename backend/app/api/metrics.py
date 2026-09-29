from fastapi import APIRouter, Response
from datetime import datetime, timezone

from sqlalchemy import func

from app.core.metrics import (
    SCHEDULER_METRICS_DATABASE_UP,
    SCHEDULER_RUNS_EXPIRED_LEASE,
    SCHEDULER_RUNS_FAILED,
    SCHEDULER_RUNS_LAST_SUCCEEDED,
    SCHEDULER_RUNS_PENDING,
    metrics_response,
)
from app.db.session import SessionLocal
from app.models import SchedulerRun

router = APIRouter()

@router.get('/metrics', include_in_schema=False)
def metrics():
    now = datetime.now(timezone.utc)
    jobs = ("worker_daily_tasks", "worker_cycle_participation_daily")
    db = None
    try:
        db = SessionLocal()
        for job_key in jobs:
            latest = db.query(func.max(SchedulerRun.completed_at)).filter(
                SchedulerRun.job_key == job_key,
                SchedulerRun.status == "SUCCEEDED",
            ).scalar()
            if latest is None:
                latest_timestamp = 0
            else:
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
                latest_timestamp = latest.timestamp()
            SCHEDULER_RUNS_LAST_SUCCEEDED.labels(job_key).set(latest_timestamp)
            SCHEDULER_RUNS_FAILED.labels(job_key).set(
                db.query(SchedulerRun.id).filter(
                    SchedulerRun.job_key == job_key,
                    SchedulerRun.status == "FAILED",
                ).count()
            )
            SCHEDULER_RUNS_EXPIRED_LEASE.labels(job_key).set(
                db.query(SchedulerRun.id).filter(
                    SchedulerRun.job_key == job_key,
                    SchedulerRun.status == "RUNNING",
                    SchedulerRun.lease_expires_at <= now,
                ).count()
            )
            SCHEDULER_RUNS_PENDING.labels(job_key).set(
                db.query(SchedulerRun.id).filter(
                    SchedulerRun.job_key == job_key,
                    SchedulerRun.status == "PENDING",
                ).count()
            )
        SCHEDULER_METRICS_DATABASE_UP.set(1)
    except Exception as exc:
        # Metrics remain scrapeable during a database outage; values are
        # zeroed and the separate database-up gauge makes them non-authoritative.
        SCHEDULER_METRICS_DATABASE_UP.set(0)
        for job_key in jobs:
            SCHEDULER_RUNS_LAST_SUCCEEDED.labels(job_key).set(0)
            SCHEDULER_RUNS_FAILED.labels(job_key).set(0)
            SCHEDULER_RUNS_EXPIRED_LEASE.labels(job_key).set(0)
            SCHEDULER_RUNS_PENDING.labels(job_key).set(0)
        import logging
        logging.getLogger(__name__).warning(
            "scheduler_metrics_database_unavailable error_type=%s",
            type(exc).__name__,
        )
    finally:
        if db is not None:
            db.close()
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type.split(';')[0])
