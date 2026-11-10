"""Snapshot builder (sc-specs 2026-11-08-keeper-snapshots.md R5-R13)."""

import gzip
import hashlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import pytest
from sc_crawler.table_fields import CpuArchitecture
from sqlmodel import text

from sc_keeper import snapshots
from sc_keeper.currency import currency_converter
from sc_keeper.database import session


@pytest.fixture(scope="module")
def hash_dir() -> Path:
    """Snapshots built when the test database was loaded."""
    path = snapshots.snapshot_dir() / session.version.hash
    assert snapshots.is_complete(path)
    return path


def _read(hash_dir: Path, path: str) -> dict:
    with gzip.open(snapshots.file_path(hash_dir, path)) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def manifest(hash_dir) -> dict:
    return _read(hash_dir, snapshots.MANIFEST_PATH)


def _rows(doc: dict) -> list[dict]:
    columns = list(doc)
    return [dict(zip(columns, values)) for values in zip(*doc.values())]


def test_manifest(hash_dir, manifest):
    """R13: one row per file, sorted by path, with rows and SHA-256 of the content."""
    assert list(manifest) == ["path", "rows", "sha256"]
    assert manifest["path"] == sorted(manifest["path"])
    for path, rows, sha256 in zip(*manifest.values()):
        with gzip.open(snapshots.file_path(hash_dir, path)) as f:
            raw = f.read()
        assert hashlib.sha256(raw).hexdigest() == sha256
        doc = json.loads(raw)
        lengths = {len(values) for values in doc.values()}
        assert lengths == {rows}, path


def test_routes(hash_dir, manifest):
    """R5: every vendor table per available vendor, the other tables once."""
    paths = set(manifest["path"])
    vendors = sorted(_vendor_ids(hash_dir))
    assert vendors
    for model in snapshots.VENDOR_TABLES:
        for vendor_id in vendors:
            assert f"{model.__table__.name}/{vendor_id}" in paths
    for model in snapshots.GLOBAL_TABLES:
        assert model.__table__.name in paths
    assert any(p.startswith("benchmark_score/") for p in paths)


def _vendor_ids(hash_dir) -> set[str]:
    return set(_read(hash_dir, "vendor")["vendor_id"])


def test_empty_vendor_file(hash_dir, manifest):
    """R5: a vendor with no rows in a table gets empty arrays."""
    empty = [p for p, rows in zip(manifest["path"], manifest["rows"]) if rows == 0]
    assert empty, "expected at least one vendor without rows in some table"
    doc = _read(hash_dir, empty[0])
    assert doc and all(values == [] for values in doc.values())


def test_gzip_header_carries_published_at(hash_dir):
    """R15: the gzip modification time is the hash's publication time."""
    with open(snapshots.file_path(hash_dir, "server/aws"), "rb") as f:
        header = f.read(10)
    mtime = int.from_bytes(header[4:8], "little")
    assert mtime == int(session.version.published_at.timestamp())


def test_columns_and_enum_values(hash_dir):
    """R8: every column except observed_at; R12: enums by value; R6: available only."""
    doc = _read(hash_dir, "server/aws")
    assert "observed_at" not in doc
    assert "status" in doc and "vcpus" in doc
    assert set(doc["status"]) <= {"active", "planned-for-retirement"}
    assert set(doc["cpu_architecture"]) <= {a.value for a in CpuArchitecture}
    prices = _read(hash_dir, "server_price/aws")
    assert "price_monthly" in prices
    assert set(prices["allocation"]) <= {"ondemand", "spot", "reserved"}
    assert set(prices["unit"]) == {"hour"}


def test_sorted_by_primary_key(hash_dir):
    """R12: rows are sorted by the table's primary key."""
    doc = _read(hash_dir, "server_price/aws")
    keys = list(
        zip(doc["region_id"], doc["zone_id"], doc["server_id"], doc["allocation"])
    )
    assert keys == sorted(keys)


def test_prices_in_usd(hash_dir, manifest):
    """R9: prices converted to USD, unrounded."""
    for path in manifest["path"]:
        if path.split("/")[0] in snapshots.PRICE_TABLES:
            assert set(_read(hash_dir, path)["currency"]) <= {"USD"}, path
    doc = _read(hash_dir, "server_price/hcloud")
    row = _rows(doc)[0]
    with session.engine.connect() as conn:
        price, currency = conn.execute(
            text(
                "SELECT price, currency FROM server_price WHERE vendor_id = 'hcloud' "
                "AND region_id = :r AND zone_id = :z AND server_id = :s"
            ),
            {"r": row["region_id"], "z": row["zone_id"], "s": row["server_id"]},
        ).first()
    assert currency != "USD"
    assert row["price"] == pytest.approx(currency_converter.convert(price, currency))


def test_references_resolve(hash_dir, manifest):
    """R7: every reference between the snapshots of one hash resolves."""
    servers, databases, zones = set(), set(), set()
    for vendor_id in _vendor_ids(hash_dir):
        servers |= {
            (vendor_id, s) for s in _read(hash_dir, f"server/{vendor_id}")["server_id"]
        }
        databases |= {
            (vendor_id, d)
            for d in _read(hash_dir, f"database/{vendor_id}")["database_id"]
        }
        zone = _read(hash_dir, f"zone/{vendor_id}")
        zones |= set(zip(zone["vendor_id"], zone["region_id"], zone["zone_id"]))
    for vendor_id in _vendor_ids(hash_dir):
        prices = _read(hash_dir, f"server_price/{vendor_id}")
        assert {(vendor_id, s) for s in prices["server_id"]} <= servers
        assert (
            set(zip(prices["vendor_id"], prices["region_id"], prices["zone_id"]))
            <= zones
        )
    by_type = defaultdict(set)
    for path in manifest["path"]:
        if path.startswith("benchmark_score/"):
            scores = _read(hash_dir, path)
            for vendor_id, rtype, rid in zip(
                scores["vendor_id"], scores["resource_type"], scores["resource_id"]
            ):
                by_type[rtype].add((vendor_id, rid))
    assert by_type["server"] and by_type["server"] <= servers
    assert by_type["database"] <= databases


