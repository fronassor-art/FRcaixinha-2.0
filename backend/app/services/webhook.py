import hashlib
import hmac
import time

def parse_signature(header: str | None):
    result = {}
    if not header:
        return result
    for item in header.split(","):
        key, sep, value = item.partition("=")
        if sep:
            result[key.strip()] = value.strip()
    return result

def validate_mercado_pago_signature(
    x_signature: str | None,
    x_request_id: str | None,
    data_id: str | None,
    secret: str | None,
    max_age_seconds: int = 300,
    now: int | None = None,
) -> bool:
    if not secret or not x_signature:
        return False
    parts = parse_signature(x_signature)
    ts = parts.get("ts")
    v1 = parts.get("v1")
    if not ts or not v1:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    current = int(time.time()) if now is None else int(now)
    if max_age_seconds >= 0 and abs(current - ts_int) > max_age_seconds:
        return False
    manifest_parts = []
    if data_id:
        manifest_parts.append(f"id:{data_id}")
    if x_request_id:
        manifest_parts.append(f"request-id:{x_request_id}")
    manifest_parts.append(f"ts:{ts}")
    manifest = ";".join(manifest_parts) + ";"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
