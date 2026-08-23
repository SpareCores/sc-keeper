import hashlib
import logging
import re
import time
from collections import OrderedDict
from json import dumps as json_dumps
from json import loads as json_loads
from os import environ
from threading import Lock
from typing import Any, Awaitable, Callable, Optional

import httpx
from fastapi import HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from starlette.middleware.base import BaseHTTPMiddleware

from .redis_client import get_redis_client

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

_introspection_regex: Optional[re.Pattern[str]] = None
_jwt_regex: Optional[re.Pattern[str]] = None
_api_key_regex: Optional[re.Pattern[str]] = None

_jwks_keys: dict[str, Any] = {}
_jwks_url: Optional[str] = None
_jwks_fetched_at: float = 0.0
_jwks_lock = Lock()
_jwks_cache_ttl = int(environ.get("AUTH_JWT_JWKS_CACHE_TTL_SECONDS", "300"))


class User(BaseModel):
    """User object extracted from a verified Bearer token."""

    model_config = ConfigDict(extra="allow")

    user_id: str
    api_credits_per_minute: Optional[int] = None


_static_tokens: dict[str, User] = {}


# L1 (in-memory, per-process) cache for token validation results
_token_cache_l1: OrderedDict[str, tuple[Optional[User], float]] = OrderedDict()
_token_cache_l1_lock = Lock()
_token_cache_l1_ttl = int(environ.get("AUTH_TOKEN_CACHE_L1_TTL_SECONDS", "60"))
_token_cache_l1_max_size = int(environ.get("AUTH_TOKEN_CACHE_L1_MAX_SIZE", "1000"))
# L2 (redis, shared across workers) cache
_token_cache_l2_ttl = int(environ.get("AUTH_TOKEN_CACHE_L2_TTL_SECONDS", "300"))


def _compile_optional_regex(env_var: str) -> Optional[re.Pattern[str]]:
    pattern = environ.get(env_var)
    if not pattern:
        return None
    return re.compile(pattern)


def _introspection_enabled() -> bool:
    return bool(environ.get("AUTH_TOKEN_INTROSPECTION_URL"))


def _jwt_enabled() -> bool:
    return bool(environ.get("AUTH_JWT_JWKS_URL") or environ.get("AUTH_JWT_PUBLIC_KEY"))


def _api_key_enabled() -> bool:
    return bool(environ.get("AUTH_API_KEY_VERIFY_URL"))


def _static_tokens_enabled() -> bool:
    return bool(environ.get("AUTH_STATIC_TOKENS"))


def _require_jwt_deps() -> None:
    try:
        import jwt  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "PyJWT is required for JWT Bearer verification. "
            "Install with: pip install sparecores-keeper[security]"
        ) from exc


def _load_static_tokens() -> dict[str, User]:
    """Parse AUTH_STATIC_TOKENS JSON into a token → User map."""
    raw = environ.get("AUTH_STATIC_TOKENS")
    if not raw:
        return {}

    try:
        entries = json_loads(raw)
    except Exception as exc:
        raise ValueError(f"AUTH_STATIC_TOKENS must be valid JSON: {exc}") from exc

    if not isinstance(entries, list):
        raise ValueError("AUTH_STATIC_TOKENS must be a JSON array")

    tokens: dict[str, User] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"AUTH_STATIC_TOKENS[{i}] must be an object")
        token = entry.get("token")
        subject = entry.get("subject")
        if not token or not subject:
            raise ValueError(
                f"AUTH_STATIC_TOKENS[{i}] requires non-empty 'token' and 'subject'"
            )
        if token in tokens:
            raise ValueError(f"AUTH_STATIC_TOKENS has duplicate token at index {i}")
        # provider subject: not necessarily a user id, might be organization id etc.
        tokens[token] = User(
            user_id=subject,
            api_credits_per_minute=entry.get("api_credits_per_minute"),
            token_source="static_token",
        )
    return tokens


def validate_auth_config() -> None:
    """Validate auth env vars and compile optional token regexes. Call at startup."""
    global _introspection_regex, _jwt_regex, _api_key_regex, _static_tokens

    missing_vars = []
    if _introspection_enabled():
        for var in ("AUTH_CLIENT_ID", "AUTH_CLIENT_SECRET"):
            if not environ.get(var):
                missing_vars.append(var)
    if _api_key_enabled() and not environ.get("AUTH_API_KEY_VERIFY_BEARER"):
        missing_vars.append("AUTH_API_KEY_VERIFY_BEARER")
    if missing_vars:
        raise ValueError(
            "The following environment variables are required for the enabled "
            f"auth method(s): {', '.join(missing_vars)}"
        )

    if _jwt_enabled():
        _require_jwt_deps()

    try:
        _introspection_regex = _compile_optional_regex("AUTH_TOKEN_INTROSPECTION_REGEX")
        _jwt_regex = _compile_optional_regex("AUTH_JWT_TOKEN_REGEX")
        _api_key_regex = _compile_optional_regex("AUTH_API_KEY_TOKEN_REGEX")
    except re.error as exc:
        raise ValueError(f"Invalid auth token regex: {exc}") from exc

    _static_tokens = _load_static_tokens()


