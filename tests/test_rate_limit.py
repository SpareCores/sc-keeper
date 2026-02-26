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
        sleep(2)
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
