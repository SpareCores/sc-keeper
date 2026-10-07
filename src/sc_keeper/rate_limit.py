import logging
from collections import defaultdict
from math import ceil
from os import environ
from threading import Lock, Thread
from time import sleep, time
from typing import Optional
from uuid import uuid4

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from .logger import get_request_id
from .redis_client import get_redis_client

logger = logging.getLogger(__name__)

# default credits per minute when rate-limiting is enabled (see env vars)
DEFAULT_CREDITS_PER_MINUTE = 60
# default credit cost per request for routes not in CUSTOM_RATE_LIMIT_COSTS
DEFAULT_CREDIT_COST = int(environ.get("RATE_LIMIT_DEFAULT_CREDIT_COST", 1))
# custom credit costs per request path patterns, e.g.
# "/expensive"=5 means that a request to any endpoint starting with "/expensive" costs 5 credits
CUSTOM_RATE_LIMIT_COSTS: dict[str, int] = {
    "/servers": 3,
    "/databases": 3,
    "/server_prices": 5,
    "/table/server_prices": 10,
    "/table/database_price": 10,
    "/benchmark_score_stats": 10,
}
# penalty credits for 401 unauthorized responses
UNAUTHORIZED_PENALTY_CREDITS = 10


class RateLimiter:
    """Base class for rate limiters."""

    window_seconds: int = 60
    """The sliding window's length (in seconds) used for credit tracking."""

    @staticmethod
    def _retry_after(
        entries: list[tuple[float, int]],
        total_credits: int,
        limit: int,
        credit_cost: int,
        now: float,
        window_seconds: int,
    ) -> int:
        """Seconds until enough credits expire from the window to afford credit_cost.

        Keep in sync with the Lua implementation in RedisRateLimiter.
        Requests that can never be afforded (credit_cost > limit) are rejected by
        RateLimitMiddleware before calling the limiter.

        Args:
            entries: (timestamp, credits) pairs in the current window, oldest first
            total_credits: Sum of credits in entries
            limit: The credits per minute limit
            credit_cost: The credit cost of the rejected request
            now: Current timestamp
            window_seconds: The sliding window's length (in seconds)

        Returns:
            int: Seconds to wait (between 1 and the window length).
        """
        for timestamp, credits in entries:
            total_credits -= credits
            if total_credits + credit_cost <= limit:
                # clamp to the window length to be safe from clock skew
                wait = ceil(timestamp + window_seconds - now)
                return min(window_seconds, max(1, wait))
        return window_seconds


class InMemoryRateLimiter(RateLimiter):
    """Simple in-memory rate limiter using a sliding window with credit-based tracking."""

    def __init__(self, credits_per_minute: int):
        self.credits_per_minute = credits_per_minute
        self._lock = Lock()
        # credit consumption history: {key: [(timestamp, credits), ...]}
        self.windows: dict[str, list[tuple[float, int]]] = defaultdict(list)
        # background thread to evict keys whose entries have all expired,
        # preventing unbounded dict growth from unique IPs/users over time
        self._cleanup_thread = Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _cleanup_loop(self):
        """Periodically remove keys whose sliding-window entries have all expired."""
        while True:
            sleep(self.window_seconds)
            window_start = time() - self.window_seconds
            with self._lock:
                stale_keys = [
                    key
                    for key, entries in self.windows.items()
                    if not any(ts > window_start for ts, _ in entries)
                ]
                for key in stale_keys:
                    del self.windows[key]
            if stale_keys:
                logger.debug("Evicted %d stale rate-limit keys", len(stale_keys))

    def is_allowed(
        self,
        key: str,
        credits_per_minute: Optional[int] = None,
        credit_cost: int = 1,
        **kwargs,
    ) -> tuple[bool, int, int]:
        """
        Check if request is allowed based on recent credit consumption in the last minute.

        Args:
            key: The rate limit key (e.g., "user:123" or "ip:127.0.0.1")
            credits_per_minute: The credits per minute limit (optional, falls back to instance default)
            credit_cost: The credit cost per request
            **kwargs: Additional optional parameters (e.g., request_id, unused in in-memory implementation)

        Returns:
            tuple[bool, int, int]: (allowed, remaining_credits, retry_after_seconds),
                where retry_after_seconds is 0 when the request is allowed.
        """
        limit = credits_per_minute or self.credits_per_minute
        now = time()
        window_start = now - self.window_seconds

        with self._lock:
            # clean old entries
            self.windows[key] = [
                (timestamp, credits)
                for timestamp, credits in self.windows[key]
                if timestamp > window_start
            ]
            # calculate total credits consumed
            total_credits = sum(credits for _, credits in self.windows[key])
            if total_credits + credit_cost > limit:
                remaining = max(0, limit - total_credits)
                retry_after = self._retry_after(
                    self.windows[key],
                    total_credits,
                    limit,
                    credit_cost,
                    now,
                    self.window_seconds,
                )
                return False, remaining, retry_after
            # record current request's credit consumption
            self.windows[key].append((now, credit_cost))
            remaining = limit - (total_credits + credit_cost)

        return True, remaining, 0