def token_verification_enabled() -> bool:
    """Check if any Bearer token verification method is enabled."""
    return (
        _introspection_enabled()
        or _jwt_enabled()
        or _api_key_enabled()
        or _static_tokens_enabled()
    )


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

        # flag that token was cached in L1
        user.token_source = "l1_cache"

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
        if not cached_data:
            return None
        user_data = json_loads(cached_data)
        user_data["token_source"] = "l2_cache"
        return User.model_validate(user_data)
    except Exception as e:
        logger.debug(f"Error reading from Redis cache: {e}")
    return None


def _cache_token_user_l2(cache_key: str, user: User, redis_client) -> None:
    """Cache token user in L2 (Redis) cache."""
    try:
        user_data = json_dumps(user.model_dump())
        redis_client.setex(f"token:{cache_key}", _token_cache_l2_ttl, user_data)
    except Exception as e:
        logger.debug(f"Error writing to Redis cache: {e}")


async def _verify_introspection(token: str) -> Optional[User]:
    """Verify token via RFC 7662 token introspection."""
    api_url = environ.get("AUTH_TOKEN_INTROSPECTION_URL")
    if not api_url:
        return None

    try:
        # RFC 7662 token introspection such as https://zitadel.com/docs/guides/integrate/token-introspection/basic-auth
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

            if bool(user_data.get("active", False)) is not True:
                logger.warning("Token is not active")
                return None

            user_id = user_data.get("sub")
            if not user_id:
                logger.warning("No user ID found in introspection response")
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

            user_data_extra = {}
            extra_fields_cel = environ.get("AUTH_TOKEN_EXTRA_FIELDS_CEL")
            if extra_fields_cel:
                try:
                    from cel import evaluate

                    extra_fields = evaluate(extra_fields_cel, {"claims": user_data})
                    assert isinstance(
                        extra_fields,
                        dict,
                    ), "Extra token fields CEL expression must return a dict"
                    user_data_extra.update(**extra_fields)
                except Exception:
                    logger.exception(
                        "Error extracting dict via CEL expression for extra token fields"
                    )

            return User(
                user_id=user_id,
                api_credits_per_minute=user_data.get("api_credits_per_minute"),
                token_source="oauth2_introspection",
                **user_data_extra,
            )
    except Exception:
        logger.exception("Error verifying token via introspection")
        return None


async def _load_jwks_keys() -> dict[str, Any]:
    """Load JWKS keys keyed by kid, refreshing from the endpoint on a TTL."""
    global _jwks_keys, _jwks_url, _jwks_fetched_at

    from jwt import PyJWK

    jwks_url = environ.get("AUTH_JWT_JWKS_URL")
    if not jwks_url:
        return {}

    with _jwks_lock:
        if (
            _jwks_url == jwks_url
            and _jwks_keys
            and time.time() - _jwks_fetched_at < _jwks_cache_ttl
        ):
            return _jwks_keys

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            jwks = response.json()
    except Exception:
        # serve the stale cache on a failed refresh, but stamp the cache time so
        # the next requests wait out the TTL instead of hammering a down endpoint
        with _jwks_lock:
            if _jwks_url == jwks_url and _jwks_keys:
                _jwks_fetched_at = time.time()
                logger.warning("Failed to refresh JWKS, serving cached keys")
                return _jwks_keys
        raise

    keys: dict[str, Any] = {}
    for key_data in jwks.get("keys", []):
        kid = key_data.get("kid")
        if not kid:
            continue
        keys[kid] = PyJWK(key_data)

    with _jwks_lock:
        _jwks_keys = keys
        _jwks_url = jwks_url
        _jwks_fetched_at = time.time()
        return _jwks_keys


