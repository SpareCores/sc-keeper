"""Shared snapshot storage in S3 (sc-specs 2026-11-08-keeper-snapshots.md R19-R21)."""

import gzip
import io
import json
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from sc_keeper import snapshot_storage, snapshots
from sc_keeper.database import session


class FakeS3:
    """The subset of the S3 client the store uses, with S3's error codes."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts = 0

    def download_fileobj(self, bucket, key, fileobj):
        if (bucket, key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        fileobj.write(self.objects[(bucket, key)])

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None):
        self.puts += 1
        if IfNoneMatch == "*" and (Bucket, Key) in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[(Bucket, Key)] = Body.read()


class BrokenS3(FakeS3):
    def download_fileobj(self, bucket, key, fileobj):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")


@pytest.fixture(scope="module")
def built_dir() -> Path:
    """A complete build: the snapshots of the loaded test database."""
    path = snapshots.snapshot_dir() / session.version.hash
    assert snapshots.is_complete(path)
    return path


def _small_build(hash_dir: Path) -> None:
    """A tiny complete build, for tests that build."""
    files = [snapshots._write(hash_dir, "country", {"country_id": ["DE"]}, 0)]
    manifest = {
        "path": [f.path for f in files],
        "rows": [f.rows for f in files],
        "sha256": [f.sha256 for f in files],
    }
    snapshots._write(hash_dir, snapshots.MANIFEST_PATH, manifest, 0)


@pytest.fixture
def local_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake_store(monkeypatch):
    shared = snapshot_storage.S3Store("s3://bucket/main", client=FakeS3())
    monkeypatch.setattr(snapshot_storage, "store", lambda: shared)
    return shared


def test_store_uri():
    s = snapshot_storage.S3Store("s3://bucket/main/")
    assert (s.bucket, s.key("abc")) == ("bucket", "main/abc.zip")
    assert snapshot_storage.S3Store("s3://bucket").key("abc") == "abc.zip"
    with pytest.raises(ValueError):
        snapshot_storage.S3Store("https://bucket/main")


def test_store_from_env(monkeypatch):
    monkeypatch.delenv("SNAPSHOT_S3_URI", raising=False)
    assert snapshot_storage.store() is None
    monkeypatch.setenv("SNAPSHOT_S3_URI", "s3://bucket/main")
    assert snapshot_storage.store().bucket == "bucket"


def test_upload_download_roundtrip(built_dir, tmp_path):
    """The archive holds every file as built; the first upload wins (R19, R20)."""
    shared = snapshot_storage.S3Store("s3://bucket/main", client=FakeS3())
    assert shared.upload("h1", built_dir) is True
    assert shared.upload("h1", built_dir) is False
    with zipfile.ZipFile(
        io.BytesIO(shared.client.objects[("bucket", "main/h1.zip")])
    ) as zf:
        assert all(info.compress_type == zipfile.ZIP_STORED for info in zf.infolist())
        assert zf.namelist()[-1] == "manifest.json.gz"

    target = tmp_path / "h1"
    assert shared.download("h1", target) is True
    assert snapshots.is_complete(target)
    with gzip.open(snapshots.file_path(target, snapshots.MANIFEST_PATH)) as f:
        paths = json.load(f)["path"]
    for path in paths[:20] + [snapshots.MANIFEST_PATH]:
        assert (
            snapshots.file_path(target, path).read_bytes()
            == snapshots.file_path(built_dir, path).read_bytes()
        )
    assert shared.download("missing", tmp_path / "missing") is False


def test_download_error_is_raised(tmp_path):
    shared = snapshot_storage.S3Store("s3://bucket/main", client=BrokenS3())
    with pytest.raises(ClientError):
        shared.download("h1", tmp_path / "h1")


@pytest.mark.parametrize("name", ["../evil.json.gz", "/abs.json.gz", "notes.txt"])
def test_unpack_rejects_unexpected_files(tmp_path, name):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(name, b"x")
        zf.writestr("manifest.json.gz", b"x")
    buffer.seek(0)
    with pytest.raises(ValueError):
        snapshot_storage.unpack(buffer, tmp_path / "h1")
    assert not (tmp_path / "evil.json.gz").exists()


def test_prepare_downloads_instead_of_building(
    built_dir, local_dir, fake_store, monkeypatch
):
    """R20 step 1: another replica's archive is downloaded, not rebuilt."""
    fake_store.upload("h1", built_dir)
    monkeypatch.setattr(snapshots, "build", _fail)
    snapshot_storage.prepare(session.engine, "h1", None)
    assert snapshots.is_complete(local_dir / "h1")


def test_prepare_builds_and_uploads(local_dir, fake_store, monkeypatch):
    """R20 step 2: no archive yet, so build once and upload."""
    monkeypatch.setattr(
        snapshots, "build", lambda conn, hash_dir, _: _small_build(hash_dir)
    )
    snapshot_storage.prepare(session.engine, "h2", None)
    assert snapshots.is_complete(local_dir / "h2")
    assert ("bucket", "main/h2.zip") in fake_store.client.objects


