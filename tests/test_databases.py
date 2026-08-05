"""Unit tests for GET /databases endpoint."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sc_crawler.table_fields import (
    Allocation,
    DatabaseEngine,
    DatabaseHaLevel,
    DatabaseHaStrategy,
    DatabaseSecurityFeature,
    DatabaseStorageScope,
    DatabaseWireProtocol,
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
    ha: list | None = None,
    ha_strategy: list | None = None,
    storage_extra_min: int | None = 10,
    storage_extra_max: int | None = 10000,
    max_read_replicas: int = 5,
    security_features: list | None = None,
):
    return {
        "vendor_id": "test",
        "database_id": database_id,
        "name": database_id,
        "api_reference": database_id,
        "display_name": database_id,
        "description": f"Test database {database_id}",
        "engine": DatabaseEngine.POSTGRESQL,
        "wire_protocol": DatabaseWireProtocol.POSTGRESQL,
        "engine_versions": ["15", "16"],
        "auto_upgrade_versions": True,
        "vcpus": vcpus,
        "memory_amount": memory,
        "storage_size": storage_size,
        "storage_extra_min": storage_extra_min,
        "storage_extra_max": storage_extra_max,
        "storage_extra_autosize": True,
        "disk_encryption": True,
        "ha": ha or [DatabaseHaLevel.MULTI_ZONE, DatabaseHaLevel.NONE],
        "ha_strategy": ha_strategy
        or [DatabaseHaStrategy.PASSIVE_STANDBY, DatabaseHaStrategy.NONE],
        "max_read_replicas": max_read_replicas,
        "connection_pool": True,
        "system_monitoring": True,
        "database_monitoring": True,
        "autotuning_advice": True,
        "autotuning_apply": False,
        "custom_config": True,
        "custom_extensions": True,
        "security_features": security_features
        or [DatabaseSecurityFeature.IP_FILTERING],
        "status": Status.ACTIVE,
        "observed_at": NOW,
    }


_DATABASES = [
    _make_database(
        "db-small",
        vcpus=2,
        memory=4096,
        storage_size=100,
        storage_extra_min=5,
        storage_extra_max=50,
    ),
    _make_database(
        "db-large",
        vcpus=8,
        memory=32768,
        storage_size=None,
        ha=[DatabaseHaLevel.MULTI_REGION, DatabaseHaLevel.MULTI_ZONE],
        ha_strategy=[DatabaseHaStrategy.MULTI_MASTER],
        max_read_replicas=15,
        storage_extra_min=10,
        storage_extra_max=10000,
        security_features=[
            DatabaseSecurityFeature.IP_FILTERING,
            DatabaseSecurityFeature.NETWORK_PEERING,
        ],
    ),
]

_PRICES = {
    "db-small": [
        {
            "ha": DatabaseHaLevel.NONE,
            "ha_strategy": DatabaseHaStrategy.NONE,
            "price": 0.10,
        },
        {
            "ha": DatabaseHaLevel.MULTI_ZONE,
            "ha_strategy": DatabaseHaStrategy.PASSIVE_STANDBY,
            "price": 0.20,
        },
    ],
    "db-large": [
        {
            "ha": DatabaseHaLevel.MULTI_REGION,
            "ha_strategy": DatabaseHaStrategy.MULTI_MASTER,
            "price": 0.50,
        },
    ],
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

    for database_id, price_rows in _PRICES.items():
        for price_row in price_rows:
            session.add(
                DatabasePrice(
                    vendor_id="test",
                    region_id="us-east-1",
                    database_id=database_id,
                    allocation=Allocation.ONDEMAND,
                    ha=price_row["ha"],
                    ha_strategy=price_row["ha_strategy"],
                    unit=PriceUnit.HOUR,
                    price=price_row["price"],
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
            min_size=1,
            max_size=70369,
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

    from sc_keeper.crawler_extend import DatabasePriceExtender

    DatabasePriceExtender().update(session.get_bind())

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

    def test_order_by_unknown_field(self, client):
        resp = client.get("/databases", params={"order_by": "not_a_column"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unknown order_by field."

    def test_currency_eur_converts_prices(self, client):
        # db-small: 100 bundled + 50 max extra → request must fit within 150
        usd, _ = get_databases(
            client,
            partial_name_or_id="db-small",
            extra_storage_size=150,
            currency="USD",
        )
        eur, _ = get_databases(
            client,
            partial_name_or_id="db-small",
            extra_storage_size=150,
            currency="EUR",
        )
        assert usd[0]["min_price"] != eur[0]["min_price"]
        assert (
            usd[0]["price_breakdown"]["compute_min_price"]
            != eur[0]["price_breakdown"]["compute_min_price"]
        )
        assert (
            usd[0]["price_breakdown"]["extra_storage_hourly"]
            != eur[0]["price_breakdown"]["extra_storage_hourly"]
        )
        assert (
            usd[0]["price_breakdown"]["extra_storage_monthly"]
            != eur[0]["price_breakdown"]["extra_storage_monthly"]
        )


class TestFiltersAndPricing:
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

    def test_ha_filter(self, client):
        data, _ = get_databases(client, ha=["multi-region"])
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"
        assert "multi-region" in data[0]["ha"]

    def test_ha_strategy_filter(self, client):
        data, _ = get_databases(client, ha_strategy=["multi-master"])
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"
        assert "multi-master" in data[0]["ha_strategy"]

    def test_min_price_across_ha_price_rows(self, client):
        data, _ = get_databases(client, partial_name_or_id="db-small")
        assert len(data) == 1
        assert data[0]["min_price"] == 0.1
        assert data[0]["min_price_ondemand"] == 0.1
        assert data[0]["min_price_ondemand_monthly"] == 73.0

    def test_wire_protocol_filter(self, client):
        data, _ = get_databases(client, wire_protocol=["postgresql"])
        assert len(data) == 2

    def test_max_read_replicas_min_filter(self, client):
        data, _ = get_databases(client, max_read_replicas_min=10)
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"

    def test_storage_extra_autosize_filter(self, client):
        data, _ = get_databases(client, storage_extra_autosize=True)
        assert len(data) == 2

    def test_security_features_filter(self, client):
        data, _ = get_databases(
            client, security_features=["ip-filtering", "network-peering"]
        )
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"

    def test_autotuning_apply_filter(self, client):
        data, _ = get_databases(client, autotuning_apply=False)
        assert len(data) == 2
        none, _ = get_databases(client, autotuning_apply=True)
        assert len(none) == 0

    def test_extra_storage_increases_min_price(self, client):
        base, _ = get_databases(client, partial_name_or_id="db-large")
        with_extra, _ = get_databases(
            client,
            partial_name_or_id="db-large",
            extra_storage_size=200,
        )
        assert with_extra[0]["min_price"] > base[0]["min_price"]
        pb = with_extra[0]["price_breakdown"]
        assert pb["extra_storage_monthly"] > 0
        assert pb["extra_storage_hourly"] > 0

    def test_storage_extra_min_floor(self, client):
        # db-small: 100 GB bundled, storage_extra_min=5 → need 101 bills 5 GB extra
        data, _ = get_databases(
            client,
            partial_name_or_id="db-small",
            extra_storage_size=101,
        )
        assert len(data) == 1
        assert data[0]["price_breakdown"]["extra_storage_monthly"] == 0.5  # 5 * 0.10

    def test_storage_extra_max_filters_out(self, client):
        # db-small: 100 + 50 max = 150 → 151 excluded, 150 kept
        excluded, _ = get_databases(
            client,
            partial_name_or_id="db-small",
            extra_storage_size=151,
        )
        assert excluded == []

        included, _ = get_databases(
            client,
            partial_name_or_id="db-small",
            extra_storage_size=150,
        )
        assert len(included) == 1
        assert (
            included[0]["price_breakdown"]["extra_storage_monthly"] == 5.0
        )  # 50 * 0.10

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

    def test_best_price_allocation_spot_only_rejected(self, client):
        resp = client.get("/databases", params={"best_price_allocation": "SPOT_ONLY"})
        assert resp.status_code == 400
        assert "SPOT_ONLY" in resp.json()["detail"]

    def test_best_price_allocation_ondemand_only(self, client):
        data, _ = get_databases(
            client,
            partial_name_or_id="db-small",
            best_price_allocation="ONDEMAND_ONLY",
        )
        row = data[0]
        assert row["min_price"] == row["min_price_ondemand"]

    def test_best_price_allocation_monthly(self, client):
        data, _ = get_databases(
            client,
            partial_name_or_id="db-small",
            best_price_allocation="MONTHLY",
        )
        row = data[0]
        assert row["min_price"] == row["min_price_ondemand_monthly"]
        assert row["min_price_ondemand_monthly"] > row["min_price_ondemand"]

    def test_database_storage_prices(self, client):
        resp = client.get(
            "/database_storage_prices",
            params={"limit": 10, "add_total_count_header": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        row = data[0]
        assert row["vendor_id"] == "test"
        assert row["database_storage_id"] == "gp3"
        assert "vendor" in row
        assert "region" in row
        assert "database_storage" in row
        assert resp.headers.get("X-Total-Count") == "1"

    def test_database_storage_prices_storage_min(self, client):
        resp = client.get(
            "/database_storage_prices",
            params={"storage_min": 50},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = client.get(
            "/database_storage_prices",
            params={"storage_min": 200000},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 0


class TestLiveIntegration:
    """Smoke tests against the real sc-data database when available."""

    def test_live_returns_databases(self):
        from sc_keeper.api import app
        from sc_keeper.database import get_db

        override = app.dependency_overrides.pop(get_db, None)
        try:
            resp = TestClient(app, raise_server_exceptions=False).get(
                "/databases", params={"limit": 5}
            )
        finally:
            if override is not None:
                app.dependency_overrides[get_db] = override

        if resp.status_code != 200:
            pytest.skip("Live database not available or schema outdated")
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_live_database_detail(self):
        from sc_keeper.api import app
        from sc_keeper.database import get_db

        override = app.dependency_overrides.pop(get_db, None)
        try:
            resp = TestClient(app, raise_server_exceptions=False).get(
                "/database/aws/db.t3.small"
            )
        finally:
            if override is not None:
                app.dependency_overrides[get_db] = override

        if resp.status_code != 200:
            pytest.skip("Live database not available or schema outdated")
        data = resp.json()
        assert data["vendor_id"] == "aws"
        assert data["database_id"] == "db.t3.small"
        assert "vendor" not in data

    def test_live_database_prices(self):
        from sc_keeper.api import app
        from sc_keeper.database import get_db

        override = app.dependency_overrides.pop(get_db, None)
        try:
            resp = TestClient(app, raise_server_exceptions=False).get(
                "/database/aws/db.t3.small/prices", params={"currency": "EUR"}
            )
        finally:
            if override is not None:
                app.dependency_overrides[get_db] = override

        if resp.status_code != 200:
            pytest.skip("Live database not available or schema outdated")
        data = resp.json()
        assert len(data) > 0
        assert data[0]["database_id"] == "db.t3.small"
        assert data[0]["currency"] == "EUR"
        assert "region" not in data[0]
