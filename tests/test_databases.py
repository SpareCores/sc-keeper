"""Unit tests for GET /databases endpoint."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sc_crawler.table_fields import (
    Allocation,
    DatabaseEngine,
    DatabaseStorageScope,
    PriceUnit,
    Status,
)
from sc_crawler.tables import (
    Country,
    Database,
    DatabasePrice,
    DatabaseStorage,
    DatabaseStoragePrice,
    Region,
    Vendor,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, delete, insert

from sc_keeper.views import Currency, DatabaseExtra

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)

VENDOR_DATA = {
    "vendor_id": "test",
    "name": "TestCloud",
    "homepage": "https://test.example.com",
    "founding_year": 2020,
    "status": Status.ACTIVE,
    "observed_at": NOW,
}

REGION_DATA = {
    "vendor_id": "test",
    "region_id": "us-east-1",
    "name": "US East 1",
    "api_reference": "us-east-1",
    "display_name": "US East 1",
    "country_id": "US",
    "status": Status.ACTIVE,
    "observed_at": NOW,
}


def _make_database(
    database_id: str,
    *,
    vcpus: int = 2,
    memory: int = 4096,
    storage_size: int | None = None,
    ha_supported: bool = True,
):
    return {
        "vendor_id": "test",
        "database_id": database_id,
        "name": database_id,
        "api_reference": database_id,
        "display_name": database_id,
        "description": f"Test database {database_id}",
        "engine": DatabaseEngine.POSTGRESQL,
        "engine_versions": ["15", "16"],
        "vcpus": vcpus,
        "memory_amount": memory,
        "storage_size": storage_size,
        "ha_supported": ha_supported,
        "status": Status.ACTIVE,
        "observed_at": NOW,
    }


_DATABASES = [
    _make_database("db-small", vcpus=2, memory=4096, storage_size=100),
    _make_database("db-large", vcpus=8, memory=32768, storage_size=None),
]

_PRICES = {
    "db-small": 0.10,
    "db-large": 0.50,
}


def _seed_db(session: Session):
    session.add(
        Country(
            country_id="US",
            continent="North America",
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.flush()
    country_us = session.get(Country, "US")

    session.add(Vendor(**VENDOR_DATA, country=country_us))
    session.add(Region(**REGION_DATA))
    session.flush()

    for row in _DATABASES:
        session.add(Database(**row))

    for database_id, price in _PRICES.items():
        session.add(
            DatabasePrice(
                vendor_id="test",
                region_id="us-east-1",
                database_id=database_id,
                allocation=Allocation.ONDEMAND,
                unit=PriceUnit.HOUR,
                price=price,
                currency="USD",
                status=Status.ACTIVE,
                observed_at=NOW,
            )
        )

    session.add(
        DatabaseStorage(
            vendor_id="test",
            database_storage_id="gp3",
            name="gp3",
            scope=DatabaseStorageScope.DATA,
            min_size=10,
            max_size=10000,
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.add(
        DatabaseStoragePrice(
            vendor_id="test",
            region_id="us-east-1",
            database_storage_id="gp3",
            unit=PriceUnit.GB_MONTH,
            price=0.10,
            currency="USD",
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )

    session.add(Currency(base="USD", quote="USD", rate=1.0))
    session.commit()

    session.execute(delete(DatabaseExtra))
    session.execute(
        insert(DatabaseExtra).from_select(
            DatabaseExtra.get_columns()["all"],
            DatabaseExtra.query(),
        )
    )
    session.commit()


@pytest.fixture(scope="module")
def test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    from sc_keeper.crawler_extend import extenders

    for extender in extenders:
        extender.add_columns(engine)
        extender.update(engine)

    with Session(engine) as session:
        _seed_db(session)
    return engine


@pytest.fixture(scope="module")
def client(test_engine):
    from sc_keeper.api import app
    from sc_keeper.database import get_db

    def _override():
        db = Session(autocommit=False, autoflush=False, bind=test_engine)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def get_databases(client, **params):
    resp = client.get("/databases", params=params)
    assert resp.status_code == 200
    return resp.json(), resp


class TestResponseStructure:
    def test_default_returns_list(self, client):
        data, _ = get_databases(client)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_required_fields(self, client):
        data, _ = get_databases(client, limit=1)
        row = data[0]
        for field in [
            "vendor_id",
            "database_id",
            "engine",
            "min_price",
            "vendor",
            "score",
            "score_per_price",
            "price_breakdown",
        ]:
            assert field in row, f"Missing field: {field}"
        assert row["score"] is None
        assert row["score_per_price"] is None

    def test_order_by_min_price_asc(self, client):
        data, _ = get_databases(client, order_by="min_price", order_dir="asc")
        prices = [r["min_price"] for r in data if r["min_price"] is not None]
        assert prices == sorted(prices)

    def test_vcpus_min_filter(self, client):
        data, _ = get_databases(client, vcpus_min=8)
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"

    def test_storage_size_filter(self, client):
        data, _ = get_databases(client, storage_size=50)
        assert len(data) == 1
        assert data[0]["database_id"] == "db-small"

    def test_engine_versions_requires_engine(self, client):
        resp = client.get("/databases", params={"engine_versions": ["15"]})
        assert resp.status_code == 400

    def test_engine_versions_filter(self, client):
        data, _ = get_databases(
            client, engine="postgresql", engine_versions=["15", "16"]
        )
        assert len(data) == 2

    def test_extra_storage_increases_min_price(self, client):
        base, _ = get_databases(client, limit=1, order_by="min_price", order_dir="asc")
        with_extra, _ = get_databases(
            client,
            limit=1,
            order_by="min_price",
            order_dir="asc",
            extra_storage_size=200,
        )
        assert with_extra[0]["min_price"] > base[0]["min_price"]
        pb = with_extra[0]["price_breakdown"]
        assert pb["extra_storage_monthly"] > 0
        assert pb["extra_storage_hourly"] > 0

    def test_bundled_storage_reduces_extra_storage_cost(self, client):
        bundled, _ = get_databases(
            client,
            partial_name_or_id="db-small",
            extra_storage_size=150,
        )
        unbundled, _ = get_databases(
            client,
            partial_name_or_id="db-large",
            extra_storage_size=150,
        )
        assert (
            bundled[0]["price_breakdown"]["extra_storage_monthly"]
            < (unbundled[0]["price_breakdown"]["extra_storage_monthly"])
        )

    def test_total_count_header(self, client):
        _, resp = get_databases(client, add_total_count_header=True, limit=1)
        assert resp.headers.get("X-Total-Count") == "2"


class TestLiveIntegration:
    """Smoke tests against the real sc-data database when available."""

    def test_live_returns_databases(self):
        from sc_keeper.api import app
        from sc_keeper.database import get_db

        override = app.dependency_overrides.pop(get_db, None)
        try:
            resp = TestClient(app).get("/databases", params={"limit": 5})
        finally:
            if override is not None:
                app.dependency_overrides[get_db] = override

        if resp.status_code != 200:
            pytest.skip("Live database not available")
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        vendors = {row["vendor_id"] for row in data}
        assert vendors & {"aws", "azure", "gcp"}
