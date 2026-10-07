import importlib
from time import sleep

import pytest
from fastapi.testclient import TestClient


def _create_app_with_env(monkeypatch, **env_vars):
    """Create app with specific environment variables."""
    # set environment variables requested
    for key, value in env_vars.items():
        monkeypatch.setenv(key, str(value))

    # unset other env vars
    for key in [
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_BACKEND",
        "RATE_LIMIT_CREDITS_PER_MINUTE",
        "RATE_LIMIT_DEFAULT_CREDIT_COST",
    ]:
        if key not in env_vars:
            monkeypatch.delenv(key, raising=False)

    # reload modules to pick up new env vars
    import sc_keeper.api
    import sc_keeper.rate_limit

    importlib.reload(sc_keeper.rate_limit)
    importlib.reload(sc_keeper.api)

    return sc_keeper.api.app


@pytest.fixture
def client(monkeypatch):
    """Create a test client for the API with rate limiting disabled."""
    app = _create_app_with_env(monkeypatch)
    return TestClient(app)


@pytest.fixture
def client_with_rate_limit(monkeypatch):
    """Create a test client for the API with rate limiting enabled."""
    app = _create_app_with_env(
        monkeypatch,
        RATE_LIMIT_ENABLED="1",
        RATE_LIMIT_BACKEND="memory",
        RATE_LIMIT_CREDITS_PER_MINUTE="10",
        RATE_LIMIT_DEFAULT_CREDIT_COST="1",
    )
    return TestClient(app)


def test_rate_limit_disabled_allows_all_requests(client):
    """Test that when rate limiting is disabled, all requests succeed."""
    for _ in range(20):
        response = client.get("/healthcheck")
        assert response.status_code == 200


def test_rate_limit_enabled_blocks_after_limit(client_with_rate_limit):
    """Test that when rate limiting is enabled, requests are blocked after credits are exhausted."""
    success_count = 0
    blocked_count = 0
    for _ in range(15):
        response = client_with_rate_limit.get("/healthcheck")
        if response.status_code == 200:
            success_count += 1
        elif response.status_code == 429:
            blocked_count += 1
            assert "X-RateLimit-Limit" in response.headers
            assert "X-RateLimit-Remaining" in response.headers
            assert "X-RateLimit-Cost" in response.headers
            assert int(response.headers["X-RateLimit-Remaining"]) == 0
        else:
            pytest.fail(f"Unexpected status code: {response.status_code}")

    # should have at least some successful requests and some blocked
    assert success_count > 0
    assert blocked_count > 0, "Expected some requests to be rate limited"


def test_rate_limit_headers_present(client_with_rate_limit):
    """Test that rate limit headers are present in responses."""
    response = client_with_rate_limit.get("/healthcheck")
    assert response.status_code == 200

    # Check that rate limit headers are present
    assert "X-RateLimit-Limit" in response.headers
    assert "X-RateLimit-Cost" in response.headers
    assert "X-RateLimit-Remaining" in response.headers

    # Verify header values are integers
    assert int(response.headers["X-RateLimit-Limit"]) > 0
    assert int(response.headers["X-RateLimit-Cost"]) >= 0
    assert int(response.headers["X-RateLimit-Remaining"]) >= 0


def test_rate_limit_custom_credit_cost(client_with_rate_limit):
    """Test that custom credit costs are applied correctly (e.g., /servers costs 3 credits)."""
    # /servers endpoint costs 3 credits according to CUSTOM_RATE_LIMIT_COSTS
    # so we should be able to make fewer requests before hitting the limit

    success_count = 0
    blocked_count = 0

    for _ in range(5):
        response = client_with_rate_limit.get("/servers", params={"limit": 1})
        if response.status_code == 200:
            success_count += 1
            # verify the cost header shows 3 credits
            assert int(response.headers.get("X-RateLimit-Cost", 0)) == 3
        elif response.status_code == 429:
            blocked_count += 1
        else:
            pytest.fail(f"Unexpected status code: {response.status_code}")

    assert success_count > 0
    assert success_count < 4


