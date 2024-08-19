from os import environ
from os.path import abspath
from time import time

from sc_data import db
from sqlmodel import Session, create_engine


class Database:
    db_hash = db.hash
    updated = db.updated
    last_updated = None
    engine = None

    @property
    def sessionmaker(self):
        if not getattr(self, "engine", None) or self.db_hash != db.hash:
            self.db_hash = db.hash
            self.last_updated = time()
            self.engine = create_engine(
                "duckdb:///" + abspath(db.path),
                echo=bool(environ.get("KEEPER_DEBUG", False)),
            )
        return Session(autocommit=False, autoflush=False, bind=self.engine)


session = Database()
