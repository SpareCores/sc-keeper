import logging
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from os import environ, unlink
from os.path import abspath
from shutil import copyfile
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread
from time import sleep, time
from typing import Optional

import safe_exit
from sc_data import db
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine, delete, insert, text

from . import snapshot_storage
from .crawler_extend import extenders
from .indexes import indexes
from .views import views

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataVersion:
    """The sc-data file a database was loaded from."""

    hash: Optional[str]
    """None when sc-data doesn't track a hash, e.g. with a custom `SC_DATA_DB_PATH`."""
    published_at: Optional[datetime]
    """When the sc-data file was published (`_metadata.published_at`), in UTC."""
    engine: Optional[Engine] = field(default=None, compare=False, repr=False)
    """The engine of the loaded file, swapped together with the version, so a single
    read gives a matching hash and engine."""


def read_published_at(conn) -> Optional[datetime]:
    """Read the publication time of an sc-data file from its `_metadata` table."""
    try:
        value = conn.execute(
            text("SELECT value FROM _metadata WHERE key = 'published_at'")
        ).scalar()
        if value is None:
            return None
        published_at = datetime.fromisoformat(value)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        return published_at.astimezone(timezone.utc)
    except Exception:
        logger.warning("Could not read published_at from _metadata", exc_info=True)
        return None


# copies of sc-data's file to try when its hash changes during the copy
COPY_ATTEMPTS = 3


def _unlink(path: str) -> None:
    try:
        unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning(f"Could not delete {path}", exc_info=True)


class Database(Thread):
    daemon = True

    def __init__(self, *args, **kwargs):
        self.tmpfiles = deque()
        self.lock = Lock()
        self.updated = Event()
        self.last_updated = None
        self.version: Optional[DataVersion] = None
        super().__init__(*args, **kwargs)

    @property
    def db_hash(self) -> Optional[str]:
        """The hash of the loaded database."""
        version = self.version
        return version.hash if version else None

    @property
    def engine(self) -> Optional[Engine]:
        """The engine of the loaded database."""
        version = self.version
        return version.engine if version else None

    @property
    def ready(self):
        return self.updated.is_set()

    def cleanup(self, keep=0):
        """Delete all SQLite files except for the number specified to keep."""
        with self.lock:
            while len(self.tmpfiles) > keep:
                tmpfile = self.tmpfiles.popleft()
                logger.debug(f"Deleting {tmpfile}")
                unlink(tmpfile)
            if len(self.tmpfiles) == 0:
                self.updated.clear()

    def _load(self, tmpfile: str) -> tuple[Engine, Optional[datetime]]:
        """Prepare a copied SQLite file for serving: add columns, indexes and views."""
        engine = create_engine(
            "sqlite:///" + abspath(tmpfile),
            connect_args={"check_same_thread": False},
            echo=bool(environ.get("KEEPER_DEBUG", False)),
            poolclass=NullPool,
        )
        with engine.connect() as conn:
            # These PRAGMAs apply only to the build phase (write operations).
            # synchronous=OFF and journal_mode=OFF speed up the one-time DB build;
            # mmap_size gives a modest read boost once the engine is reused for queries.
            # Additional PRAGMA tuning (cache_size, temp_store, etc.) showed no significant
            # latency improvement for the read-heavy query workload.
            conn.execute(text("PRAGMA synchronous=OFF"))
            conn.execute(text("PRAGMA journal_mode=OFF"))
            conn.execute(text("PRAGMA mmap_size=67108864"))  # 64 MiB
            # add new columns to the database
            logger.info("Extending tables")
            for extender in extenders:
                logger.info(f"Adding columns to {extender.table.__table__}")
                extender.add_columns(engine)
                extender.update(engine)
            # speed up some queries with indexes
            for index in indexes:
                index.create(bind=conn, checkfirst=True)
            # prep and fill ~materialized views
            for t in views:
                t.__table__.create(engine, checkfirst=True)
                if hasattr(t, "insert"):
                    with Session(engine) as session:
                        t.insert(session)
                        session.commit()
                if hasattr(t, "query"):
                    conn.execute(delete(t))
                    q = insert(t).from_select(
                        t.get_columns()["all"],
                        t.query(),
                    )
                    conn.execute(q)
                    conn.commit()
            conn.execute(text("VACUUM"))
            conn.execute(text("ANALYZE"))
            published_at = read_published_at(conn)
        return engine, published_at

    def _copy(self, tmpfile: str) -> Optional[str]:
        """Copy sc-data's file, and return its hash.

        sc-data replaces its file before it updates its hash, so the hash read before
        the copy may not be the copied file's: a file labelled with another hash would
        serve (and upload) its snapshots under the wrong hash. If the hash changed
        during the copy, copy again.
        """
        for _ in range(COPY_ATTEMPTS):
            db_hash = db.hash
            logger.info(f"Copying {db.path} [{db_hash}] to {tmpfile}")
            copyfile(db.path, tmpfile)
            if db.hash == db_hash:
                return db_hash
            logger.info(
                "sc-data switched to a newer file during the copy, copying again"
            )
        raise RuntimeError(f"sc-data's file kept changing in {COPY_ATTEMPTS} copies")

    def update(self, force=False):
        """Copies sc-data's most recent SQLite file, adds index and new tables."""
        if not self.ready or self.db_hash != db.hash or force:
            logger.info(
                f"Found a new version of the SQLite database at {db.path} [{db.hash}]"
            )
            # delete=False due to Windows support
            tmpfile = NamedTemporaryFile(delete=False).name
            try:
                new_hash = self._copy(tmpfile)
                engine, published_at = self._load(tmpfile)
                logger.info(f"SQLite database updated {tmpfile}")
                # snapshots are available before any response carries the new hash
                snapshot_storage.prepare(
                    engine,
                    new_hash,
                    published_at,
                    previous_hash=self.version.hash if self.version else None,
                )
            except BaseException:
                # a failed copy is never kept: it would count towards the files kept by
                # cleanup(), pushing out the previous file that requests may still use
                _unlink(tmpfile)
                raise
            with self.lock:
                self.tmpfiles.append(tmpfile)
                self.version = DataVersion(
                    hash=new_hash, published_at=published_at, engine=engine
                )
                self.last_updated = time()
                self.updated.set()
            # keep up to 2 files so that queries can run
            # on the old file while we update the new file
            self.cleanup(2)

        else:
            logger.debug("No need to update the database yet")

    def run(self):
        """Start the update thread with 1 min interval."""
        while True:
            try:
                self.update()
            except Exception:
                logger.exception("Failed to update the database")
            sleep(60)

    @property
    def sessionmaker(self):
        self.updated.wait()
        with self.lock:
            return Session(autocommit=False, autoflush=False, bind=self.engine)


session = Database()
session.start()
safe_exit.register(session.cleanup)
session.updated.wait()


# the database version a request started on, set by DataVersionMiddleware
request_version: ContextVar[Optional[DataVersion]] = ContextVar(
    "request_version", default=None
)


def get_db():
    """A session on the database the request started on, or the current one outside
    of requests.

    A request can wait for a thread of the pool while the database switches; using
    the version the request started on keeps its data and its SC-Data-Hash header
    in sync. The previous database file is kept until the next switch.
    """
    version = request_version.get()
    if version is not None and version.engine is not None:
        db = Session(autocommit=False, autoflush=False, bind=version.engine)
    else:
        db = session.sessionmaker
    try:
        yield db
    finally:
        db.close()
