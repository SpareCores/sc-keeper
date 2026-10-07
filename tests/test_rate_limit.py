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
    # can never be afforded
    assert RateLimiter._retry_after(entries, 10, 10, 11, now, 60) == 60
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
    assert "higher than the credit limit" in response.text
    logged = [
        r.rate_limit for r in caplog.records if getattr(r, "event", None) == "response"
    ]
    assert logged[-1]["retry_after"] is None


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
