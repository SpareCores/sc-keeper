"""Shared pytest fixtures and helpers."""

import importlib
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient


def create_app_with_auth(
    monkeypatch, introspection_url, client_id="test_client", client_secret="test_secret"
):
    """Create app with authentication enabled."""
    monkeypatch.setenv("AUTH_TOKEN_INTROSPECTION_URL", introspection_url)
    monkeypatch.setenv("AUTH_CLIENT_ID", client_id)
    monkeypatch.setenv("AUTH_CLIENT_SECRET", client_secret)

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)

    return sc_keeper.api.app


def _create_mock_introspection_response(user_data):
    mock_response = Mock()
    mock_response.json.return_value = user_data
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()
    return mock_response


@contextmanager
def mock_token_introspection(user_data=None, exception=None):
    """Mock token introspection API calls."""
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
    """Test client with OAuth introspection enabled."""
    introspection_url = "http://test-auth-server.com/introspect"
    app = create_app_with_auth(monkeypatch, introspection_url)
    return TestClient(app), introspection_url