def test_score_config_as_stored(hash_dir, manifest):
    """R11: configs are told apart by their text, kept as stored."""
    path = next(p for p in manifest["path"] if p.startswith("benchmark_score/aws/"))
    _, vendor_id, benchmark_id = path.split("/", 2)
    with gzip.open(snapshots.file_path(hash_dir, path)) as f:
        raw = f.read().decode()
    with session.engine.connect() as conn:
        config = conn.execute(
            text(
                "SELECT config FROM benchmark_score WHERE vendor_id = :v "
                "AND benchmark_id = :b AND status = 'ACTIVE' LIMIT 1"
            ),
            {"v": vendor_id, "b": benchmark_id},
        ).scalar()
    assert config in raw


def test_deterministic_encoding():
    doc = {"a": [1, None, "x"], "b": [snapshots.RawJson('{"k": 1}'), None, "y"]}
    raw, compressed = snapshots.encode_file(doc, mtime=1_700_000_000)
    assert raw == b'{"a":[1,null,"x"],"b":[{"k": 1},null,"y"]}'
    assert snapshots.encode_file(doc, mtime=1_700_000_000)[1] == compressed
    assert json.loads(gzip.decompress(compressed)) == {
        "a": [1, None, "x"],
        "b": [{"k": 1}, None, "y"],
    }


def _hash_dir(root: Path, name: str, age: float, manifest_age=None) -> Path:
    """A hash directory with one data file (and a manifest), aged in seconds."""
    hash_dir = root / name
    files = [snapshots.file_path(hash_dir, "server/aws")]
    if manifest_age is not None:
        files.append(snapshots.file_path(hash_dir, snapshots.MANIFEST_PATH))
    for path in files:
        snapshots.write_atomically(path, b"x")
    _age(files[0], age)
    if manifest_age is not None:
        _age(files[1], manifest_age)
    return hash_dir


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_cleanup(tmp_path):
    """R20 step 3: delete old hashes as a whole, except the hash being loaded."""
    old = _hash_dir(tmp_path, "old", age=3600, manifest_age=3600)
    # the manifest is written last: files can be older than their manifest
    recent = _hash_dir(tmp_path, "recent", age=3600, manifest_age=10)
    partial_old = _hash_dir(tmp_path, "partial-old", age=3600)
    partial_fresh = _hash_dir(tmp_path, "partial-fresh", age=10)
    current = _hash_dir(tmp_path, "current", age=3600, manifest_age=3600)
    # the hash being replaced is kept whatever its age
    previous = _hash_dir(tmp_path, "previous", age=3600, manifest_age=3600)

    snapshots.cleanup(tmp_path, keep_hashes={"current", "previous"}, max_age=60)

    assert not old.exists()
    assert not partial_old.exists()
    assert snapshots.is_complete(recent)
    assert snapshots.file_path(recent, "server/aws").exists()
    assert partial_fresh.exists()
    assert snapshots.is_complete(current)
    assert snapshots.is_complete(previous)


def test_local_hashes(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    _hash_dir(tmp_path, "older", age=100, manifest_age=100)
    _hash_dir(tmp_path, "newer", age=10, manifest_age=10)
    _hash_dir(tmp_path, "partial", age=10)
    (tmp_path / "not-a-dir").write_bytes(b"x")
    assert snapshots.local_hashes() == ["newer", "older"]


def test_unknown_currency_is_reported_once(monkeypatch, caplog):
    """An unconvertible currency is logged once per build, not once per row."""

    def convert(amount, from_currency, to_currency="USD"):
        raise ValueError(f"{from_currency} is not a supported currency")

    monkeypatch.setattr(currency_converter, "convert", convert)
    monkeypatch.setattr(snapshots, "_unconvertible_currencies", set())
    rows = [("XYZ", 1.0), ("XYZ", 2.0), ("XYZ", 3.0)]
    doc = snapshots.columnar(["currency", "price"], rows, usd=True)
    assert doc == {"currency": ["XYZ"] * 3, "price": [1.0, 2.0, 3.0]}
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and "XYZ" in errors[0].getMessage()


def test_booleans_are_true_false(hash_dir):
    """R12: booleans are JSON true/false as in other API responses, not SQLite's 1/0."""
    from sqlalchemy import Boolean

    for path, model in [("vendor", snapshots.Vendor), ("region/aws", snapshots.Region)]:
        doc = _read(hash_dir, path)
        columns = [
            c.name for c in model.__table__.columns if isinstance(c.type, Boolean)
        ]
        assert columns, path
        for column in columns:
            assert {type(v) for v in doc[column]} <= {bool, type(None)}, column
    with gzip.open(snapshots.file_path(hash_dir, "vendor")) as f:
        raw = f.read().decode()
    assert '"stopped_server_charged":[' in raw
    values = raw.split('"stopped_server_charged":[', 1)[1].split("]", 1)[0].split(",")
    assert set(values) <= {"true", "false", "null"}
