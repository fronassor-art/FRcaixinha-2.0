from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

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
WORKER_HEARTBEAT = Gauge('frcaixinha_worker_heartbeat_timestamp_seconds','Worker last heartbeat Unix timestamp')
WORKER_RUNS = Counter('frcaixinha_worker_runs_total','Worker daily task runs',['status'])


def metrics_response():
    return generate_latest(), CONTENT_TYPE_LATEST