class RedisRateLimiter(RateLimiter):
    """Redis-based rate limiter using sliding window with credit-based tracking."""

    RATE_LIMIT_SCRIPT = """
    -- use a sorted set for the sliding window as:
    -- score=timestamp, member="request_id:credit_cost"
    local key = KEYS[1]
    local window_start = tonumber(ARGV[1])
    local now = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local credit_cost = tonumber(ARGV[4])
    local member_id = ARGV[5]
    local window_seconds = tonumber(ARGV[6])

    -- drop old entries
    redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

    -- get all current entries (oldest first) as flat list of member, score
    local entries = redis.call('ZRANGE', key, 0, -1, 'WITHSCORES')
    -- calculate total credits consumed, keeping the parsed costs for reuse
    local costs = {}
    local total_credits = 0
    for i = 1, #entries, 2 do
        local cost = tonumber(string.match(entries[i], ':(%d+)$')) or 0
        costs[#costs + 1] = cost
        total_credits = total_credits + cost
    end

    -- early return if not enough credits left
    if total_credits + credit_cost > limit then
        -- find when enough of the oldest entries expire to afford this request
        -- (keep in sync with RateLimiter._retry_after)
        local retry_after = window_seconds
        local credits_left = total_credits
        for j, cost in ipairs(costs) do
            credits_left = credits_left - cost
            if credits_left + credit_cost <= limit then
                local ts = tonumber(entries[2 * j])
                -- clamp to the window length as entries might have been
                -- recorded by other workers with clock skew
                retry_after = math.min(
                    window_seconds,
                    math.max(1, math.ceil(ts + window_seconds - now))
                )
                break
            end
        end
        return {0, math.max(0, limit - total_credits), retry_after}
    end

    -- record current request's credit consumption
    redis.call('ZADD', key, now, member_id)
    redis.call('EXPIRE', key, window_seconds)
    return {1, limit - total_credits - credit_cost, 0}
    """

    def __init__(self, credits_per_minute: int):
        redis_client = get_redis_client()
        self.credits_per_minute = credits_per_minute
        self._rate_limiter = redis_client.register_script(self.RATE_LIMIT_SCRIPT)
        # fallback to in-memory limiter when Redis fails
        self._fallback_limiter = InMemoryRateLimiter(credits_per_minute)

    def is_allowed(
        self,
        key: str,
        credits_per_minute: Optional[int] = None,
        credit_cost: int = 1,
        **kwargs,
    ) -> tuple[bool, int, int]:
        """Check if request is allowed based on recent credit consumption in the last minute.

        Args:
            key: The rate limit key (e.g., "user:123" or "ip:127.0.0.1")
            credits_per_minute: The credits per minute limit (optional, falls back to instance default)
            credit_cost: The credit cost per request
            **kwargs: Additional optional parameters (e.g., request_id for uniqueness)

        Returns:
            tuple[bool, int, int]: (allowed, remaining_credits, retry_after_seconds),
                where retry_after_seconds is 0 when the request is allowed.
        """
        limit = credits_per_minute or self.credits_per_minute
        now = time()
        window_start = now - self.window_seconds
        # fall back to a random id also when request_id is explicitly None, otherwise
        # all such requests would share the same sorted set member and overwrite each other
        request_id = kwargs.get("request_id") or str(uuid4())
        member_id = f"{request_id}:{credit_cost}"
        redis_key = f"ratelimit:{key}"

        try:
            result = self._rate_limiter(
                keys=[redis_key],
                args=[
                    window_start,
                    now,
                    limit,
                    credit_cost,
                    member_id,
                    self.window_seconds,
                ],
            )
            return bool(result[0]), int(result[1]), int(result[2])
        except Exception:
            logger.exception(
                "Failed to check rate limit with Redis, falling back to in-memory limiter"
            )
            # fallback to in-memory limiter when Redis fails
            return self._fallback_limiter.is_allowed(
                key, credits_per_minute, credit_cost, **kwargs
            )


