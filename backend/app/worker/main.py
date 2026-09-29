import logging, time
from datetime import datetime, timezone
from sqlalchemy.exc import DBAPIError
from prometheus_client import start_http_server
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.worker.tasks import run_daily_tasks, run_cycle_participation_tasks
from app.core.metrics import (
    WORKER_DISPATCH_ATTEMPTS,
    WORKER_DISPATCH_SKIPPED,
    WORKER_HEARTBEAT,
    WORKER_POSTGRES_FAILURES,
    WORKER_PROCESS_START_TIMESTAMP,
    WORKER_REDIS_FAIL_OPEN,
    WORKER_RUNS,
)

configure_logging()
log = logging.getLogger("worker")


def _set_worker_process_started_at(timestamp: float | None = None) -> None:
    """Publish a process-local start reference before the exporter is served."""
    WORKER_PROCESS_START_TIMESTAMP.set(time.time() if timestamp is None else timestamp)


# Module initialization is the worker entrypoint's process-local activation
# reference. The API exporter imports the same metric but never sets it.
_set_worker_process_started_at()

try:
    import redis
except ImportError:
    redis = None

# Redis keys are UTC operational lock identities; task financial dates come
# from the single timezone-aware scheduled_for passed by main().
def _acquire_slot_lock(job_key: str, scheduled_for: datetime) -> bool:
    if redis is None:
        raise RuntimeError("redis_client_unavailable")
    if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
        raise ValueError("scheduled_for must be timezone-aware")
    scheduled_for = scheduled_for.astimezone(timezone.utc)
    key = f"frcaixinha:{job_key}:{scheduled_for.date().isoformat()}"
    exceptions = redis.exceptions
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        acquired = client.set(key, "1", nx=True, ex=86400)
    except (
        exceptions.AuthenticationError,
        exceptions.AuthorizationError,
        exceptions.BusyLoadingError,
        exceptions.MaxConnectionsError,
    ):
        raise
    except (exceptions.ConnectionError, exceptions.TimeoutError) as exc:
        # redis-py 6.4.0 has several non-transient subclasses of
        # ConnectionError. Only the two exact transport exception classes
        # are allowed to defer slot ownership to PostgreSQL.
        if type(exc) not in (exceptions.ConnectionError, exceptions.TimeoutError):
            raise
        metric_job_key = (
            "worker_daily_tasks"
            if job_key == "daily"
            else "worker_cycle_participation_daily"
        )
        WORKER_REDIS_FAIL_OPEN.labels(metric_job_key).inc()
        log.warning(
            "redis_lock_unavailable job=%s slot=%s error_type=%s",
            metric_job_key,
            scheduled_for.isoformat(),
            type(exc).__name__,
        )
        return True
    return bool(acquired)


def acquire_daily_lock(scheduled_for: datetime | None = None) -> bool:
    scheduled_for = scheduled_for or datetime.now(timezone.utc)
    return _acquire_slot_lock("daily", scheduled_for)


def acquire_cycle_lock(scheduled_for: datetime | None = None) -> bool:
    scheduled_for = scheduled_for or datetime.now(timezone.utc)
    return _acquire_slot_lock("cycle-daily", scheduled_for)


def _record_daily_result(result: dict) -> None:
    if result.get("executed") is True and result.get("scheduler_run") == "SUCCEEDED":
        WORKER_RUNS.labels("success").inc()
    else:
        WORKER_RUNS.labels("claim_rejected").inc()


def _record_task_failure(job_key: str, exc: Exception) -> None:
    WORKER_RUNS.labels("failure").inc()
    if isinstance(exc, DBAPIError):
        WORKER_POSTGRES_FAILURES.labels(job_key).inc()


def _start_metrics_server() -> None:
    # Internal container port only; Compose does not publish it on the host.
    start_http_server(8001, addr="0.0.0.0")


def main():
    while True:
        WORKER_HEARTBEAT.set(time.time())
        if redis is not None:
            try:
                redis.from_url(settings.redis_url, decode_responses=True).set("frcaixinha:worker:heartbeat", str(time.time()), ex=300)
            except Exception:
                log.exception("worker_heartbeat_failed")
        scheduled_for = datetime.now(timezone.utc)
        # Run once shortly after 00:05 UTC; lock makes it single-run across replicas.
        if scheduled_for.hour == 0 and scheduled_for.minute == 5:
            try:
                if acquire_daily_lock(scheduled_for):
                    WORKER_DISPATCH_ATTEMPTS.labels("worker_daily_tasks").inc()
                    _record_daily_result(run_daily_tasks(scheduled_for=scheduled_for))
                else:
                    WORKER_DISPATCH_SKIPPED.labels(
                        "worker_daily_tasks", "redis_lock_occupied"
                    ).inc()
            except Exception as exc:
                _record_task_failure("worker_daily_tasks", exc)
                log.exception("scheduled_task_failed")
        # 04:05 UTC is 01:05 in America/Belem, after the financial day changes.
        if scheduled_for.hour == 4 and scheduled_for.minute == 5:
            try:
                if acquire_cycle_lock(scheduled_for):
                    WORKER_DISPATCH_ATTEMPTS.labels(
                        "worker_cycle_participation_daily"
                    ).inc()
                    result = run_cycle_participation_tasks(scheduled_for=scheduled_for)
                    log.info("cycle_participation_tasks_completed %s", result)
                else:
                    WORKER_DISPATCH_SKIPPED.labels(
                        "worker_cycle_participation_daily", "redis_lock_occupied"
                    ).inc()
            except Exception as exc:
                _record_task_failure("worker_cycle_participation_daily", exc)
                log.exception("cycle_participation_tasks_failed")
        time.sleep(30)

if __name__ == "__main__":
    _start_metrics_server()
    main()