def test_rate_limit_sliding_window(client_with_rate_limit):
    """Test that rate limiting uses a sliding window (old credits expire after 60 seconds)."""
    # make requests with 1 second sleep between them to spread them out
    responses = []
    for _ in range(15):
        response = client_with_rate_limit.get("/healthcheck")
        responses.append(response.status_code)
        sleep(1)

    # should have some 200s and some 429s
    assert 200 in responses
    assert 429 in responses

    # wait 50 seconds so the first requests fall out of the 60-second window
    sleep(50)

    # should be able to make a successful request now that credits have recovered
    response = client_with_rate_limit.get("/healthcheck")
    assert response.status_code == 200


def test_rate_limit_different_endpoints_share_pool(client_with_rate_limit):
    """Test that different endpoints share the same credit pool."""
    responses = []
    endpoints = ["/healthcheck", *["/servers"] * 3, "/healthcheck"]

    for endpoint in endpoints:
        response = client_with_rate_limit.get(
            endpoint, params={"limit": 1} if endpoint == "/servers" else {}
        )
        responses.append(response.status_code)

    assert 200 in responses
    # last request should be blocked
    assert response.status_code == 429


def test_rate_limit_remaining_decreases(client_with_rate_limit):
    """Test that remaining credits decrease with each request."""
    remaining_credits = []
    for _ in range(10):
        response = client_with_rate_limit.get("/healthcheck")
        if response.status_code == 200:
            remaining = int(response.headers["X-RateLimit-Remaining"])
            remaining_credits.append(remaining)
    assert remaining_credits == [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]


def test_rate_limit_retry_after(client_with_rate_limit):
    """Test that 429 responses include a Retry-After header exposed via CORS."""
    origin = {"Origin": "https://sparecores.com"}
    for _ in range(10):
        response = client_with_rate_limit.get("/healthcheck", headers=origin)
        assert response.status_code == 200
    response = client_with_rate_limit.get("/healthcheck", headers=origin)
    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    assert response.headers["X-RateLimit-Remaining"] == "0"
    # per-client response, must not be cached by CDN/proxies
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Content-Type"].startswith("text/plain")
    # CORS headers must be present on the early 429 response for browsers
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    exposed = response.headers["Access-Control-Expose-Headers"].lower()
    assert "retry-after" in exposed
    assert "x-ratelimit-remaining" in exposed


def test_retry_after_waits_for_enough_credits():
    """Test that Retry-After accounts for the credit cost of the rejected request."""
    from sc_keeper.rate_limit import RateLimiter

    now = 1000.0
    entries = [(now - 50, 2), (now - 30, 3), (now - 10, 5)]
    # 10 used, need 5: dropping the first two entries (5 credits) is enough
    assert RateLimiter._retry_after(entries, 10, 10, 5, now, 60) == 30
    # need 1: dropping the first entry is enough
    assert RateLimiter._retry_after(entries, 10, 10, 1, now, 60) == 10
    # clamped to the window length (e.g. clock skew between workers)
    assert RateLimiter._retry_after([(now + 5, 10)], 10, 10, 1, now, 60) == 60
    # at least 1 second
    assert RateLimiter._retry_after([(now - 59.9, 10)], 10, 10, 1, now, 60) == 1


def test_rate_limit_never_affordable_has_no_retry_after(monkeypatch, caplog):
    """Test that 429 has no Retry-After when the request costs more than the limit."""
    app = _create_app_with_env(
        monkeypatch,
        RATE_LIMIT_ENABLED="1",
        RATE_LIMIT_BACKEND="memory",
        RATE_LIMIT_CREDITS_PER_MINUTE="2",
    )
    client = TestClient(app)
    with caplog.at_level("INFO"):
        # /servers costs 3 credits
        response = client.get("/servers", params={"limit": 1})
    assert response.status_code == 429
    assert "Retry-After" not in response.headers
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "higher than the credit limit" in response.text
    logged = [
        r.rate_limit for r in caplog.records if getattr(r, "event", None) == "response"
    ]
    assert logged[-1]["retry_after"] is None
    # rejected without consulting (and charging) the limiter
    response = client.get("/healthcheck")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == "1"