def _get_rate_limit_response_data(
    credits_per_minute: int,
    credit_cost: int,
    remaining_credits: Optional[int],
    retry_after: Optional[int],
) -> dict:
    """Get rate limit response data (status, headers, content) for reuse.

    Args:
        credits_per_minute: The credits per minute limit
        credit_cost: The credit cost of the request
        remaining_credits: Remaining credits, or None if not known
        retry_after: Seconds to wait before retrying, or None if retrying
            will never succeed (credit_cost > credits_per_minute).
    """
    headers = {
        # per-client response, must not be cached (CacheHeaderMiddleware does not
        # see this response as RateLimitMiddleware returns early)
        "Cache-Control": "private, no-store",
        "X-RateLimit-Limit": str(credits_per_minute),
        "X-RateLimit-Cost": str(credit_cost),
    }
    if remaining_credits is not None:
        headers["X-RateLimit-Remaining"] = str(remaining_credits)
    if retry_after is None:
        content = (
            f"Rate limit exceeded: request cost ({credit_cost} credits) is higher "
            f"than the credit limit ({credits_per_minute} credits per minute)."
        )
    else:
        content = "Rate limit exceeded."
        headers["Retry-After"] = str(retry_after)
    return {
        "status_code": status.HTTP_429_TOO_MANY_REQUESTS,
        "content": content,
        "headers": headers,
    }


