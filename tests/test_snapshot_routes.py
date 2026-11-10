"""/snapshot routes (sc-specs 2026-11-08-keeper-snapshots.md R5, R13-R18, R21)."""

import threading
import time

import pytest
from conftest import create_app_with_auth, mock_token_introspection
from fastapi.testclient import TestClient

from sc_keeper import snapshots
from sc_keeper.data_version import (
    DATA_HASH_HEADER,
    DATA_PUBLISHED_AT_HEADER,
    format_published_at,
)
from sc_keeper.database import session
from sc_keeper.routers.snapshot import (
    CACHE_CONTROL,
    NOT_AVAILABLE,
    NOT_FOUND,
    accepts_gzip,
)

AUTH = {"Authorization": "Bearer valid_token"}
UNKNOWN_HASH = "0" * 40


@pytest.fixture
def client(monkeypatch):
    app = create_app_with_auth(monkeypatch, "http://test-auth-server.com/introspect")
    return TestClient(app)


@pytest.fixture
def db_hash() -> str:
    db_hash = session.version.hash
    assert snapshots.is_complete(snapshots.snapshot_dir() / db_hash)
    return db_hash


def _get(client, url, headers=None):
    with mock_token_introspection({"active": True, "sub": "user123"}):
        return client.get(url, headers={**AUTH, **(headers or {})})


def test_requires_api_key(client, db_hash):
    response = client.get(f"/snapshot/{db_hash}")
    assert response.status_code == 401


def test_requires_gzip(client, db_hash):
    """R14: a request that doesn't accept gzip gets 406."""
    response = _get(client, f"/snapshot/{db_hash}", {"Accept-Encoding": "identity"})
    assert response.status_code == 406
    assert response.headers["Content-Type"] == "application/problem+json"


@pytest.mark.parametrize(
    "header, accepted",
    [
        ("gzip", True),
        ("br, gzip;q=0.5", True),
        ("*", True),
        ("GZIP", True),
        ("gzip;q=0", False),
        ("gzip;q=0, *", False),
        ("*, gzip;q=0", False),
        ("br, *", True),
        ("*;q=0", False),
        ("identity", False),
        ("br", False),
        (None, False),
    ],
)
def test_accepts_gzip(header, accepted):
    assert accepts_gzip(header) is accepted


def test_manifest(client, db_hash):
    """R13, R15, R16: the manifest, gzipped, with the snapshot's version headers."""
    response = _get(client, f"/snapshot/{db_hash}")
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["Cache-Control"] == CACHE_CONTROL
    assert response.headers[DATA_HASH_HEADER] == db_hash
    assert response.headers[DATA_PUBLISHED_AT_HEADER] == format_published_at(
        session.version.published_at
    )
    manifest = response.json()
    assert list(manifest) == ["path", "rows", "sha256"]
    assert "server/aws" in manifest["path"]


def test_vendor_table(client, db_hash):
    response = _get(client, f"/snapshot/{db_hash}/server/aws")
    assert response.status_code == 200
    doc = response.json()
    assert doc["server_id"] and set(doc["vendor_id"]) == {"aws"}


def test_global_table(client, db_hash):
    response = _get(client, f"/snapshot/{db_hash}/country")
    assert response.status_code == 200
    assert "country_id" in response.json()


def test_scores(client, db_hash):
    manifest = _get(client, f"/snapshot/{db_hash}").json()
    path = next(p for p in manifest["path"] if p.startswith("benchmark_score/aws/"))
    response = _get(client, f"/snapshot/{db_hash}/{path}")
    assert response.status_code == 200
    assert set(response.json()["vendor_id"]) == {"aws"}


def test_scores_of_unknown_benchmark_are_empty(client, db_hash):
    """R11: a well-formed benchmark id without rows gets empty arrays."""
    response = _get(
        client, f"/snapshot/{db_hash}/benchmark_score/aws/no:such-benchmark"
    )
    assert response.status_code == 200
    assert response.headers[DATA_HASH_HEADER] == db_hash
    doc = response.json()
    assert "config" in doc and all(values == [] for values in doc.values())