def test_rate_limit_retry_after_logged(client_with_rate_limit, caplog):
    """Test that the Retry-After value sent to the client is logged."""
    for _ in range(10):
        client_with_rate_limit.get("/healthcheck")
    with caplog.at_level("INFO"):
        response = client_with_rate_limit.get("/healthcheck")
    assert response.status_code == 429
    logged = [
        r.rate_limit for r in caplog.records if getattr(r, "event", None) == "response"
    ]
    assert logged[-1]["retry_after"] == int(response.headers["Retry-After"])


def test_rate_limit_cors_preflight_logged_but_not_charged(
    client_with_rate_limit, caplog
):
    """Test that CORS preflight requests are logged but cost no credits."""
    preflight_headers = {
        "Origin": "https://sparecores.com",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    }
    with caplog.at_level("INFO"):
        for _ in range(15):
            response = client_with_rate_limit.options(
                "/healthcheck", headers=preflight_headers
            )
            assert response.status_code == 200
            assert response.headers["Access-Control-Allow-Origin"] == "*"
            # set by LogMiddleware
            assert "X-Request-ID" in response.headers
            assert "X-RateLimit-Remaining" not in response.headers
    preflight_logs = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "response"
        and getattr(r, "res", {}).get("status_code") == 200
        and getattr(r, "req", {}).get("method") == "OPTIONS"
    ]
    assert len(preflight_logs) == 15
    # all credits are still available after more preflights than the limit
    response = client_with_rate_limit.get("/healthcheck")
    assert response.status_code == 200
    assert int(response.headers["X-RateLimit-Remaining"]) == 9


def test_rate_limit_cors_preflight_with_invalid_token(monkeypatch):
    """Test that preflights are answered without token verification or 401 penalty.

    Relies on CORSMiddleware sitting between AuthMiddleware (which skips token
    verification for preflights) and AuthGuardMiddleware (which would return 401).
    """
    from conftest import create_app_with_auth, mock_token_introspection

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setenv("RATE_LIMIT_CREDITS_PER_MINUTE", "10")
    monkeypatch.delenv("RATE_LIMIT_DEFAULT_CREDIT_COST", raising=False)
    import sc_keeper.rate_limit

    importlib.reload(sc_keeper.rate_limit)
    client = TestClient(
        create_app_with_auth(monkeypatch, "http://test-auth-server.com/introspect")
    )
    with mock_token_introspection({"active": False}):
        response = client.options(
            "/healthcheck",
            headers={
                "Origin": "https://sparecores.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
                "Authorization": "Bearer bad",
            },
        )
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    response = client.get("/healthcheck")
    assert int(response.headers["X-RateLimit-Remaining"]) == 9


# ---------------------------------------------------------------------------
# Limiter backends: the same scenarios run against the in-memory limiter and
# the Redis limiter (Lua script executed by fakeredis) to keep them in sync
# ---------------------------------------------------------------------------


class _FakeClock:
    """Controllable replacement for time.time used by the rate limiters."""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    import sc_keeper.rate_limit

    fake_clock = _FakeClock()
    monkeypatch.setattr(sc_keeper.rate_limit, "time", fake_clock)
    return fake_clock


