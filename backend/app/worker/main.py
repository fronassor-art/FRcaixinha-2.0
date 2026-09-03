import logging, time
from datetime import datetime, timezone
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.worker.tasks import run_daily_tasks
from app.core.metrics import WORKER_HEARTBEAT, WORKER_RUNS

configure_logging()
log = logging.getLogger("worker")

try:
    import redis
except ImportError:
    redis = None

def acquire_daily_lock():
    if redis is None:
        return True
    r = redis.from_url(settings.redis_url, decode_responses=True)
    key = f"frcaixinha:daily:{datetime.now(timezone.utc).date().isoformat()}"
    return bool(r.set(key, "1", nx=True, ex=86400))

def main():
    while True:
        WORKER_HEARTBEAT.set(time.time())
        if redis is not None:
            try:
                redis.from_url(settings.redis_url, decode_responses=True).set("frcaixinha:worker:heartbeat", str(time.time()), ex=300)
            except Exception:
                log.exception("worker_heartbeat_failed")
        now = datetime.now(timezone.utc)
        # Run once shortly after 00:05 UTC; lock makes it single-run across replicas.
        if now.hour == 0 and now.minute == 5 and acquire_daily_lock():
            try:
                run_daily_tasks(); WORKER_RUNS.labels("success").inc()
            except Exception:
                WORKER_RUNS.labels("failure").inc(); log.exception("scheduled_task_failed")
        time.sleep(30)

if __name__ == "__main__": main()