@pytest.mark.parametrize(
    "path",
    [
        "server/no-such-vendor",
        "server/.hidden",
        "benchmark_score/no-such-vendor/stress_ng:bestn",
        "benchmark_score/aws/.hidden",
    ],
)
def test_not_found(client, db_hash, path):
    """R17: unknown vendor or malformed id: not-found, not cached."""
    response = _get(client, f"/snapshot/{db_hash}/{path}")
    assert response.status_code == 404
    assert response.json()["type"] == NOT_FOUND
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("requested", [UNKNOWN_HASH, "not-a-hash"])
def test_hash_not_available(client, db_hash, requested):
    """R17: an unavailable hash names the replica's current hash."""
    response = _get(client, f"/snapshot/{requested}/server/aws")
    assert response.status_code == 404
    problem = response.json()
    assert problem["type"] == NOT_AVAILABLE
    assert problem["requested"] == requested
    assert problem["current"] == db_hash
    assert db_hash in problem["available"]
    # R3: a 404 describes the replica's current database
    assert response.headers[DATA_HASH_HEADER] == db_hash


def test_current_hash_is_rebuilt_on_request(client, db_hash, tmp_path, monkeypatch):
    """R21 step 3: the current database's snapshots are built when missing."""
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    built = []

    def build(conn, hash_dir, published_at):
        built.append(hash_dir.name)
        files = [snapshots._write(hash_dir, "country", {"country_id": ["DE"]}, 0)]
        manifest = {
            "path": [f.path for f in files],
            "rows": [f.rows for f in files],
            "sha256": [f.sha256 for f in files],
        }
        snapshots._write(hash_dir, snapshots.MANIFEST_PATH, manifest, 0)

    monkeypatch.setattr(snapshots, "build", build)
    response = _get(client, f"/snapshot/{db_hash}/country")
    assert response.status_code == 200
    assert response.json() == {"country_id": ["DE"]}
    assert built == [db_hash]


def test_healthcheck_lists_local_hashes(client, db_hash):
    """R18: /healthcheck lists the hashes this replica has locally."""
    assert db_hash in client.get("/healthcheck").json()["snapshot_hashes"]


def test_openapi(client):
    spec = client.get("/openapi.json").json()
    route = spec["paths"]["/snapshot/{hash}/server_price/{vendor}"]["get"]
    assert route["security"]
    schema = route["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("ServerPriceSnapshot")
    properties = spec["components"]["schemas"]["ServerPriceSnapshot"]["properties"]
    assert "price_monthly" in properties and "observed_at" not in properties


def test_rebuild_doesnt_hold_the_request(client, db_hash, tmp_path, monkeypatch):
    """R21: a rebuild runs in the background; the request isn't held for it."""
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_REQUEST_WAIT_SECONDS", "0.2")
    release = threading.Event()

    def slow_build(conn, hash_dir, published_at):
        release.wait(10)

    monkeypatch.setattr(snapshots, "build", slow_build)
    t0 = time.time()
    response = _get(client, f"/snapshot/{db_hash}/country")
    release.set()
    assert time.time() - t0 < 3
    assert response.status_code == 404
    assert response.json()["type"] == NOT_AVAILABLE


def test_snapshot_without_published_at(client, tmp_path, monkeypatch):
    """R3: a snapshot without a publication time gets no other version's time."""
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    hash_dir = tmp_path / UNKNOWN_HASH
    files = [snapshots._write(hash_dir, "country", {"country_id": ["DE"]}, 0)]
    manifest = {
        "path": [f.path for f in files],
        "rows": [f.rows for f in files],
        "sha256": [f.sha256 for f in files],
    }
    snapshots._write(hash_dir, snapshots.MANIFEST_PATH, manifest, 0)
    response = _get(client, f"/snapshot/{UNKNOWN_HASH}/country")
    assert response.status_code == 200
    assert response.headers[DATA_HASH_HEADER] == UNKNOWN_HASH
    assert DATA_PUBLISHED_AT_HEADER not in response.headers


def test_vendor_cache_is_thread_safe(tmp_path):
    """Concurrent lookups of more hashes than the cache holds never fail."""
    from concurrent.futures import ThreadPoolExecutor

    from sc_keeper.routers import snapshot as router

    dirs = []
    for i in range(40):
        hash_dir = tmp_path / f"{i:040x}"
        snapshots._write(hash_dir, "vendor", {"vendor_id": ["aws"]}, 0)
        dirs.append(hash_dir)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(router._vendors, dirs * 25))
    assert all(vendors == frozenset({"aws"}) for vendors in results)
    assert len(router._vendor_cache) <= 16
