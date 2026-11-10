"""SC-Data-Hash and SC-Data-Published-At headers, including on early responses."""

import re
from datetime import datetime, timezone

import pytest
from test_cache import (  # noqa: F401 (client is a fixture)
    _auth_required_with_token,
    _auth_required_without_token,
    _cors_preflight,
    _healthcheck,
    _invalid_token,
    _never_affordable,
    _public_endpoint,
    _rate_limited,
    client,
)

from sc_keeper.data_version import (
    DATA_HASH_HEADER,
    DATA_PUBLISHED_AT_HEADER,
    format_published_at,
)
from sc_keeper.database import DataVersion, read_published_at, session

RFC3339_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.mark.parametrize(
    "make_request, status_code",
    [
        (_public_endpoint, 200),
        (_healthcheck, 200),
        (_auth_required_without_token, 401),
        (_auth_required_with_token, 200),
        (_invalid_token, 401),
        (_rate_limited, 429),
        (_never_affordable, 429),
        (_cors_preflight, 200),
    ],
    ids=lambda x: x.__name__.strip("_") if callable(x) else None,
)
def test_data_version_headers(client, make_request, status_code):  # noqa: F811
    """Every response names the loaded database, early responses included."""
    response = make_request(client)
    assert response.status_code == status_code
    assert response.headers[DATA_HASH_HEADER] == session.version.hash
    assert RFC3339_SECONDS.match(response.headers[DATA_PUBLISHED_AT_HEADER])


def test_healthcheck_matches_header(client):  # noqa: F811
    """A client pinning the hash from the healthcheck body gets the header's hash."""
    response = client.get("/healthcheck")
    assert response.json()["database_hash"] == response.headers[DATA_HASH_HEADER]


def test_data_version_exposed_to_browsers(client):  # noqa: F811
    response = _cors_preflight(client)
    assert response.status_code == 200
    response = client.get("/healthcheck", headers={"Origin": "https://example.com"})
    exposed = response.headers["Access-Control-Expose-Headers"]
    assert DATA_HASH_HEADER in exposed
    assert DATA_PUBLISHED_AT_HEADER in exposed


def test_format_published_at_truncates_to_seconds():
    published_at = datetime(2026, 11, 7, 22, 47, 8, 401956, tzinfo=timezone.utc)
    assert format_published_at(published_at) == "2026-11-07T22:47:08Z"


def test_read_published_at():
    with session.engine.connect() as conn:
        published_at = read_published_at(conn)
    assert published_at is not None
    assert published_at.tzinfo is not None
    assert session.version == DataVersion(
        hash=session.db_hash, published_at=published_at
    )
