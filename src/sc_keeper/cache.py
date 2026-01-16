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
            ttl = 60 * 10
        # skip cache for authenticated endpoints and a few specific paths
        if (
            getattr(request.state, "auth_required", False)
            or request.url.path in ["/healthcheck"]
            or "/ai/assist" in request.url.path
        ):
            response.headers["Cache-Control"] = "private, no-store"
            ttl = 0
        if ttl > 0:
            response.headers["Cache-Control"] = f"public, max-age={ttl}"
        return response
