from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings

class RedisRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only throttle authentication and payment entry points.
        if request.url.path not in {"/api/auth/login", "/api/auth/register", "/api/auth/password-reset/request", "/api/payments/webhook/mercado-pago"}:
            return await call_next(request)
        try:
            import redis
            r = redis.from_url(settings.redis_url, decode_responses=True)
            ip = request.client.host if request.client else "unknown"
            key = f"frcaixinha:rl:{request.url.path}:{ip}"
            count = r.incr(key)
            if count == 1: r.expire(key, 60)
            if count > settings.rate_limit_per_minute:
                return JSONResponse({"detail":"Muitas requisições. Tente novamente em instantes."}, status_code=429,
                                    headers={"Retry-After":"60"})
        except Exception:
            # Availability first: Redis outage must not make the API unavailable.
            pass
        return await call_next(request)
