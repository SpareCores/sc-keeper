import importlib
from json import dumps as json_dumps
from unittest.mock import Mock

from conftest import (
    create_app_with_api_key_auth,
    create_app_with_auth,
    create_app_with_jwt_auth,
    create_app_with_static_tokens,
    mock_api_key_verify,
    mock_auth_http,
    mock_token_introspection,
)
from fastapi.testclient import TestClient


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
    app = create_app_with_auth(monkeypatch, introspection_url)
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
    app = create_app_with_auth(monkeypatch, introspection_url)
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


def test_auth_401_penalty_on_rate_limiting(monkeypatch):
    """Test that 401 penalty is applied on rate limiting."""
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

    with mock_token_introspection({"active": False}):
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer inactive_token"}
        )
        assert response.status_code == 401
        assert response.headers["X-RateLimit-Limit"] == "60"
        # 10 credits extra penalty for inactive token + 1 credit for the request
        assert response.headers["X-RateLimit-Remaining"] == "49"


def test_auth_no_verification_enabled(monkeypatch):
    """Test that auth is disabled when no verification method is configured."""
    for var in (
        "AUTH_TOKEN_INTROSPECTION_URL",
        "AUTH_CLIENT_ID",
        "AUTH_CLIENT_SECRET",
        "AUTH_JWT_JWKS_URL",
        "AUTH_JWT_PUBLIC_KEY",
        "AUTH_API_KEY_VERIFY_URL",
        "AUTH_API_KEY_VERIFY_BEARER",
        "AUTH_STATIC_TOKENS",
    ):
        monkeypatch.delenv(var, raising=False)

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)

    client = TestClient(sc_keeper.api.app)

    response = client.get("/healthcheck")
    assert response.status_code == 200

    response = client.get("/healthcheck", headers={"Authorization": "Bearer any_token"})
    assert response.status_code == 200


def test_auth_api_key_verify(monkeypatch):
    """Test opaque API-key verification."""
    verify_url = "http://test-clerk.com/v1/api_keys/verify"
    monkeypatch.setenv("AUTH_API_KEY_TOKEN_REGEX", r"^ak_")
    app = create_app_with_api_key_auth(monkeypatch, verify_url)
    client = TestClient(app)

    with mock_api_key_verify(
        {
            "subject": "user_api",
            "claims": {"api_credits_per_minute": 150, "org": "acme"},
        }
    ):
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer ak_test_key"}
        )
        assert response.status_code == 200
        response = client.get("/me", headers={"Authorization": "Bearer ak_test_key"})
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "user_api"
        # claims merge onto User; only declared fields appear in /me JSON
        assert data["api_credits_per_minute"] == 150


def test_auth_jwt_verify(monkeypatch, jwt_keypair):
    """Test JWT Bearer verification with a static public key."""
    import jwt

    private_key, public_pem = jwt_keypair
    monkeypatch.setenv("AUTH_JWT_TOKEN_REGEX", r"^eyJ")
    app = create_app_with_jwt_auth(monkeypatch, public_pem)
    client = TestClient(app)

    token = jwt.encode({"sub": "user_jwt"}, private_key, algorithm="RS256")
    response = client.get("/healthcheck", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "user_jwt"


def test_auth_dispatch_api_key_regex_short_circuit(monkeypatch):
    """API keys with ak_ prefix should hit branch C only."""
    introspection_url = "http://test-auth-server.com/introspect"
    verify_url = "http://test-clerk.com/v1/api_keys/verify"
    monkeypatch.setenv("AUTH_TOKEN_INTROSPECTION_URL", introspection_url)
    monkeypatch.setenv("AUTH_CLIENT_ID", "test_client")
    monkeypatch.setenv("AUTH_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("AUTH_API_KEY_VERIFY_URL", verify_url)
    monkeypatch.setenv("AUTH_API_KEY_VERIFY_BEARER", "sk_test")
    monkeypatch.setenv("AUTH_API_KEY_TOKEN_REGEX", r"^ak_")

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)
    client = TestClient(sc_keeper.api.app)

    with mock_auth_http(
        introspection_data={"active": True, "sub": "should_not_be_used"},
        api_key_data={"subject": "api_user", "claims": {}},
    ) as mock_client:
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer ak_short_circuit"}
        )
        assert response.status_code == 200
        assert mock_client.api_key_calls == 1
        assert mock_client.introspection_calls == 0


def test_auth_dispatch_catchall_introspection_last(monkeypatch):
    """Opaque tokens without regex matches should fall through to introspection catchall."""
    introspection_url = "http://test-auth-server.com/introspect"
    verify_url = "http://test-clerk.com/v1/api_keys/verify"
    monkeypatch.setenv("AUTH_TOKEN_INTROSPECTION_URL", introspection_url)
    monkeypatch.setenv("AUTH_CLIENT_ID", "test_client")
    monkeypatch.setenv("AUTH_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("AUTH_API_KEY_VERIFY_URL", verify_url)
    monkeypatch.setenv("AUTH_API_KEY_VERIFY_BEARER", "sk_test")
    monkeypatch.setenv("AUTH_API_KEY_TOKEN_REGEX", r"^ak_")
    monkeypatch.setenv(
        "AUTH_JWT_TOKEN_REGEX",
        r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
    )

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)
    client = TestClient(sc_keeper.api.app)

    with mock_auth_http(
        introspection_data={"active": True, "sub": "opaque_user"},
        api_key_data={"subject": "api_user", "claims": {}},
    ) as mock_client:
        response = client.get(
            "/healthcheck", headers={"Authorization": "Bearer opaque_pat_token"}
        )
        assert response.status_code == 200
        assert mock_client.api_key_calls == 0
        assert mock_client.introspection_calls == 1


