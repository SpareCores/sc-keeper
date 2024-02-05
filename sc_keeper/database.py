import sys
from sc_data import db
from sqlalchemy.orm import sessionmaker
from sqlmodel import create_engine


class Session:
    db_hash = db.hash
    engine = None
    session_obj = None

    @property
    def sessionmaker(self):
        if not getattr(self, "engine", None) or self.db_hash != db.hash:
            self.db_hash = db.hash
            self.engine = create_engine("sqlite:///" + str(db.path),
                                        connect_args={"check_same_thread": False},
                                        echo=True)
            self.session_obj = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        return self.session_obj


session = Session()