@pytest.fixture(params=["memory", "redis"])
def limiter(request, monkeypatch, clock):
    """Rate limiter with 10 credits per minute for both backends."""
    import sc_keeper.rate_limit

    if request.param == "memory":
        return sc_keeper.rate_limit.InMemoryRateLimiter(credits_per_minute=10)

    fakeredis = pytest.importorskip("fakeredis")
    pytest.importorskip("lupa")
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        sc_keeper.rate_limit,
        "get_redis_client",
        lambda: fakeredis.FakeRedis(server=server, decode_responses=True),
    )
    redis_limiter = sc_keeper.rate_limit.RedisRateLimiter(credits_per_minute=10)

    # make sure the Lua script is actually tested instead of silently
    # falling back to the in-memory limiter on any error
    class NoFallback:
        def is_allowed(self, *args, **kwargs):
            raise AssertionError("Redis rate limiter fell back to in-memory")

    redis_limiter._fallback_limiter = NoFallback()
    return redis_limiter


def _consume(limiter, clock, at, credit_cost, key="ip:1.2.3.4", request_id=None):
    """Consume credits at the given relative time (seconds from now)."""
    original_now = clock.now
    clock.now = original_now + at
    result = limiter.is_allowed(
        key, credit_cost=credit_cost, request_id=request_id or f"req{at}"
    )
    clock.now = original_now
    return result


def test_limiter_allows_until_limit(limiter):
    """Test remaining credits and rejection once the limit is reached."""
    results = [
        limiter.is_allowed("ip:1.2.3.4", credit_cost=3, request_id=f"r{i}")
        for i in range(4)
    ]
    assert [r[:2] for r in results] == [(True, 7), (True, 4), (True, 1), (False, 1)]
    assert [r[2] for r in results[:3]] == [0, 0, 0]
    # cheaper request still fits
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="r4") == (
        True,
        0,
        0,
    )


def test_limiter_keys_are_independent(limiter):
    """Test that credits are tracked per key."""
    assert limiter.is_allowed("ip:1.1.1.1", credit_cost=10, request_id="a")[0]
    assert not limiter.is_allowed("ip:1.1.1.1", credit_cost=1, request_id="b")[0]
    assert limiter.is_allowed("ip:2.2.2.2", credit_cost=10, request_id="c")[0]


def test_limiter_sliding_window(limiter, clock):
    """Test that credits are freed once their entries leave the window."""
    _consume(limiter, clock, -50, 10)
    assert not limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="x")[0]
    clock.now += 11
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="y") == (
        True,
        9,
        0,
    )


def test_limiter_custom_credits_per_minute(limiter):
    """Test that the per-call limit overrides the default limit."""
    assert limiter.is_allowed(
        "ip:1.2.3.4", credits_per_minute=20, credit_cost=15, request_id="a"
    ) == (True, 5, 0)


@pytest.mark.parametrize(
    "credit_cost, expected",
    [
        # 10 used, need 5: dropping the first two entries (5 credits) is enough
        (5, (False, 0, 30)),
        # need 1: dropping the first entry is enough
        (1, (False, 0, 10)),
        # all credits needed: wait for all entries to expire
        (10, (False, 0, 50)),
    ],
)
def test_limiter_retry_after(limiter, clock, credit_cost, expected):
    """Test Retry-After computation with entries of different costs."""
    _consume(limiter, clock, -50, 2)
    _consume(limiter, clock, -30, 3)
    _consume(limiter, clock, -10, 5)
    assert (
        limiter.is_allowed("ip:1.2.3.4", credit_cost=credit_cost, request_id="new")
        == expected
    )


def test_limiter_retry_after_rounds_up(limiter, clock):
    """Test that Retry-After is rounded up to full seconds."""
    _consume(limiter, clock, -10.5, 10)
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="x") == (
        False,
        0,
        50,
    )


def test_limiter_retry_after_clamped(limiter, clock):
    """Test that Retry-After never exceeds the window, e.g. on clock skew."""
    # entry recorded by a worker with a clock 5 seconds ahead
    _consume(limiter, clock, 5, 10)
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="x") == (
        False,
        0,
        60,
    )


def test_limiter_same_request_id_different_cost(limiter):
    """Test that two entries with the same request_id but different cost both count."""
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=3, request_id="same")[0]
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=4, request_id="same")[0]
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="other") == (
        True,
        2,
        0,
    )


