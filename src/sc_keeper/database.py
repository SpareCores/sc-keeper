import logging
from collections import deque
from os import environ, unlink
from os.path import abspath
from shutil import copyfile
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread
from time import sleep, time

import safe_exit
from sc_data import db
from sqlmodel import Session, create_engine, delete, insert, text

from . import views
from .indexes import indexes

logger = logging.getLogger(__name__)


class Database(Thread):
    daemon = True

    def __init__(self, *args, **kwargs):
        self.tmpfiles = deque()
        self.db_hash = db.hash
        self.lock = Lock()
        self.updated = Event()
        self.last_updated = None
        self.engine = None
        super().__init__(*args, **kwargs)

    @property
    def ready(self):
        return self.updated.is_set()

    def cleanup(self, keep=0):
        """Delete all SQLite files except for the number specified to keep."""
        while len(self.tmpfiles) > keep:
            tmpfile = self.tmpfiles.popleft()
            logger.debug(f"Deleting {tmpfile}")
            unlink(tmpfile)
        if len(self.tmpfiles) == 0:
            self.updated.clear()

    def update(self, force=False):
        """Copies sc-data's most recent SQLite file, adds index and new tables."""
        if not self.ready or self.db_hash != db.hash or force:
            logger.info(
                f"Found a new version of the SQLite database at {db.path} [{db.hash}]"
            )
            # delete=False due to Windows support
            tmpfile = NamedTemporaryFile(delete=False).name
            copyfile(db.path, tmpfile)
            self.tmpfiles.append(tmpfile)
            # add indexes etc
            engine = create_engine(
                "sqlite:///" + abspath(tmpfile),
                connect_args={"check_same_thread": False},
                echo=bool(environ.get("KEEPER_DEBUG", False)),
            )
            with engine.connect() as conn:
                # minimal gain for read ops with the below PRAGMA configs
                conn.execute(text("PRAGMA synchronous=OFF"))
                conn.execute(text("PRAGMA journal_mode=OFF"))
                conn.execute(text("PRAGMA mmap_size=67108864"))  # 64 MiB
                # speed up some queries with indexes
                for index in indexes:
                    index.create(bind=conn, checkfirst=True)
                # prep and fill ~materialized views
                for t in views.views:
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
            logger.info(f"SQLite database updated {tmpfile}")
            with self.lock:
                self.engine = engine
                self.db_hash = db.hash
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
safe_exit.register(lambda _: session.cleanup(), db.tmpfiles)


def get_db():
    db = session.sessionmaker
    try:
        yield db
    finally:
        db.close()
