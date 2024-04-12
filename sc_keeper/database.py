from os.path import abspath

from sc_data import db
from sqlmodel import Session, create_engine


class Database:
    db_hash = db.hash
    engine = None

    @property
    def sessionmaker(self):
        if not getattr(self, "engine", None) or self.db_hash != db.hash:
            self.db_hash = db.hash
            self.engine = create_engine(
                "sqlite:///" + abspath(db.path),
                connect_args={"check_same_thread": False},
                echo=True,
            )
        return Session(autocommit=False, autoflush=False, bind=self.engine)


session = Database()
