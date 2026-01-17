import importlib
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient


def _create_app_with_auth(
    monkeypatch, introspection_url, client_id="test_client", client_secret="test_secret"
):
    """Create app with authentication enabled."""
    monkeypatch.setenv("AUTH_TOKEN_INTROSPECTION_URL", introspection_url)
    monkeypatch.setenv("AUTH_CLIENT_ID", client_id)
    monkeypatch.setenv("AUTH_CLIENT_SECRET", client_secret)

    # reload modules to pick up new env vars
    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)

    return sc_keeper.api.app


def _create_mock_introspection_response(user_data):
    """Create a mock token introspection response."""
    mock_response = Mock()
    mock_response.json.return_value = user_data
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()
    return mock_response


@contextmanager
def mock_token_introspection(user_data=None, exception=None):
    """Context manager to mock token introspection API calls.

    Args:
        user_data: Dict with token introspection response data (e.g., {"sub": "user123"})
        exception: Exception to raise instead of returning a response

    Yields:
        The mock async client (useful for checking call counts)
    """
    if exception:
        mock_response = None
    else:
        mock_response = _create_mock_introspection_response(user_data or {})

    class MockAsyncClient:
        def __init__(self):
            self.post_call_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            self.post_call_count += 1
            if exception:
                raise exception
            return mock_response

    mock_client = MockAsyncClient()

    with patch("sc_keeper.auth.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture
def client_with_auth(monkeypatch):
    """Create a test client with authentication enabled."""
    introspection_url = "http://test-auth-server.com/introspect"
    app = _create_app_with_auth(monkeypatch, introspection_url)
    return TestClient(app), introspection_url


def test_endpoints_no_token(client_with_auth):
    """Test that requests without token are allowed at the public endpoints but not at the private endpoints."""
    client, _ = client_with_auth
    response = client.get("/healthcheck")
    assert response.status_code == 200
    response = client.get("/me")
    assert response.status_code == 401


def test_endpoints_with_token(client_with_auth):
    """Test that requests with token are allowed at both the public and private endpoints."""
    client, _ = client_with_auth
    response = client.get("/healthcheck")
    assert response.status_code == 200

    with mock_token_introspection(
        {
            "active": True,
            "sub": "user123",
            "scope": "read write",
            "api_credits_per_minute": 100,
        }
    ):
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer valid_token_123"}
        )
        assert response.status_code == 200
        response = client.get(
            "/me", headers={"Authorization": "Bearer valid_token_123"}
        )
        assert response.status_code == 200


def test_auth_inactive_token(client_with_auth):
    """Test that requests with inactive token return 401."""
    client, _ = client_with_auth

    # mock inactive token introspection response
    with mock_token_introspection({"active": False}):
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer invalid_token"}
        )
        assert response.status_code == 401
        assert "Invalid or expired token" in response.text


def test_auth_token_missing_scope(monkeypatch):
    """Test that requests with token missing required scope return 401."""
    introspection_url = "http://test-auth-server.com/introspect"
    monkeypatch.setenv("AUTH_TOKEN_VALIDATION_CEL", "claims.scope == 'required_scope'")
    app = _create_app_with_auth(monkeypatch, introspection_url)
    client = TestClient(app)

    with mock_token_introspection(
        {
            "active": True,
            "sub": "user123",
            "scope": "other_scope",
        }
    ):
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer token_without_scope"}
        )
        assert response.status_code == 401


def test_auth_token_with_required_scope(monkeypatch):
    """Test that requests with token having required scope succeed."""
    introspection_url = "http://test-auth-server.com/introspect"
    monkeypatch.setenv("AUTH_TOKEN_VALIDATION_CEL", "claims.scope == 'required_scope'")
    app = _create_app_with_auth(monkeypatch, introspection_url)
    client = TestClient(app)

    with mock_token_introspection(
        {
            "active": True,
            "sub": "user123",
            "scope": "required_scope",
        }
    ):
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer token_with_scope"}
        )
        assert response.status_code == 200


def test_auth_token_introspection_error(client_with_auth):
    """Test that requests fail gracefully when introspection API errors."""
    client, _ = client_with_auth

    import httpx

    mock_request = Mock()
    mock_response = Mock()
    with mock_token_introspection(
        exception=httpx.HTTPStatusError(
            "API error", request=mock_request, response=mock_response
        )
    ):
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer token_error"}
        )
        assert response.status_code == 401


def test_auth_token_caching(client_with_auth):
    """Test that token validation results are cached."""
    client, _ = client_with_auth

    with mock_token_introspection(
        {
            "active": True,
            "sub": "user123",
            "api_credits_per_minute": 50,
        }
    ) as mock_client:
        # first request should call introspection API
        response1 = client.get(
            "/healthcheck", headers={"Authorization": "Bearer cached_token"}
        )
        assert response1.status_code == 200
        assert mock_client.post_call_count == 1

        # second request should use cache (should not call API again)
        response2 = client.get(
            "/healthcheck", headers={"Authorization": "Bearer cached_token"}
        )
        assert response2.status_code == 200
        # should still be 1 call due to L1 cache
        assert mock_client.post_call_count == 1


def test_auth_user_credits_per_minute(monkeypatch):
    """Test that user's api_credits_per_minute is extracted from token and used for rate limiting."""
    introspection_url = "http://test-auth-server.com/introspect"
    # set up auth
    monkeypatch.setenv("AUTH_TOKEN_INTROSPECTION_URL", introspection_url)
    monkeypatch.setenv("AUTH_CLIENT_ID", "test_client")
    monkeypatch.setenv("AUTH_CLIENT_SECRET", "test_secret")
    # enable rate limiting with a default rate different from user's rate
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setenv("RATE_LIMIT_CREDITS_PER_MINUTE", "60")
    monkeypatch.setenv("RATE_LIMIT_DEFAULT_CREDIT_COST", "1")

    # reload modules to pick up auth and rate limiting config
    import sc_keeper.api
    import sc_keeper.auth
    import sc_keeper.rate_limit

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.rate_limit)
    importlib.reload(sc_keeper.api)

    client = TestClient(sc_keeper.api.app)

    with mock_token_introspection(
        {
            "active": True,
            "sub": "user123",
            "api_credits_per_minute": 200,
        }
    ):
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer token_with_credits"}
        )
        assert response.status_code == 200
        # verify that the rate limit header shows user's custom rate (200) instead of default (60)
        assert response.headers["X-RateLimit-Limit"] == "200"


def test_auth_no_introspection_url(monkeypatch):
    """Test that auth is disabled when AUTH_TOKEN_INTROSPECTION_URL is not set."""
    monkeypatch.delenv("AUTH_TOKEN_INTROSPECTION_URL", raising=False)

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)

    client = TestClient(sc_keeper.api.app)

    # should work without token
    response = client.get("/healthcheck")
    assert response.status_code == 200

    # token should be ignored (not validated)
    response = client.get("/healthcheck", headers={"Authorization": "Bearer any_token"})
    assert response.status_code == 200
