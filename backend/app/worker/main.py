import logging, time
from datetime import datetime, timezone
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.worker.tasks import run_daily_tasks, run_cycle_participation_tasks
from app.core.metrics import WORKER_HEARTBEAT, WORKER_RUNS

configure_logging()
log = logging.getLogger("worker")

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
        log.warning(
            "redis_lock_unavailable job=%s slot=%s error_type=%s",
            "worker_daily_tasks" if job_key == "daily" else "worker_cycle_participation_daily",
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
        if scheduled_for.hour == 0 and scheduled_for.minute == 5 and acquire_daily_lock(scheduled_for):
            try:
                run_daily_tasks(scheduled_for=scheduled_for); WORKER_RUNS.labels("success").inc()
            except Exception:
                WORKER_RUNS.labels("failure").inc(); log.exception("scheduled_task_failed")
        # 04:05 UTC is 01:05 in America/Belem, after the financial day changes.
        if scheduled_for.hour == 4 and scheduled_for.minute == 5 and acquire_cycle_lock(scheduled_for):
            try:
                result = run_cycle_participation_tasks(scheduled_for=scheduled_for)
                log.info("cycle_participation_tasks_completed %s", result)
            except Exception:
                log.exception("cycle_participation_tasks_failed")
        time.sleep(30)

if __name__ == "__main__": main()