def test_limiter_request_id_none(limiter):
    """Test that requests without request_id (explicit None) are all counted."""
    results = [
        limiter.is_allowed("ip:1.2.3.4", credit_cost=3, request_id=None)
        for _ in range(4)
    ]
    assert [r[:2] for r in results] == [(True, 7), (True, 4), (True, 1), (False, 1)]


@pytest.mark.parametrize(
    "path, cost", [("/healthcheck", 1), ("/table/server_prices", 10)]
)
def test_rate_limit_401_penalty_charged(limiter, path, cost):
    """Test that the 401 penalty is charged on top of the request's own cost.

    Redis stores credits in a sorted set with "{request_id}:{credit_cost}" members,
    so reusing the request_id for a penalty of the same cost (e.g. /table/server_prices)
    would overwrite the original entry instead of adding a new one.
    """
    from starlette.applications import Starlette
    from starlette.responses import Response as StarletteResponse
    from starlette.routing import Route

    from sc_keeper.logger import LogMiddleware
    from sc_keeper.rate_limit import UNAUTHORIZED_PENALTY_CREDITS, RateLimitMiddleware

    async def unauthorized(request):
        return StarletteResponse(status_code=401)

    limiter.credits_per_minute = 100
    app = Starlette(routes=[Route(path, unauthorized)])
    app.add_middleware(RateLimitMiddleware, default_limiter=limiter)
    # sets the request_id
    app.add_middleware(LogMiddleware)

    response = TestClient(app).get(path, headers={"X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 401
    assert int(response.headers["X-RateLimit-Cost"]) == (
        cost + UNAUTHORIZED_PENALTY_CREDITS
    )
    # check the credits actually recorded by the backend
    _, remaining, _ = limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="x")
    assert remaining == 100 - cost - UNAUTHORIZED_PENALTY_CREDITS - 1


def test_limiter_record_ignores_limit(limiter):
    """Test that recorded credits are stored even beyond the limit."""
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=10, request_id="a")[0]
    limiter.record("ip:1.2.3.4", credit_cost=10, request_id="a:penalty")
    limiter.record("ip:1.2.3.4", credit_cost=10, request_id="b:penalty")
    # 30 credits used out of 20
    assert limiter.is_allowed(
        "ip:1.2.3.4", credits_per_minute=20, credit_cost=1, request_id="c"
    ) == (False, 0, 60)
    assert limiter.is_allowed(
        "ip:1.2.3.4", credits_per_minute=40, credit_cost=1, request_id="d"
    ) == (True, 9, 0)


def test_limiter_record_request_id_none(limiter):
    """Test that recorded credits without request_id (explicit None) are all counted."""
    for _ in range(3):
        limiter.record("ip:1.2.3.4", credit_cost=2, request_id=None)
    assert limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="x") == (
        True,
        3,
        0,
    )


def test_rate_limit_401_penalty_without_request_id(limiter):
    """Test that each 401 penalty is charged when no request_id is set."""
    from starlette.applications import Starlette
    from starlette.responses import Response as StarletteResponse
    from starlette.routing import Route

    from sc_keeper.rate_limit import UNAUTHORIZED_PENALTY_CREDITS, RateLimitMiddleware

    async def unauthorized(request):
        return StarletteResponse(status_code=401)

    limiter.credits_per_minute = 100
    app = Starlette(routes=[Route("/healthcheck", unauthorized)])
    # no LogMiddleware: request_id is None
    app.add_middleware(RateLimitMiddleware, default_limiter=limiter)

    client = TestClient(app)
    for _ in range(3):
        response = client.get("/healthcheck", headers={"X-Forwarded-For": "1.2.3.4"})
        assert response.status_code == 401
    _, remaining, _ = limiter.is_allowed("ip:1.2.3.4", credit_cost=1, request_id="x")
    assert remaining == 100 - 3 * (1 + UNAUTHORIZED_PENALTY_CREDITS) - 1
