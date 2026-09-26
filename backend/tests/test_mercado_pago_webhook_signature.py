import hashlib
import hmac

from app.services.webhook import validate_mercado_pago_signature


SECRET = "test-webhook-secret"
REQUEST_ID = "request-123"
DATA_ID = "order-456"
NOW = 1_790_440_000


def _signature(ts: str) -> str:
    manifest = f"id:{DATA_ID};request-id:{REQUEST_ID};ts:{ts};"
    digest = hmac.new(
        SECRET.encode(),
        manifest.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"ts={ts},v1={digest}"


def test_accepts_timestamp_in_seconds():
    ts = str(NOW)
    assert validate_mercado_pago_signature(
        _signature(ts),
        REQUEST_ID,
        DATA_ID,
        SECRET,
        max_age_seconds=300,
        now=NOW,
    )


def test_accepts_timestamp_in_milliseconds_without_changing_hmac_manifest():
    ts = str(NOW * 1000)
    assert validate_mercado_pago_signature(
        _signature(ts),
        REQUEST_ID,
        DATA_ID,
        SECRET,
        max_age_seconds=300,
        now=NOW,
    )


def test_rejects_stale_timestamp_in_milliseconds():
    ts = str((NOW - 301) * 1000)
    assert not validate_mercado_pago_signature(
        _signature(ts),
        REQUEST_ID,
        DATA_ID,
        SECRET,
        max_age_seconds=300,
        now=NOW,
    )