def test_prepare_survives_failures(local_dir, monkeypatch, caplog):
    """R20: failures are reported as errors, and the switch proceeds."""
    shared = snapshot_storage.S3Store("s3://bucket/main", client=BrokenS3())
    monkeypatch.setattr(snapshot_storage, "store", lambda: shared)
    monkeypatch.setattr(snapshots, "build", _fail)
    snapshot_storage.prepare(session.engine, "h3", None)
    assert not snapshots.is_complete(local_dir / "h3")
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("download" in m for m in errors)
    assert any("build" in m for m in errors)


def test_ensure(built_dir, local_dir, fake_store, monkeypatch):
    """R21: download any hash from S3; build only the current database's."""
    builds = []

    def build(conn, hash_dir, _):
        builds.append(hash_dir.name)
        _small_build(hash_dir)

    monkeypatch.setattr(snapshots, "build", build)
    fake_store.upload("in-s3", built_dir)
    assert snapshot_storage.ensure("in-s3", "current", session.engine, None)
    assert not snapshot_storage.ensure("unknown", "current", session.engine, None)
    assert snapshot_storage.ensure("current", "current", session.engine, None)
    assert builds == ["current"]
    # already complete locally: no further download or build
    shutil.rmtree(local_dir / "in-s3" / "server")
    assert snapshot_storage.ensure("in-s3", "current", session.engine, None)


def _fail(*args, **kwargs):
    raise RuntimeError("should not build")


def test_available_serves_a_quick_download(built_dir, local_dir, fake_store):
    """R21: a download that finishes within the wait is served right away."""
    fake_store.upload("in-s3", built_dir)
    assert snapshot_storage.available("in-s3", "current", session.engine, None, wait=5)


def test_available_doesnt_wait_for_a_build(local_dir, fake_store, monkeypatch):
    """R21: a build longer than the wait runs in the background, once per hash."""
    started, release = threading.Event(), threading.Event()
    builds = []

    def slow_build(conn, hash_dir, _):
        builds.append(hash_dir.name)
        started.set()
        release.wait(10)
        _small_build(hash_dir)

    monkeypatch.setattr(snapshots, "build", slow_build)
    t0 = time.time()
    assert not snapshot_storage.available(
        "current", "current", session.engine, None, wait=0.2
    )
    assert not snapshot_storage.available(
        "current", "current", session.engine, None, wait=0.2
    )
    assert time.time() - t0 < 2
    assert started.wait(5)
    release.set()
    for _ in range(50):
        if snapshots.is_complete(local_dir / "current"):
            break
        time.sleep(0.1)
    assert snapshot_storage.available(
        "current", "current", session.engine, None, wait=0
    )
    assert builds == ["current"]


def test_available_never_builds_another_hash(local_dir, fake_store, monkeypatch):
    monkeypatch.setattr(snapshots, "build", _fail)
    assert not snapshot_storage.available(
        "other", "current", session.engine, None, wait=1
    )


def test_failed_build_isnt_retried_right_away(local_dir, monkeypatch):
    """After a failed build, the same hash isn't built again for a while."""
    attempts = []

    def failing_build(conn, hash_dir, _):
        attempts.append(hash_dir.name)
        raise RuntimeError("build failed")

    monkeypatch.setattr(snapshot_storage, "store", lambda: None)
    monkeypatch.setattr(snapshots, "build", failing_build)
    monkeypatch.setattr(snapshot_storage, "_failed_builds", {})
    assert not snapshot_storage.ensure("current", "current", session.engine, None)
    assert not snapshot_storage.ensure("current", "current", session.engine, None)
    assert attempts == ["current"]
    # once the retry interval passed, it's built again
    snapshot_storage._failed_builds["current"] -= snapshot_storage.BUILD_RETRY_SECONDS
    monkeypatch.setattr(
        snapshots, "build", lambda conn, hash_dir, _: _small_build(hash_dir)
    )
    assert snapshot_storage.ensure("current", "current", session.engine, None)
    assert "current" not in snapshot_storage._failed_builds


def test_prepare_keeps_the_previous_hash(local_dir, monkeypatch):
    """The hash being replaced survives the cleanup, whatever its age."""
    monkeypatch.setattr(snapshot_storage, "store", lambda: None)
    monkeypatch.setattr(
        snapshots, "build", lambda conn, hash_dir, _: _small_build(hash_dir)
    )
    for name in ("previous", "older"):
        _small_build(local_dir / name)
        for path in (local_dir / name).rglob("*"):
            os.utime(path, (0, 0))
    snapshot_storage.prepare(session.engine, "new", None, previous_hash="previous")
    assert snapshots.is_complete(local_dir / "new")
    assert snapshots.is_complete(local_dir / "previous")
    assert not (local_dir / "older").exists()
