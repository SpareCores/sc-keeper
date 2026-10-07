"""Cache-Control headers, including responses returned early by middlewares."""

from unittest.mock import patch

import pytest
from conftest import create_app_with_auth, mock_token_introspection
from fastapi import HTTPException
from fastapi.testclient import TestClient

PUBLIC = "public, max-age=3600"
NO_STORE = "private, no-store"


@pytest.fixture
def client(monkeypatch):
    """Client with auth and rate limiting (5 credits per minute) enabled."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setenv("RATE_LIMIT_CREDITS_PER_MINUTE", "5")
    monkeypatch.delenv("RATE_LIMIT_DEFAULT_CREDIT_COST", raising=False)
    app = create_app_with_auth(monkeypatch, "http://test-auth-server.com/introspect")
    return TestClient(app)


def _public_endpoint(client):
    return client.get("/servers", params={"limit": 1})


def _healthcheck(client):
    return client.get("/healthcheck")


def _auth_required_without_token(client):
    return client.get("/me")


def _auth_required_with_token(client):
    with mock_token_introspection({"active": True, "sub": "user123"}):
        return client.get("/me", headers={"Authorization": "Bearer valid_token"})


def _invalid_token(client):
    """401 returned early by AuthGuardMiddleware."""
    with mock_token_introspection({"active": False}):
        return client.get(
            "/servers", params={"limit": 1}, headers={"Authorization": "Bearer bad"}
        )


def _rate_limited(client):
    """429 returned early by RateLimitMiddleware (/servers costs 3 credits)."""
    client.get("/servers", params={"limit": 1})
    return client.get("/servers", params={"limit": 1})


def _never_affordable(client):
    """429 returned early by RateLimitMiddleware (costs 10 credits > limit of 5)."""
    return client.get("/table/server_prices")


def _cors_preflight(client):
    """200 returned early by CORSMiddleware."""
    return client.options(
        "/servers",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )


def _cors_preflight_disallowed(client):
    """400 returned early by CORSMiddleware for a disallowed request header."""
    return client.options(
        "/servers",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Not-Allowed",
        },
    )


def _too_many_heavy_jobs(client):
    """503 raised by the concurrency limiter of heavy endpoints."""

    async def busy(self):
        raise HTTPException(status_code=503, detail="Server busy")

    with patch("sc_keeper.limits.SemaphoreLimiter.__aenter__", busy):
        return client.get("/debug")


@pytest.mark.parametrize(
    "make_request, status_code, cache_control",
    [
        (_public_endpoint, 200, PUBLIC),
        (_healthcheck, 200, NO_STORE),
        (_auth_required_without_token, 401, NO_STORE),
        (_auth_required_with_token, 200, NO_STORE),
        (_invalid_token, 401, NO_STORE),
        (_rate_limited, 429, NO_STORE),
        (_never_affordable, 429, NO_STORE),
        (_cors_preflight, 200, PUBLIC),
        (_cors_preflight_disallowed, 400, NO_STORE),
        (_too_many_heavy_jobs, 503, NO_STORE),
    ],
    ids=lambda x: x.__name__.strip("_") if callable(x) else None,
)
def test_cache_control(client, make_request, status_code, cache_control):
    """Test that per-client and error responses are never cached publicly."""
    response = make_request(client)
    assert response.status_code == status_code
    assert response.headers["Cache-Control"].startswith(cache_control)
