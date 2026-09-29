from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

# Both scheduler jobs recur daily. From any worker start, the next slot is
# less than 24 hours away; add the existing six-hour operational tolerance.
WORKER_FIRST_SUCCESS_GRACE_SECONDS = 24 * 60 * 60 + 6 * 60 * 60

HTTP_REQUESTS = Counter('frcaixinha_http_requests_total','Total HTTP requests',['method','path','status'])
HTTP_LATENCY = Histogram('frcaixinha_http_request_duration_seconds','HTTP request latency',['method','path'])
PIX_CREATED = Counter('frcaixinha_pix_created_total','Pix payment attempts created',['kind'])
PIX_APPROVED = Counter('frcaixinha_pix_approved_total','Pix payments approved',['kind'])
PIX_FAILED = Counter('frcaixinha_pix_failed_total','Pix payments failed',['kind'])
WEBHOOK_RECEIVED = Counter('frcaixinha_webhooks_received_total','Mercado Pago webhooks received',['status'])
WEBHOOK_DUPLICATE = Counter('frcaixinha_webhooks_duplicate_total','Duplicate webhooks ignored')
LOANS_APPROVED = Counter('frcaixinha_loans_approved_total','Loans approved')
INSTALLMENTS_PAID = Counter('frcaixinha_installments_paid_total','Loan installments fully paid')
INSTALLMENTS_OVERDUE = Gauge('frcaixinha_installments_overdue','Current overdue installments')
WORKER_HEARTBEAT = Gauge(
    'frcaixinha_worker_heartbeat_timestamp_seconds',
    'Worker process last loop iteration Unix timestamp',
)
WORKER_PROCESS_START_TIMESTAMP = Gauge(
    'frcaixinha_worker_process_start_timestamp_seconds',
    'Unix timestamp when this worker process initialized its entrypoint',
)
WORKER_RUNS = Counter(
    'frcaixinha_worker_runs_total',
    'Daily task outcomes; success means the durable run committed SUCCEEDED',
    ['status'],
)
WORKER_DISPATCH_ATTEMPTS = Counter(
    'frcaixinha_worker_dispatch_attempts_total',
    'Current-slot dispatches submitted to the task and PostgreSQL claim path',
    ['job_key'],
)
WORKER_DISPATCH_SKIPPED = Counter(
    'frcaixinha_worker_dispatch_skipped_total',
    'Current slots skipped before task dispatch',
    ['job_key', 'reason'],
)
WORKER_REDIS_FAIL_OPEN = Counter(
    'frcaixinha_worker_redis_fail_open_total',
    'Current-slot dispatches allowed to proceed after an approved Redis transport failure',
    ['job_key'],
)
WORKER_POSTGRES_FAILURES = Counter(
    'frcaixinha_worker_postgres_failures_total',
    'Scheduler task attempts that failed with a SQLAlchemy database error',
    ['job_key'],
)

SCHEDULER_RUNS_LAST_SUCCEEDED = Gauge(
    'frcaixinha_scheduler_runs_last_succeeded_timestamp_seconds',
    'Unix timestamp of the latest durably SUCCEEDED run for each scheduler job; zero means none',
    ['job_key'],
)
SCHEDULER_RUNS_FAILED = Gauge(
    'frcaixinha_scheduler_runs_failed',
    'Number of durably FAILED scheduler runs for each job',
    ['job_key'],
)
SCHEDULER_RUNS_EXPIRED_LEASE = Gauge(
    'frcaixinha_scheduler_runs_expired_lease',
    'Number of RUNNING scheduler runs with an expired lease for each job',
    ['job_key'],
)
SCHEDULER_RUNS_PENDING = Gauge(
    'frcaixinha_scheduler_runs_pending',
    'Number of PENDING scheduler runs for each job',
    ['job_key'],
)
SCHEDULER_METRICS_DATABASE_UP = Gauge(
    'frcaixinha_scheduler_metrics_database_up',
    'Whether the latest scheduler metrics refresh could query PostgreSQL',
)


def metrics_response():
    return generate_latest(), CONTENT_TYPE_LATEST
