"""Snapshot storage: local files, a shared S3 bucket, or on-the-fly build.

Each hash is one zip archive in S3, `<SNAPSHOT_S3_URI>/<hash>.zip`, holding the
hash's local files as they are (already gzipped, stored without recompression)
and its manifest. The first replica that loads a hash builds and uploads it; the
others download it.
"""

import gzip
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import IO, Optional
from urllib.parse import urlparse

from sqlalchemy.engine import Engine

from . import snapshots

logger = logging.getLogger(__name__)

# names inside an archive: a snapshot route plus the file suffix, nothing else
_MEMBER_NAME = re.compile(r"^[A-Za-z0-9_.:~-]+(/[A-Za-z0-9_.:~-]+)*\.json\.gz$")


class S3Store:
    """A hash's snapshots as one zip archive in an S3 bucket."""

    def __init__(self, uri: str, client=None):
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError(f"Expected s3://bucket/prefix, got {uri!r}")
        self.bucket = parsed.netloc
        self.prefix = parsed.path.strip("/")
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def key(self, db_hash: str) -> str:
        name = f"{db_hash}.zip"
        return f"{self.prefix}/{name}" if self.prefix else name

    def download(self, db_hash: str, hash_dir: Path) -> bool:
        """Download and unpack a hash's archive. False if there is none."""
        from botocore.exceptions import ClientError

        with tempfile.TemporaryFile() as tmp:
            try:
                self.client.download_fileobj(self.bucket, self.key(db_hash), tmp)
            except ClientError as e:
                if _error_code(e) in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise
            tmp.seek(0)
            unpack(tmp, hash_dir)
        return True

    def upload(self, db_hash: str, hash_dir: Path) -> bool:
        """Upload a hash's archive unless one exists already. True if uploaded."""
        from botocore.exceptions import ClientError

        with tempfile.TemporaryFile() as tmp:
            pack(hash_dir, tmp)
            tmp.seek(0)
            try:
                # the first upload wins: S3 rejects this one if the key exists
                self.client.put_object(
                    Bucket=self.bucket, Key=self.key(db_hash), Body=tmp, IfNoneMatch="*"
                )
            except ClientError as e:
                if _error_code(e) in (
                    "PreconditionFailed",
                    "412",
                    "ConditionalRequestConflict",
                    "409",
                ):
                    return False
                raise
        return True


def _error_code(error) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _member_name(path: str) -> str:
    return f"{path}{snapshots.FILE_SUFFIX}"


def pack(hash_dir: Path, fileobj: IO[bytes]) -> None:
    """Zip a complete build: every file of its manifest, then the manifest."""
    manifest_file = snapshots.file_path(hash_dir, snapshots.MANIFEST_PATH)
    with gzip.open(manifest_file) as f:
        paths = json.load(f)["path"]
    with zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in [*paths, snapshots.MANIFEST_PATH]:
            zf.write(snapshots.file_path(hash_dir, path), _member_name(path))


def unpack(fileobj: IO[bytes], hash_dir: Path) -> None:
    """Unpack an archive into a hash's directory, the manifest last."""
    manifest_name = _member_name(snapshots.MANIFEST_PATH)
    with zipfile.ZipFile(fileobj) as zf:
        members = zf.infolist()
        for member in members:
            if not _MEMBER_NAME.match(member.filename) or ".." in member.filename:
                raise ValueError(
                    f"Unexpected file in snapshot archive: {member.filename}"
                )
        if manifest_name not in {m.filename for m in members}:
            raise ValueError("Snapshot archive has no manifest")
        for member in sorted(members, key=lambda m: m.filename == manifest_name):
            snapshots.write_atomically(hash_dir / member.filename, zf.read(member))


_store: Optional[S3Store] = None
_store_uri: Optional[str] = None


def store() -> Optional[S3Store]:
    """The shared store configured by `SNAPSHOT_S3_URI`, if any."""
    global _store, _store_uri
    uri = os.environ.get("SNAPSHOT_S3_URI") or None
    if uri != _store_uri:
        _store, _store_uri = (S3Store(uri) if uri else None), uri
    return _store


# ##############################################################################
# Loading and serving


# per hash: the lock and how many threads hold it or wait for it
_locks: dict[str, list] = {}
_locks_lock = Lock()