def get_rate_limit_key(request: Request) -> str:
    """Get rate limit key for identifying the client (user or IP)."""
    # user was set in request state by AuthMiddleware
    user = getattr(request.state, "user", None)
    if user and user.user_id:
        return f"user:{user.user_id}"
    # fallback to IP address
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ip = forwarded_for.split(",")[0].strip()
        return f"ip:{ip}"
    ip = request.client.host if request.client else "unknown"
    return f"ip:{ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware that adapts based on user authentication."""

    def __init__(self, app, default_limiter=None):
        super().__init__(app)
        self.default_limiter = default_limiter

    async def dispatch(self, request: Request, call_next):
        if self.default_limiter is None:  # disabled by default
            return await call_next(request)

        # determine credit cost based on request path, as explicit request.scope.route lookup is not yet available
        credit_cost = DEFAULT_CREDIT_COST
        request_path = request.url.path
        for route_path, cost in CUSTOM_RATE_LIMIT_COSTS.items():
            # exact path or path that starts with route_path
            if request_path == route_path or request_path.startswith(route_path):
                credit_cost = cost
                break

        # determine credit pool limit
        user = getattr(request.state, "user", None)
        if user and user.api_credits_per_minute:
            # use user's credit pool limit if authenticated set by AuthMiddleware
            credits_per_minute = user.api_credits_per_minute
        else:
            # fallback to default from limiter
            credits_per_minute = self.default_limiter.credits_per_minute

        # check rate limit
        rate_limit_key = get_rate_limit_key(request)
        request_id = get_request_id()
        if credit_cost > credits_per_minute:
            # the request can never be afforded: reject without consulting the
            # limiter, and without Retry-After as there is no point in retrying
            allowed, remaining_credits, retry_after = False, None, None
        else:
            allowed, remaining_credits, retry_after = self.default_limiter.is_allowed(
                rate_limit_key, credits_per_minute, credit_cost, request_id=request_id
            )

        # store credit info in request.state for logging by LogMiddleware
        request.state.rate_limit = {
            "credits_per_minute": credits_per_minute,
            "credit_cost": credit_cost,
            "remaining_credits": remaining_credits,
        }

        if not allowed:
            request.state.rate_limit["retry_after"] = retry_after
            data = _get_rate_limit_response_data(
                credits_per_minute, credit_cost, remaining_credits, retry_after
            )
            response = Response(
                content=data["content"],
                status_code=data["status_code"],
                media_type="text/plain",
            )
            response.headers.update(data["headers"])
            return response

        response: Response = await call_next(request)

        # apply penalty for 401 unauthorized responses
        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            # record penalty credits by calling is_allowed with high limit to ensure it always passes;
            # use a distinct request_id, otherwise the Redis sorted set member could collide
            # with the original request's member (when credit_cost == UNAUTHORIZED_PENALTY_CREDITS)
            self.default_limiter.is_allowed(
                rate_limit_key,
                credits_per_minute=credits_per_minute + UNAUTHORIZED_PENALTY_CREDITS,
                credit_cost=UNAUTHORIZED_PENALTY_CREDITS,
                request_id=f"{request_id}:penalty",
            )
            # update credit cost to include penalty
            credit_cost += UNAUTHORIZED_PENALTY_CREDITS
            # adjust remaining credits to account for penalty (estimate)
            remaining_credits = max(0, remaining_credits - UNAUTHORIZED_PENALTY_CREDITS)
            # update request state for logging
            request.state.rate_limit["credit_cost"] = credit_cost
            request.state.rate_limit["remaining_credits"] = remaining_credits

        response.headers["X-RateLimit-Limit"] = str(credits_per_minute)
        response.headers["X-RateLimit-Cost"] = str(credit_cost)
        response.headers["X-RateLimit-Remaining"] = str(remaining_credits)
        return response


def create_rate_limiter() -> Optional[InMemoryRateLimiter | RedisRateLimiter]:
    """Create rate limiter based on environment variables.

    Environment variables:
    - RATE_LIMIT_ENABLED: enable rate limiting (set to any truthy value)
    - RATE_LIMIT_CREDITS_PER_MINUTE: default credits per minute
    - RATE_LIMIT_BACKEND: backend to use: "memory" (default) or "redis"
    """
    if not bool(environ.get("RATE_LIMIT_ENABLED", "")):
        return None

    credits_per_minute = int(
        environ.get("RATE_LIMIT_CREDITS_PER_MINUTE", DEFAULT_CREDITS_PER_MINUTE)
    )
    backend = environ.get("RATE_LIMIT_BACKEND", "memory").lower()

    if backend == "redis":
        try:
            limiter = RedisRateLimiter(credits_per_minute)
        except Exception:
            logger.exception("Failed to initialize Redis rate limiter")
            logger.warning("Falling back to in-memory rate limiter")
            backend = "memory"

    if backend == "memory":
        limiter = InMemoryRateLimiter(credits_per_minute)

    logger.info(
        f"Rate limiting enabled: {backend} backend, {credits_per_minute} credits/minute"
    )
    return limiter
