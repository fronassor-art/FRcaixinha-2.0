import hashlib
import hmac
import time
from app.services.webhook import validate_mercado_pago_signature

def test_mercado_pago_signature():
    secret = "secret"
    ts = str(int(time.time()))
    data_id = "999999"
    request_id = "req-123"
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    header = f"ts={ts},v1={digest}"
    assert validate_mercado_pago_signature(header, request_id, data_id, secret)
    assert not validate_mercado_pago_signature(header, "wrong", data_id, secret)
