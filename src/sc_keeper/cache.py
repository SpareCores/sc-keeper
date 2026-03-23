from fastapi import Response
from starlette.middleware.base import BaseHTTPMiddleware


class CacheHeaderMiddleware(BaseHTTPMiddleware):
    """Sets Cache-Control HTTP header."""

    async def dispatch(self, request, call_next):
        response: Response = await call_next(request)
        # default 1 hour
        ttl = 60 * 60
        # lower TTL for server prices
        if "server" in request.url.path and "prices" in request.url.path:
            ttl = 60 * 15
        # skip cache for authenticated endpoints, a few specific paths, and all error responses
        if (
            getattr(request.state, "auth_required", False)
            or request.url.path in ["/healthcheck"]
            or "/ai/assist" in request.url.path
            or response.status_code in [429, 500, 502, 503, 504]
        ):
            response.headers["Cache-Control"] = "private, no-store"
            ttl = 0
        if ttl > 0:
            # allow serving stale content while revalidating in the background
            stale_ttl = max(60 * 15, int(ttl * 0.25))
            # bridge short (max 30 mins) downtime by serving stale content while the cluster gets back online
            error_ttl = 60 * 30
            response.headers["Cache-Control"] = (
                f"public, max-age={ttl}, stale-while-revalidate={stale_ttl}, stale-if-error={error_ttl}"
            )
        return response
