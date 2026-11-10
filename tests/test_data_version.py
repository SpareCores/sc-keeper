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
    body = response.json()
    assert body["database_hash"] == response.headers[DATA_HASH_HEADER]
    assert body["database_published_at"] == response.headers[DATA_PUBLISHED_AT_HEADER]


def test_healthcheck_uses_the_request_version(client, monkeypatch):  # noqa: F811
    """During a switch, the body describes the version the request started on, like
    its headers."""
    started_on = DataVersion(
        hash="started-on",
        published_at=datetime(2026, 11, 7, 22, 47, 8, tzinfo=timezone.utc),
        engine=session.engine,
    )
    monkeypatch.setattr(session, "version", started_on)
    response = client.get("/healthcheck")
    body = response.json()
    assert body["database_hash"] == response.headers[DATA_HASH_HEADER] == "started-on"
    assert (
        body["database_published_at"]
        == response.headers[DATA_PUBLISHED_AT_HEADER]
        == "2026-11-07T22:47:08Z"
    )


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
    # the version carries the engine of the same database
    assert session.version.engine is session.engine


def test_session_uses_the_version_the_request_started_on(monkeypatch):
    """A database switch while a request waits doesn't mix data and headers: the
    session is opened on the version captured when the request arrived."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlmodel import Session, create_engine

    from sc_keeper.data_version import DataVersionMiddleware
    from sc_keeper.database import get_db

    other_engine = create_engine("sqlite://")
    started_on = DataVersion(hash="started-on", published_at=None, engine=other_engine)

    app = FastAPI()
    app.add_middleware(DataVersionMiddleware)

    @app.get("/bind")
    def bind(db: Session = Depends(get_db)):  # sync: runs in the thread pool
        return {"engine": str(db.get_bind().url)}

    monkeypatch.setattr(session, "version", started_on)
    response = TestClient(app).get("/bind")
    assert response.json() == {"engine": "sqlite://"}
    assert response.headers[DATA_HASH_HEADER] == "started-on"


def test_get_db_outside_requests_uses_the_current_database():
    from sc_keeper.database import get_db

    with next(get_db()) as db:
        assert db.get_bind() is session.engine
