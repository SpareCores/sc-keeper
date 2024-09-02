from os import environ
from os.path import abspath
from threading import Lock
from time import time

from sc_data import db
from sqlmodel import Session, create_engine, delete, insert, text

from .indexes import indexes
from . import views


class Database:
    db_hash = db.hash
    lock = Lock()
    updated = db.updated
    last_updated = None
    engine = None

    @property
    def sessionmaker(self):
        with self.lock:
            if not getattr(self, "engine", None) or self.db_hash != db.hash:
                self.db_hash = db.hash
                self.last_updated = time()
                self.engine = create_engine(
                    "sqlite:///" + abspath(db.path),
                    connect_args={"check_same_thread": False},
                    echo=bool(environ.get("KEEPER_DEBUG", False)),
                )
                with self.engine.connect() as conn:
                    # speed up some queries with indexes
                    for index in indexes:
                        index.create(bind=conn, checkfirst=True)
                    # prep and fill ~materialized views
                    for t in views.views:
                        t.__table__.create(self.engine, checkfirst=True)
                        if hasattr(t, "insert"):
                            with Session(self.engine) as session:
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

        return Session(autocommit=False, autoflush=False, bind=self.engine)


session = Database()


def get_db():
    db = session.sessionmaker
    try:
        yield db
    finally:
        db.close()
