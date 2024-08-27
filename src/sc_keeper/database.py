from os import environ
from os.path import abspath
from threading import Lock
from time import time

from sc_data import db
from sqlmodel import Session, create_engine, text


class Database:
    db_hash = db.hash
    updating = Lock()
    updated = db.updated
    last_updated = None
    engine = None

    @property
    def sessionmaker(self):
        with self.updating:
            if not getattr(self, "engine", None) or self.db_hash != db.hash:
                self.db_hash = db.hash
                self.last_updated = time()
                self.engine = create_engine(
                    "sqlite:///" + abspath(db.path),
                    connect_args={"check_same_thread": False},
                    echo=bool(environ.get("KEEPER_DEBUG", False)),
                )
                with self.engine.connect() as conn:
                    for index_create in [
                        "CREATE INDEX IF NOT EXISTS server_price_idx_4be28cc1 ON server_price(vendor_id, price)",
                        "CREATE INDEX IF NOT EXISTS server_price_idx_dd929fc9 ON server_price(vendor_id, server_id)",
                        "CREATE INDEX IF NOT EXISTS server_price_idx_6f0ddbb8 ON server_price(vendor_id, server_id, price)",
                        "CREATE INDEX IF NOT EXISTS server_price_idx_3902126d ON server_price(server_id, vendor_id, region_id)",
                        "CREATE INDEX IF NOT EXISTS server_price_idx_f4994df3 ON server_price(allocation, vendor_id, server_id)",
                        "CREATE INDEX IF NOT EXISTS server_idx_447dcc29 ON server(status, vcpus)",
                        "CREATE INDEX IF NOT EXISTS server_idx_68282de9 ON server(status, server_id, vendor_id)",
                        "CREATE INDEX IF NOT EXISTS benchmark_score_idx_979d2124 ON benchmark_score(benchmark_id, vendor_id, server_id)",
                        "VACUUM",
                    ]:
                        conn.execute(text(index_create))

        return Session(autocommit=False, autoflush=False, bind=self.engine)


session = Database()


def get_db():
    db = session.sessionmaker
    try:
        yield db
    finally:
        db.close()