async def _verify_jwt(token: str) -> Optional[User]:
    """Verify a signed JWT via JWKS or a static public key."""
    if not _jwt_enabled():
        return None

    import jwt

    decode_kwargs: dict[str, Any] = {"algorithms": ["RS256", "ES256"]}
    issuer = environ.get("AUTH_JWT_ISSUER")
    if issuer:
        decode_kwargs["issuer"] = issuer
    audience = environ.get("AUTH_JWT_AUDIENCE")
    if audience:
        decode_kwargs["audience"] = [part.strip() for part in audience.split(",")]

    try:
        public_key = environ.get("AUTH_JWT_PUBLIC_KEY")
        if public_key:
            signing_key = public_key
        else:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if not kid:
                logger.warning("JWT missing kid header")
                return None

            keys = await _load_jwks_keys()
            jwk = keys.get(kid)
            if jwk is None:
                logger.warning("JWT kid not found in JWKS")
                return None
            signing_key = jwk.key

        payload = jwt.decode(token, signing_key, **decode_kwargs)

        authorized_parties = environ.get("AUTH_JWT_AUTHORIZED_PARTIES")
        if authorized_parties:
            allowed = {part.strip() for part in authorized_parties.split(",")}
            azp = payload.get("azp")
            if azp not in allowed:
                logger.warning("JWT azp not in authorized parties allowlist")
                return None

        # provider subject: not necessarily a user id, might be organization id etc.
        user_id = payload.get("sub")
        if not user_id:
            logger.warning("No sub claim found in JWT")
            return None

        return User(
            user_id=user_id,
            api_credits_per_minute=None,
            token_source="jwt_jwks",
        )
    except Exception:
        logger.exception("Error verifying JWT")
        return None


async def _verify_api_key(token: str) -> Optional[User]:
    """Verify an opaque API key via a remote verify endpoint."""
    api_url = environ.get("AUTH_API_KEY_VERIFY_URL")
    if not api_url:
        return None

    request_field = environ.get("AUTH_API_KEY_VERIFY_REQUEST_FIELD", "secret")
    subject_field = environ.get("AUTH_API_KEY_VERIFY_SUBJECT_FIELD", "subject")
    claims_field = environ.get("AUTH_API_KEY_VERIFY_CLAIMS_FIELD", "claims")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {environ['AUTH_API_KEY_VERIFY_BEARER']}"
                },
                json={request_field: token},
            )
            if response.status_code >= 400:
                logger.warning("API key verification rejected")
                return None

            data = response.json()
            # provider subject: not necessarily a user id, might be organization id etc.
            user_id = data.get(subject_field)
            if not user_id:
                logger.warning("No subject found in API key verification response")
                return None

            claims = data.get(claims_field) or {}
            if not isinstance(claims, dict):
                logger.warning("API key claims field is not a dict")
                return None

            claims.pop("user_id", None)
            claims.pop("token_source", None)
            return User(
                user_id=user_id,
                token_source="api_key_verify",
                **claims,
            )
    except Exception:
        logger.exception("Error verifying API key")
        return None


async def _verify_static_token(token: str) -> Optional[User]:
    """Verify a Bearer token against the static allowlist."""
    user = _static_tokens.get(token)
    if user is None:
        return None
    return user.model_copy()


def _verification_candidates(
    token: str,
) -> list[Callable[[str], Awaitable[Optional[User]]]]:
    """Build ordered verifier list: regex matches, then catchalls, then static tokens."""
    verifiers: list[tuple[Optional[re.Pattern[str]], bool, Callable]] = [
        (_api_key_regex, _api_key_enabled(), _verify_api_key),
        (_jwt_regex, _jwt_enabled(), _verify_jwt),
        (_introspection_regex, _introspection_enabled(), _verify_introspection),
    ]

    candidates: list[Callable[[str], Awaitable[Optional[User]]]] = []
    for regex, enabled, verify_fn in verifiers:
        if enabled and regex is not None and regex.search(token):
            candidates.append(verify_fn)
    for regex, enabled, verify_fn in verifiers:
        if enabled and regex is None:
            candidates.append(verify_fn)
    if _static_tokens_enabled():
        candidates.append(_verify_static_token)
    return candidates


async def verify_token(token: str) -> Optional[User]:
    """
    Verify a Bearer token using enabled verification methods with two-tier caching.

    Supports RFC 7662 introspection, JWT Bearer validation (JWKS/static key),
    remote opaque API-key verification, and a static token allowlist. Methods
    are tried sequentially until one succeeds.
    """
    if not token_verification_enabled():
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

    # all caches missed, try enabled verifiers until one returns a User
    for verify_fn in _verification_candidates(token):
        user = await verify_fn(token)
        if user is not None:
            _cache_token_user_l1(cache_key, user)
            if redis_client:
                _cache_token_user_l2(cache_key, user, redis_client)
            return user

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
    # flag that this endpoint requires authentication for cache middleware to skip caching
    request.state.auth_required = True

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


validate_auth_config()
