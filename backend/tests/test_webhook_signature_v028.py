import hashlib
import hmac
from app.services.webhook import validate_mercado_pago_signature

def sign(secret, data_id, request_id, ts):
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    return hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()

def test_signature_accepts_fresh_request():
    secret = "secret"
    ts = 1_700_000_000
    sig = sign(secret, "123", "req-1", ts)
    assert validate_mercado_pago_signature(f"ts={ts},v1={sig}", "req-1", "123", secret, max_age_seconds=300, now=ts+10)

def test_signature_rejects_stale_request():
    secret = "secret"
    ts = 1_700_000_000
    sig = sign(secret, "123", "req-1", ts)
    assert not validate_mercado_pago_signature(f"ts={ts},v1={sig}", "req-1", "123", secret, max_age_seconds=300, now=ts+301)

def test_signature_rejects_invalid_timestamp():
    assert not validate_mercado_pago_signature("ts=abc,v1=deadbeef", "req-1", "123", "secret", now=1700000000)
