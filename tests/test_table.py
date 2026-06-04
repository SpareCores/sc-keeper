"""Tests for /table/* dump endpoints and /table/server/meta."""

import pytest
from fastapi.testclient import TestClient
from sc_crawler.tables import Server

from sc_keeper.api import app
from sc_keeper.routers.table_metadata import _get_category
from test_auth import _create_app_with_auth, mock_token_introspection

client = TestClient(app)

VALID_CATEGORIES = {"meta", "cpu", "memory", "gpu", "storage", "network"}


@pytest.fixture
def auth_client(monkeypatch):
    """Test client with OAuth introspection enabled (for /table/server_prices)."""
    app_with_auth = _create_app_with_auth(
        monkeypatch, "http://test-auth-server.com/introspect"
    )
    return TestClient(app_with_auth)

# (path suffix under /table, required field on first row)
TABLE_DUMPS = [
    ("benchmark", "benchmark_id"),
    ("country", "country_id"),
    ("compliance_framework", "compliance_framework_id"),
    ("vendor", "vendor_id"),
    ("region", "region_id"),
    ("zone", "zone_id"),
    ("storage", "storage_id"),
]


@pytest.mark.parametrize("path, id_field", TABLE_DUMPS)
def test_table_dump(path, id_field):
    """Lightweight table dumps return 200 with non-empty lists."""
    response = client.get(f"/table/{path}")
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 2
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert id_field in data[0]


def test_table_server():
    """Full Server dump returns all rows with primary keys."""
    response = client.get("/table/server")
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 3
    data = response.json()
    assert len(data) > 100
    assert data[0]["vendor_id"]
    assert data[0]["server_id"]


@pytest.mark.parametrize(
    "params,expected_keys,exact",
    [
        (None, {"vendor_id", "server_id", "name"}, False),
        ({"columns": ["vendor_id", "server_id"]}, {"vendor_id", "server_id"}, True),
        ({"columns": ["vendor_id"]}, {"vendor_id"}, True),
    ],
)
def test_table_server_select(params, expected_keys, exact):
    """Server column selection returns dict rows with requested keys only."""
    response = client.get("/table/server/select", params=params or {})
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 2
    data = response.json()
    assert len(data) > 100
    keys = set(data[0].keys())
    if exact:
        assert keys == expected_keys
    else:
        assert expected_keys <= keys


def test_table_server_prices_requires_auth():
    """Server prices dump requires a bearer token when auth is enabled."""
    response = client.get("/table/server_prices", params={"vendor": ["hcloud"]})
    assert response.status_code == 401


def test_table_server_prices(auth_client):
    """Authenticated server prices dump supports filters and currency."""
    client_auth = auth_client

    params = {"vendor": ["hcloud"], "allocation": "ondemand"}
    headers = {"Authorization": "Bearer valid_token"}
    user = {"active": True, "sub": "user123"}

    with mock_token_introspection(user):
        response = client_auth.get(
            "/table/server_prices", params=params, headers=headers
        )
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 5
    data = response.json()
    assert len(data) > 0
    row = data[0]
    assert row["vendor_id"] == "hcloud"
    assert row["region_id"]
    assert row["price"] is not None

    with mock_token_introspection(user):
        response = client_auth.get(
            "/table/server_prices",
            params=params | {"currency": "USD"},
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()[0]["currency"] == "USD"

    with mock_token_introspection(user):
        response = client_auth.get(
            "/table/server_prices",
            params=params | {"currency": "INVALID"},
            headers=headers,
        )
    assert response.status_code == 400


def test_all_server_columns_have_category():
    """Every Server column must map to a metadata category (regression guard)."""
    for column in Server.model_fields:
        assert column in Server.get_columns()["all"]
        assert _get_category(column) in VALID_CATEGORIES


def test_table_server_meta():
    """GET /table/server/meta returns table info and categorized field metadata."""
    response = client.get("/table/server/meta")
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    data = response.json()
    assert data["table"]["name"] == Server.get_table_name()
    assert data["table"]["description"]
    assert len(data["fields"]) == len(Server.model_fields)
    for field in data["fields"]:
        assert field["id"] in Server.model_fields
        assert field["name"]
        assert field["description"] is not None
        assert field["category"] in VALID_CATEGORIES