def test_auth_jwt_jwks_verify(monkeypatch, jwt_keypair):
    """Test JWT verification via JWKS fetch and kid lookup."""
    import json

    import jwt
    from jwt.algorithms import RSAAlgorithm

    private_key, _ = jwt_keypair
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "kid-1"

    for var in (
        "AUTH_TOKEN_INTROSPECTION_URL",
        "AUTH_CLIENT_ID",
        "AUTH_CLIENT_SECRET",
        "AUTH_API_KEY_VERIFY_URL",
        "AUTH_API_KEY_VERIFY_BEARER",
        "AUTH_JWT_PUBLIC_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("AUTH_JWT_JWKS_URL", "http://test/.well-known/jwks.json")
    monkeypatch.setenv("AUTH_JWT_TOKEN_REGEX", r"^eyJ")

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)
    client = TestClient(sc_keeper.api.app)

    token = jwt.encode(
        {"sub": "jwks_user"},
        private_key,
        algorithm="RS256",
        headers={"kid": "kid-1"},
    )
    with mock_auth_http(jwks_data={"keys": [public_jwk]}):
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["user_id"] == "jwks_user"


def test_auth_jwt_unknown_kid_no_refetch(monkeypatch, jwt_keypair):
    """An unknown kid must not trigger a JWKS refetch (TTL cache only)."""
    import json

    import jwt
    from jwt.algorithms import RSAAlgorithm

    private_key, _ = jwt_keypair
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "kid-1"

    for var in (
        "AUTH_TOKEN_INTROSPECTION_URL",
        "AUTH_CLIENT_ID",
        "AUTH_CLIENT_SECRET",
        "AUTH_API_KEY_VERIFY_URL",
        "AUTH_API_KEY_VERIFY_BEARER",
        "AUTH_JWT_PUBLIC_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("AUTH_JWT_JWKS_URL", "http://test/.well-known/jwks.json")

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)
    client = TestClient(sc_keeper.api.app)

    with mock_auth_http(jwks_data={"keys": [public_jwk]}) as mock_client:
        # different tokens to bypass the token cache, each with an unknown kid
        for i in range(3):
            token = jwt.encode(
                {"sub": f"attacker_{i}"},
                private_key,
                algorithm="RS256",
                headers={"kid": f"unknown-kid-{i}"},
            )
            response = client.get(
                "/healthcheck", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 401
        # only the initial load, no per-request refetch
        assert mock_client.jwks_calls == 1


def test_auth_dispatch_failed_jwt_then_introspect(monkeypatch, jwt_keypair):
    """A JWT regex match that fails JWKS should still fall through to introspection."""
    import jwt

    _, public_pem = jwt_keypair
    introspection_url = "http://test-auth-server.com/introspect"
    monkeypatch.setenv("AUTH_TOKEN_INTROSPECTION_URL", introspection_url)
    monkeypatch.setenv("AUTH_CLIENT_ID", "test_client")
    monkeypatch.setenv("AUTH_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("AUTH_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.setenv(
        "AUTH_JWT_TOKEN_REGEX",
        r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
    )

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)
    client = TestClient(sc_keeper.api.app)

    token = jwt.encode(
        {"sub": "jwt_user"}, "wrong-hs256-secret-32-bytes-min!!", algorithm="HS256"
    )

    with mock_token_introspection({"active": True, "sub": "fallback_user"}):
        response = client.get(
            "/healthcheck", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "fallback_user"


def test_auth_static_tokens_only(monkeypatch):
    """Static allowlist works when it is the only configured method."""
    tokens = json_dumps(
        [
            {
                "token": "mig_abc",
                "subject": "user_123",
                "api_credits_per_minute": 200,
            }
        ]
    )
    app = create_app_with_static_tokens(monkeypatch, tokens)
    client = TestClient(app)

    response = client.get("/me", headers={"Authorization": "Bearer mig_abc"})
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "user_123"
    assert data["api_credits_per_minute"] == 200

    response = client.get(
        "/healthcheck", headers={"Authorization": "Bearer unknown_token"}
    )
    assert response.status_code == 401


def test_auth_static_tokens_last_resort(monkeypatch):
    """A listed static token authenticates after other methods return None."""
    introspection_url = "http://test-auth-server.com/introspect"
    monkeypatch.setenv("AUTH_TOKEN_INTROSPECTION_URL", introspection_url)
    monkeypatch.setenv("AUTH_CLIENT_ID", "test_client")
    monkeypatch.setenv("AUTH_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv(
        "AUTH_STATIC_TOKENS",
        json_dumps([{"token": "mig_listed", "subject": "static_user"}]),
    )

    import sc_keeper.api
    import sc_keeper.auth

    importlib.reload(sc_keeper.auth)
    importlib.reload(sc_keeper.api)
    client = TestClient(sc_keeper.api.app)

    with mock_token_introspection({"active": False}) as mock_client:
        response = client.get("/me", headers={"Authorization": "Bearer mig_listed"})
        assert response.status_code == 200
        assert response.json()["user_id"] == "static_user"
        assert mock_client.post_call_count == 1
