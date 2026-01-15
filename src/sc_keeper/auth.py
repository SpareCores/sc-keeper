import hashlib
import logging
import time
from collections import OrderedDict
from json import dumps as json_dumps
from json import loads as json_loads
from os import environ
from threading import Lock
from typing import Optional

import httpx
from fastapi import HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .redis_client import get_redis_client

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


class User(BaseModel):
    """User object extracted from OAuth 2.0 token introspection."""

    user_id: str
    api_credits_per_minute: Optional[int] = None


# L1 (in-memory, per-process) cache for token validation results
_token_cache_l1: OrderedDict[str, tuple[Optional[User], float]] = OrderedDict()
_token_cache_l1_lock = Lock()
_token_cache_l1_ttl = int(environ.get("AUTH_TOKEN_CACHE_L1_TTL_SECONDS", "60"))
_token_cache_l1_max_size = int(environ.get("AUTH_TOKEN_CACHE_L1_MAX_SIZE", "1000"))
# L2 (redis, shared across workers) cache
_token_cache_l2_ttl = int(environ.get("AUTH_TOKEN_CACHE_L2_TTL_SECONDS", "300"))


def _get_token_cache_key(token: str) -> str:
    """Generate a hashed cache key from the token with optional salt."""
    salt = environ.get("AUTH_TOKEN_CACHE_SALT", "").encode()
    return hashlib.sha256(token.encode() + salt).hexdigest()


def _get_cached_token_user_l1(cache_key: str) -> Optional[User]:
    """Get cached token user from L1 (in-memory) cache if still valid."""
    with _token_cache_l1_lock:
        if cache_key not in _token_cache_l1:
            return None

        user, cached_time = _token_cache_l1[cache_key]
        if time.time() - cached_time > _token_cache_l1_ttl:
            _token_cache_l1.pop(cache_key, None)  # expired
            return None

        # mark key as recently used not to be evicted when max size reached
        _token_cache_l1.move_to_end(cache_key)
        return user


def _cache_token_user_l1(cache_key: str, user: User) -> None:
    """Cache token user in L1 (in-memory) cache with TTL and housekeeping."""
    with _token_cache_l1_lock:
        # remove expired entries
        current_time = time.time()
        expired_keys = [
            key
            for key, (_, cached_time) in _token_cache_l1.items()
            if current_time - cached_time > _token_cache_l1_ttl
        ]
        for key in expired_keys:
            _token_cache_l1.pop(key, None)

        # enforce max size
        while len(_token_cache_l1) >= _token_cache_l1_max_size:
            _token_cache_l1.popitem(last=False)

        # add/update cache entry
        _token_cache_l1[cache_key] = (user, current_time)


def _get_cached_token_user_l2(cache_key: str, redis_client) -> Optional[User]:
    """Get cached token user from L2 (Redis) cache."""
    try:
        cached_data = redis_client.get(f"token:{cache_key}")
        if cached_data:
            user_data = json_loads(cached_data)
            return User(
                user_id=user_data["user_id"],
                api_credits_per_minute=user_data.get("api_credits_per_minute"),
            )
    except Exception as e:
        logger.debug(f"Error reading from Redis cache: {e}")
    return None


def _cache_token_user_l2(cache_key: str, user: User, redis_client) -> None:
    """Cache token user in L2 (Redis) cache."""
    try:
        user_data = json_dumps(
            {
                "user_id": user.user_id,
                "api_credits_per_minute": user.api_credits_per_minute,
            }
        )
        redis_client.setex(f"token:{cache_key}", _token_cache_l2_ttl, user_data)
    except Exception as e:
        logger.debug(f"Error writing to Redis cache: {e}")


def token_verification_enabled() -> bool:
    """Check if token verification is enabled via env var."""
    return bool(environ.get("AUTH_TOKEN_INTROSPECTION_URL"))


async def verify_token(token: str) -> Optional[User]:
    """
    Verify OAuth 2.0 token (access token or PAT) via token introspection API with two-tier caching.
    Works for both access tokens from frontend (humans) and personal access tokens (service users).
    Supports any OAuth 2.0-compatible identity provider that implements token introspection.
    """
    api_url = environ.get("AUTH_TOKEN_INTROSPECTION_URL")
    if not api_url:
        return None

    cache_key = _get_token_cache_key(token)

    # check in-memory cache first
    cached_user = _get_cached_token_user_l1(cache_key)
    if cached_user is not None:
        return cached_user

    # check Redis cache
    redis_client = None
    if environ.get("REDIS_URL"):
        try:
            redis_client = get_redis_client()
            cached_user = _get_cached_token_user_l2(cache_key, redis_client)
            if cached_user is not None:
                _cache_token_user_l1(cache_key, cached_user)
                return cached_user
        except Exception:
            logger.exception("Error getting cached token user from Redis")

    # all caches missed, validate with token introspection API
    try:
        # https://zitadel.com/docs/guides/integrate/token-introspection/basic-auth
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                api_url,
                auth=httpx.BasicAuth(
                    environ["AUTH_CLIENT_ID"], environ["AUTH_CLIENT_SECRET"]
                ),
                data={"token": token},
            )
            response.raise_for_status()
            user_data = response.json()
            user_id = user_data.get("sub")

            if not user_id:
                logger.warning("No user ID found in API response")
                return None

            rule = environ.get("AUTH_TOKEN_VALIDATION_CEL")
            if rule:
                try:
                    from cel import evaluate

                    token_valid = evaluate(rule, {"claims": user_data})
                    if not token_valid:
                        logger.warning("Token validation CEL rule not satisfied")
                        return None
                except Exception:
                    logger.exception("Error evaluating token validation CEL rule")
                    return None

            user = User(
                user_id=user_id,
                api_credits_per_minute=user_data.get("api_credits_per_minute"),
            )
            _cache_token_user_l1(cache_key, user)
            if redis_client:
                _cache_token_user_l2(cache_key, user, redis_client)
            return user
    except Exception:
        logger.exception("Error verifying token")
        return None


async def extract_user_from_request(request) -> Optional[User]:
    """Extract user from request Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1]
    return await verify_token(token)


async def current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> User:
    """FastAPI dependency to require authentication and return the current user.

    Uses request.state.user (populated by AuthMiddleware). The credentials parameter
    is only present for FastAPI to detect the security scheme in OpenAPI docs.

    Raises: HTTPException(401) if user is not authenticated.
    """
    # AuthMiddleware always sets request.state.user (even if None)
    user = getattr(request.state, "user", None)
    if user:
        return user

    # if no User found, middleware already tried and failed
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that extracts and stores user info early in the request lifecycle."""

    async def dispatch(self, request, call_next):
        request.state.user = await extract_user_from_request(request)
        response = await call_next(request)
        return response


class AuthGuardMiddleware(BaseHTTPMiddleware):
    """Middleware that returns 401 error if token was provided but validation failed."""

    async def dispatch(self, request, call_next):
        if (
            token_verification_enabled()
            and bool(request.headers.get("Authorization"))
            and not bool(request.state.user)
        ):
            return Response(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content='{"detail":"Invalid or expired token"}',
                headers={
                    "Content-Type": "application/json",
                    "WWW-Authenticate": "Bearer",
                },
            )
        response = await call_next(request)
        return response
