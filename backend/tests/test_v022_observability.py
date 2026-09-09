def test_metrics_module_exposes_expected_metrics():
    from app.core.metrics import HTTP_REQUESTS, HTTP_LATENCY, WORKER_HEARTBEAT
    assert HTTP_REQUESTS is not None
    assert HTTP_LATENCY is not None
    assert WORKER_HEARTBEAT is not None

def test_prometheus_and_alertmanager_configs_exist():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert (root / 'ops/prometheus.yml').exists()
    assert (root / 'ops/prometheus/rules/frcaixinha.yml').exists()
    assert (root / 'ops/alertmanager/alertmanager.yml').exists()