@contextmanager
def _lock(db_hash: str):
    """One download or build per hash at a time (R21).

    A hash's entry is removed once no thread holds or waits for its lock, so
    requests for many different (e.g. made-up) hashes don't grow memory.
    """
    with _locks_lock:
        entry = _locks.setdefault(db_hash, [Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _locks_lock:
            entry[1] -= 1
            if entry[1] == 0:
                _locks.pop(db_hash, None)


def _download(db_hash: str, hash_dir: Path) -> bool:
    shared = store()
    if shared is None:
        return False
    try:
        return shared.download(db_hash, hash_dir) and snapshots.is_complete(hash_dir)
    except Exception:
        logger.exception(f"Failed to download the snapshots of {db_hash}")
        return False


# after a failed build, don't build the same hash again for this long: requests
# would otherwise start one ~25 second build after the other while it keeps failing
BUILD_RETRY_SECONDS = 5 * 60
_failed_builds: dict[str, float] = {}


def _build_and_upload(
    engine: Engine, db_hash: str, hash_dir: Path, published_at: Optional[datetime]
) -> bool:
    failed = _failed_builds.get(db_hash)
    if failed is not None and time.time() - failed < BUILD_RETRY_SECONDS:
        return False
    try:
        with engine.connect() as conn:
            snapshots.build(conn, hash_dir, published_at)
    except Exception:
        logger.exception(f"Failed to build the snapshots of {db_hash}")
        _failed_builds[db_hash] = time.time()
        return False
    _failed_builds.pop(db_hash, None)
    shared = store()
    if shared is not None:
        try:
            if not shared.upload(db_hash, hash_dir):
                logger.info(f"Snapshots of {db_hash} were already uploaded")
        except Exception:
            logger.exception(f"Failed to upload the snapshots of {db_hash}")
    return True


def prepare(
    engine: Engine,
    db_hash: Optional[str],
    published_at: Optional[datetime],
    previous_hash: Optional[str] = None,
) -> None:
    """Make a database's snapshots available before switching to it (R20).

    Download them from S3, or else build them once (and upload them). Failures are
    logged as errors (reported to Sentry) and don't stop the switch: serving tries
    again on request (R21).
    """
    if db_hash is None:
        logger.info("No database hash (custom SC_DATA_DB_PATH?), skipping snapshots")
        return
    root = snapshots.snapshot_dir()
    hash_dir = root / db_hash
    with _lock(db_hash):
        if not snapshots.is_complete(hash_dir) and not _download(db_hash, hash_dir):
            _build_and_upload(engine, db_hash, hash_dir, published_at)
    try:
        snapshots.cleanup(
            root,
            # keep the hash being replaced too, whatever its age: clients may be
            # partway through a refresh with it; the next load deletes it
            keep_hashes={db_hash, previous_hash} - {None},
            max_age=snapshots.local_retention_seconds(),
        )
    except Exception:
        logger.exception("Failed to clean up local snapshots")


def ensure(
    db_hash: str,
    current_hash: Optional[str],
    engine: Optional[Engine],
    published_at: Optional[datetime],
) -> bool:
    """Make a hash's snapshots available locally, if possible (R21).

    `current_hash`, `engine` and `published_at` must come from the same version of
    the replica's database: building a hash from another database's engine would
    write (and upload) wrong data under that hash.

    Download them from S3, or, for the replica's current database, build them once.
    Returns whether the hash's build is complete locally.
    """
    hash_dir = snapshots.snapshot_dir() / db_hash
    if snapshots.is_complete(hash_dir):
        return True
    with _lock(db_hash):
        if snapshots.is_complete(hash_dir) or _download(db_hash, hash_dir):
            return True
        if db_hash == current_hash and engine is not None:
            return _build_and_upload(engine, db_hash, hash_dir, published_at)
    return False


# ##############################################################################
# Serving without holding request threads

# R21: requests for a hash that isn't local wait for its download or build for at
# most SNAPSHOT_REQUEST_WAIT_SECONDS, so request threads, shared with every other
# endpoint, are never held for a whole build. A job still running after the wait is
# answered as "preparing" (503, retry the same hash), one that finished without the
# hash as "not available" (404, re-pin to `current`).

READY = "ready"
PREPARING = "preparing"
UNAVAILABLE = "unavailable"


def request_wait_seconds() -> float:
    return float(os.environ.get("SNAPSHOT_REQUEST_WAIT_SECONDS", 3))


_jobs: dict[str, Event] = {}
_jobs_lock = Lock()


def _job(
    db_hash: str,
    current_hash: Optional[str],
    engine: Optional[Engine],
    published_at: Optional[datetime],
    done: Event,
) -> None:
    try:
        ensure(db_hash, current_hash, engine, published_at)
    except Exception:
        logger.exception(f"Failed to make the snapshots of {db_hash} available")
    finally:
        with _jobs_lock:
            _jobs.pop(db_hash, None)
        done.set()


def available(
    db_hash: str,
    current_hash: Optional[str],
    engine: Optional[Engine],
    published_at: Optional[datetime],
    wait: Optional[float] = None,
) -> str:
    """Whether a hash's build is complete locally, for serving (R21).

    Returns `READY` if it is. If it isn't, a background job downloads it from S3,
    or builds the replica's current database once (see `ensure`), and this waits
    for that job for at most `wait` seconds (`SNAPSHOT_REQUEST_WAIT_SECONDS`,
    default 3). Request threads never wait on the per-hash lock nor for a whole
    build, so a burst of snapshot requests can't use up the thread pool that serves
    every other endpoint.

    Returns `READY` if the job made the hash complete, `PREPARING` if the job is
    still running (a download that takes longer, or a build, about 25 seconds), and
    `UNAVAILABLE` if it finished without the hash (no archive in S3, a hash other
    than the current one, or a build that failed or recently failed).
    """
    hash_dir = snapshots.snapshot_dir() / db_hash
    if snapshots.is_complete(hash_dir):
        return READY
    with _jobs_lock:
        done = _jobs.get(db_hash)
        if done is None:
            done = Event()
            _jobs[db_hash] = done
            Thread(
                target=_job,
                args=(db_hash, current_hash, engine, published_at, done),
                name=f"snapshot-{db_hash[:12]}",
                daemon=True,
            ).start()
    finished = done.wait(request_wait_seconds() if wait is None else wait)
    if snapshots.is_complete(hash_dir):
        return READY
    return UNAVAILABLE if finished else PREPARING
