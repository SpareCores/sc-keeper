"""Shared pytest fixtures and helpers."""

import importlib
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient


def create_app_with_auth(
    monkeypatch, introspection_url, client_id="test_client", client_secret="test_secret"
):
    """Create app with RFC 7662 introspection authentication enabled."""
    monkeypatch.setenv("AUTH_TOKEN_INTROSPECTION_URL", introspection_url)
    monkeypatch.setenv("AUTH_CLIENT_ID", client_id)
    monkeypatch.setenv("AUTH_CLIENT_SECRET", client_secret)

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)

    return sc_keeper.api.app


def create_app_with_jwt_auth(monkeypatch, public_key_pem: str, **extra_env):
    """Create app with JWT Bearer authentication enabled."""
    for var in (
        "AUTH_TOKEN_INTROSPECTION_URL",
        "AUTH_CLIENT_ID",
        "AUTH_CLIENT_SECRET",
        "AUTH_API_KEY_VERIFY_URL",
        "AUTH_API_KEY_VERIFY_BEARER",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("AUTH_JWT_PUBLIC_KEY", public_key_pem)
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)

    return sc_keeper.api.app


def create_app_with_api_key_auth(
    monkeypatch, verify_url, verify_bearer="sk_test", **extra_env
):
    """Create app with opaque API-key verification enabled."""
    for var in (
        "AUTH_TOKEN_INTROSPECTION_URL",
        "AUTH_CLIENT_ID",
        "AUTH_CLIENT_SECRET",
        "AUTH_JWT_JWKS_URL",
        "AUTH_JWT_PUBLIC_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("AUTH_API_KEY_VERIFY_URL", verify_url)
    monkeypatch.setenv("AUTH_API_KEY_VERIFY_BEARER", verify_bearer)
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)

    return sc_keeper.api.app


def create_app_with_static_tokens(monkeypatch, tokens_json: str, **extra_env):
    """Create app with only the static token allowlist enabled."""
    for var in (
        "AUTH_TOKEN_INTROSPECTION_URL",
        "AUTH_CLIENT_ID",
        "AUTH_CLIENT_SECRET",
        "AUTH_JWT_JWKS_URL",
        "AUTH_JWT_PUBLIC_KEY",
        "AUTH_API_KEY_VERIFY_URL",
        "AUTH_API_KEY_VERIFY_BEARER",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("AUTH_STATIC_TOKENS", tokens_json)
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)

    return sc_keeper.api.app


def _create_mock_json_response(payload, status_code=200):
    mock_response = Mock()
    mock_response.json.return_value = payload
    mock_response.status_code = status_code
    mock_response.raise_for_status = Mock()
    if status_code >= 400:
        mock_response.raise_for_status.side_effect = Exception("HTTP error")
    return mock_response


@contextmanager
def mock_token_introspection(user_data=None, exception=None):
    """Mock RFC 7662 token introspection API calls."""
    if exception:
        mock_response = None
    else:
        mock_response = _create_mock_json_response(user_data or {})

    class MockAsyncClient:
        def __init__(self):
            self.post_call_count = 0
            self.get_call_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            self.get_call_count += 1
            raise NotImplementedError("Unexpected GET in introspection mock")

        async def post(self, *args, **kwargs):
            self.post_call_count += 1
            if exception:
                raise exception
            if kwargs.get("data") is not None:
                return mock_response
            raise NotImplementedError("Unexpected POST in introspection mock")

    mock_client = MockAsyncClient()

    with patch("sc_keeper.auth.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@contextmanager
def mock_api_key_verify(user_data=None, status_code=200, exception=None):
    """Mock remote opaque API-key verification calls."""
    mock_response = _create_mock_json_response(user_data or {}, status_code=status_code)

    class MockAsyncClient:
        def __init__(self):
            self.post_call_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            raise NotImplementedError("Unexpected GET in API key mock")

        async def post(self, *args, **kwargs):
            self.post_call_count += 1
            if exception:
                raise exception
            if kwargs.get("json") is not None:
                return mock_response
            raise NotImplementedError("Unexpected POST in API key mock")

    mock_client = MockAsyncClient()

    with patch("sc_keeper.auth.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@contextmanager
def mock_auth_http(
    introspection_data=None,
    introspection_exception=None,
    api_key_data=None,
    api_key_status=200,
    api_key_exception=None,
    jwks_data=None,
    jwks_by_url=None,
):
    """Mock httpx for introspection, API-key verify, and JWKS fetch."""
    introspection_response = (
        None
        if introspection_exception
        else _create_mock_json_response(introspection_data or {})
    )
    api_key_response = _create_mock_json_response(
        api_key_data or {}, status_code=api_key_status
    )
    jwks_response = _create_mock_json_response(jwks_data or {"keys": []})

    class MockAsyncClient:
        def __init__(self):
            self.post_call_count = 0
            self.get_call_count = 0
            self.introspection_calls = 0
            self.api_key_calls = 0
            self.jwks_calls = 0
            self.jwks_urls: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, *args, **kwargs):
            self.get_call_count += 1
            self.jwks_calls += 1
            self.jwks_urls.append(url)
            if jwks_by_url is not None:
                return _create_mock_json_response(jwks_by_url.get(url, {"keys": []}))
            return jwks_response

        async def post(self, *args, **kwargs):
            self.post_call_count += 1
            if introspection_exception and kwargs.get("data") is not None:
                raise introspection_exception
            if api_key_exception and kwargs.get("json") is not None:
                raise api_key_exception
            if kwargs.get("json") is not None:
                self.api_key_calls += 1
                return api_key_response
            if kwargs.get("data") is not None:
                self.introspection_calls += 1
                return introspection_response
            raise NotImplementedError("Unexpected POST in auth HTTP mock")

    mock_client = MockAsyncClient()

    with patch("sc_keeper.auth.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture
def client_with_auth(monkeypatch):
    """Test client with OAuth introspection enabled."""
    introspection_url = "http://test-auth-server.com/introspect"
    app = create_app_with_auth(monkeypatch, introspection_url)
    return TestClient(app), introspection_url


@pytest.fixture(scope="session")
def jwt_keypair():
    """Generate an RSA key pair for JWT tests."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_key, public_pem
