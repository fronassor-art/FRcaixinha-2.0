from fastapi import APIRouter, Response
from app.core.metrics import metrics_response, WORKER_HEARTBEAT
from app.core.config import settings
import time

router = APIRouter()

@router.get('/metrics', include_in_schema=False)
def metrics():
    try:
        import redis
        value = redis.from_url(settings.redis_url, decode_responses=True).get('frcaixinha:worker:heartbeat')
        if value:
            WORKER_HEARTBEAT.set(float(value))
    except Exception:
        # Metrics endpoint must remain available even if Redis is unavailable.
        pass
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type.split(';')[0])
