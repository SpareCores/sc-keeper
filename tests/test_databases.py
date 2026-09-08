"""Tests for GET /databases: seeded in-memory DB and live sc-data."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sc_crawler.table_fields import (
    Allocation,
    CpuAllocation,
    CpuArchitecture,
    DatabaseEngine,
    DatabaseHaLevel,
    DatabaseHaStrategy,
    DatabaseSecurityFeature,
    DatabaseStorageScope,
    DatabaseWireProtocol,
    HashableDict,
    PriceUnit,
    ResourceType,
    Status,
)
from sc_crawler.tables import (
    BenchmarkScore,
    Country,
    Database,
    DatabasePrice,
    DatabaseStorage,
    DatabaseStoragePrice,
    Region,
    Server,
    Vendor,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, delete, insert

from sc_keeper.api import app
from sc_keeper.views import Currency, DatabaseExtra

live_client = TestClient(app)

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
    server_id: str | None = None,
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
        "server_id": server_id,
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


def _make_server(
    server_id: str,
    *,
    architecture: CpuArchitecture = CpuArchitecture.X86_64,
    allocation: CpuAllocation = CpuAllocation.DEDICATED,
    network_speed_baseline: float = 1.0,
    network_speed_max: float = 5.0,
    network_storage_speed_baseline: float = 1.0,
    network_storage_speed_max: float = 5.0,
):
    return {
        "vendor_id": "test",
        "server_id": server_id,
        "name": server_id,
        "api_reference": server_id,
        "display_name": server_id,
        "description": f"Test server {server_id}",
        "family": "general",
        "vcpus": 2,
        "memory_amount": 4096,
        "cpu_allocation": allocation,
        "cpu_architecture": architecture,
        "network_speed_baseline": network_speed_baseline,
        "network_speed_max": network_speed_max,
        "network_storage_speed_baseline": network_storage_speed_baseline,
        "network_storage_speed_max": network_storage_speed_max,
        "status": Status.ACTIVE,
        "observed_at": NOW,
    }


_SERVERS = [
    _make_server(
        "srv-x86",
        architecture=CpuArchitecture.X86_64,
        allocation=CpuAllocation.DEDICATED,
        network_speed_baseline=5.0,
        network_speed_max=10.0,
        network_storage_speed_baseline=4.0,
        network_storage_speed_max=8.0,
    ),
    _make_server(
        "srv-arm",
        architecture=CpuArchitecture.ARM64,
        allocation=CpuAllocation.SHARED,
        network_speed_baseline=1.0,
        network_speed_max=2.0,
        network_storage_speed_baseline=0.5,
        network_storage_speed_max=1.0,
    ),
]

_DATABASES = [
    _make_database(
        "db-small",
        vcpus=2,
        memory=4096,
        storage_size=100,
        server_id="srv-x86",
        storage_extra_min=5,
        storage_extra_max=50,
    ),
    _make_database(
        "db-large",
        vcpus=8,
        memory=32768,
        storage_size=None,
        server_id="srv-arm",
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

    for row in _SERVERS:
        session.add(Server(**row))
    session.flush()

    for row in _DATABASES:
        session.add(Database(**row))

    session.add(
        BenchmarkScore(
            vendor_id="test",
            resource_type=ResourceType.DATABASE,
            resource_id="db-large",
            benchmark_id="pgbench:heavy_read_only",
            config=HashableDict(),
            score=1000,
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.add(
        BenchmarkScore(
            vendor_id="test",
            resource_type=ResourceType.DATABASE,
            resource_id="db-small",
            benchmark_id="pgbench:heavy_read_only",
            config=HashableDict(),
            score=100,
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )

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


@pytest.fixture
def seeded_client(test_engine):
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


def get_databases(seeded_client, **params):
    resp = seeded_client.get("/databases", params=params)
    assert resp.status_code == 200
    return resp.json(), resp


class TestResponseStructure:
    def test_default_returns_list(self, seeded_client):
        data, _ = get_databases(seeded_client)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_required_fields(self, seeded_client):
        data, _ = get_databases(seeded_client, limit=1)
        row = data[0]
        for field in [
            "vendor_id",
            "database_id",
            "engine",
            "min_price",
            "vendor",
            "price_breakdown",
        ]:
            assert field in row, f"Missing field: {field}"
        assert "selected_benchmark_score" in row
        assert "selected_benchmark_score_per_price" in row
        assert row["selected_benchmark_score"] is not None
        assert row["selected_benchmark_score_per_price"] is not None

    def test_order_by_min_price_asc(self, seeded_client):
        data, _ = get_databases(seeded_client, order_by="min_price", order_dir="asc")
        prices = [r["min_price"] for r in data if r["min_price"] is not None]
        assert prices == sorted(prices)

    def test_order_by_unknown_field(self, seeded_client):
        resp = seeded_client.get("/databases", params={"order_by": "not_a_column"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unknown order_by field."

    def test_currency_eur_converts_prices(self, seeded_client):
        # db-small: 100 bundled + 50 max extra → request must fit within 150
        usd, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-small",
            extra_storage_size=150,
            currency="USD",
        )
        eur, _ = get_databases(
            seeded_client,
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
    def test_vcpus_min_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, vcpus_min=8)
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"

    def test_architecture_filter_via_identified_server(self, seeded_client):
        data, _ = get_databases(seeded_client, architecture=["arm64"])
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"
        assert data[0]["server_id"] == "srv-arm"
        # Response shape stays DatabasePKs; no extra server fields.
        assert "cpu_architecture" not in data[0]

    def test_cpu_allocation_filter_via_identified_server(self, seeded_client):
        data, _ = get_databases(seeded_client, cpu_allocation=["Dedicated"])
        assert len(data) == 1
        assert data[0]["database_id"] == "db-small"

    def test_network_filters_via_identified_server(self, seeded_client):
        data, _ = get_databases(
            seeded_client,
            network_speed_baseline_min=4,
            network_speed_max_min=8,
            network_storage_speed_baseline_min=3,
            network_storage_speed_max_min=7,
        )
        assert len(data) == 1
        assert data[0]["database_id"] == "db-small"

    def test_benchmark_score_min_filter(self, seeded_client):
        data, _ = get_databases(
            seeded_client,
            benchmark_id="pgbench:heavy_read_only",
            benchmark_score_min=500,
            order_by="selected_benchmark_score",
        )
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"
        assert data[0]["selected_benchmark_score"] == 1000

    def test_benchmark_score_per_price_min_filter(self, seeded_client):
        # db-large: 1000 / 0.50 = 2000; db-small: 100 / 0.10 = 1000
        data, _ = get_databases(
            seeded_client,
            benchmark_id="pgbench:heavy_read_only",
            benchmark_score_per_price_min=1500,
            order_by="selected_benchmark_score_per_price",
        )
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"
        assert data[0]["selected_benchmark_score_per_price"] == 2000

    def test_storage_size_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, storage_size=50)
        assert len(data) == 1
        assert data[0]["database_id"] == "db-small"

    def test_engine_version_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, engine_version="15")
        assert len(data) == 2
        assert all("15" in r["engine_versions"] for r in data)

    def test_ha_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, ha=["multi-region"])
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"
        assert "multi-region" in data[0]["ha"]

    def test_ha_strategy_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, ha_strategy=["multi-master"])
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"
        assert "multi-master" in data[0]["ha_strategy"]

    def test_min_price_across_ha_price_rows(self, seeded_client):
        data, _ = get_databases(seeded_client, partial_name_or_id="db-small")
        assert len(data) == 1
        assert data[0]["min_price"] == 0.1
        assert data[0]["min_price_ondemand"] == 0.1
        assert data[0]["min_price_ondemand_monthly"] == 73.0

    def test_wire_protocol_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, wire_protocol=["postgresql"])
        assert len(data) == 2

    def test_max_read_replicas_min_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, max_read_replicas_min=10)
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"

    def test_storage_extra_autosize_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, storage_extra_autosize=True)
        assert len(data) == 2

    def test_security_features_filter(self, seeded_client):
        data, _ = get_databases(
            seeded_client, security_features=["ip-filtering", "network-peering"]
        )
        assert len(data) == 1
        assert data[0]["database_id"] == "db-large"

    def test_autotuning_apply_filter(self, seeded_client):
        data, _ = get_databases(seeded_client, autotuning_apply=False)
        assert len(data) == 2
        none, _ = get_databases(seeded_client, autotuning_apply=True)
        assert len(none) == 0

    def test_extra_storage_increases_min_price(self, seeded_client):
        base, _ = get_databases(seeded_client, partial_name_or_id="db-large")
        with_extra, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-large",
            extra_storage_size=200,
        )
        assert with_extra[0]["min_price"] > base[0]["min_price"]
        pb = with_extra[0]["price_breakdown"]
        assert pb["extra_storage_monthly"] > 0
        assert pb["extra_storage_hourly"] > 0

    def test_storage_extra_min_floor(self, seeded_client):
        # db-small: 100 GB bundled, storage_extra_min=5 → need 101 bills 5 GB extra
        data, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-small",
            extra_storage_size=101,
        )
        assert len(data) == 1
        assert data[0]["price_breakdown"]["extra_storage_monthly"] == 0.5  # 5 * 0.10

    def test_storage_extra_max_filters_out(self, seeded_client):
        # db-small: 100 + 50 max = 150 → 151 excluded, 150 kept
        excluded, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-small",
            extra_storage_size=151,
        )
        assert excluded == []

        included, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-small",
            extra_storage_size=150,
        )
        assert len(included) == 1
        assert (
            included[0]["price_breakdown"]["extra_storage_monthly"] == 5.0
        )  # 50 * 0.10

    def test_bundled_storage_reduces_extra_storage_cost(self, seeded_client):
        bundled, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-small",
            extra_storage_size=150,
        )
        unbundled, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-large",
            extra_storage_size=150,
        )
        assert (
            bundled[0]["price_breakdown"]["extra_storage_monthly"]
            < (unbundled[0]["price_breakdown"]["extra_storage_monthly"])
        )

    def test_total_count_header(self, seeded_client):
        _, resp = get_databases(seeded_client, add_total_count_header=True, limit=1)
        assert resp.headers.get("X-Total-Count") == "2"

    def test_best_price_allocation_spot_only_rejected(self, seeded_client):
        resp = seeded_client.get(
            "/databases", params={"best_price_allocation": "SPOT_ONLY"}
        )
        assert resp.status_code == 422

    def test_best_price_allocation_ondemand_only(self, seeded_client):
        data, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-small",
            best_price_allocation="ONDEMAND_ONLY",
        )
        row = data[0]
        assert row["min_price"] == row["min_price_ondemand"]

    def test_best_price_allocation_monthly(self, seeded_client):
        data, _ = get_databases(
            seeded_client,
            partial_name_or_id="db-small",
            best_price_allocation="MONTHLY",
        )
        row = data[0]
        assert row["min_price"] == row["min_price_ondemand_monthly"]
        assert row["min_price_ondemand_monthly"] > row["min_price_ondemand"]

    def test_database_storage_prices(self, seeded_client):
        resp = seeded_client.get(
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

    def test_database_storage_prices_storage_min(self, seeded_client):
        resp = seeded_client.get(
            "/database_storage_prices",
            params={"storage_min": 50},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = seeded_client.get(
            "/database_storage_prices",
            params={"storage_min": 200000},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 0


def get_live_databases(**params):
    resp = live_client.get("/databases", params=params)
    assert resp.status_code == 200
    return resp.json(), resp


class TestLiveResponseStructure:
    def test_default_returns_list(self):
        data, _ = get_live_databases()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_default_limit(self):
        data, _ = get_live_databases()
        assert len(data) <= 25

    def test_custom_limit(self):
        data, _ = get_live_databases(limit=5)
        assert len(data) <= 5

    def test_required_fields(self):
        data, _ = get_live_databases(limit=1)
        row = data[0]
        for field in [
            "vendor_id",
            "database_id",
            "engine",
            "ha",
            "ha_strategy",
            "min_price",
            "vendor",
            "price_breakdown",
        ]:
            assert field in row, f"Missing field: {field}"
        assert isinstance(row["ha"], list)
        assert isinstance(row["ha_strategy"], list)

    def test_vendor_nested(self):
        data, _ = get_live_databases(limit=1)
        vendor = data[0]["vendor"]
        assert "vendor_id" in vendor
        assert "name" in vendor


class TestLiveOrdering:
    def test_order_by_min_price_asc(self):
        data, _ = get_live_databases(limit=10, order_by="min_price", order_dir="asc")
        prices = [r["min_price"] for r in data if r["min_price"] is not None]
        assert prices == sorted(prices)

    def test_order_by_min_price_desc(self):
        data, _ = get_live_databases(limit=10, order_by="min_price", order_dir="desc")
        prices = [r["min_price"] for r in data if r["min_price"] is not None]
        assert prices == sorted(prices, reverse=True)

    def test_order_by_vcpus(self):
        data, _ = get_live_databases(limit=10, order_by="vcpus", order_dir="asc")
        assert [r["vcpus"] for r in data] == sorted(r["vcpus"] for r in data)


class TestLiveFiltering:
    def test_vendor_filter(self):
        data, _ = get_live_databases(vendor=["aws"], limit=10)
        assert data
        assert all(r["vendor_id"] == "aws" for r in data)

    def test_multi_vendor_filter(self):
        data, _ = get_live_databases(vendor=["aws", "azure"], limit=50)
        assert data
        assert {r["vendor_id"] for r in data} <= {"aws", "azure"}

    def test_vcpus_min(self):
        data, _ = get_live_databases(vcpus_min=8, limit=10)
        assert data
        assert all(r["vcpus"] >= 8 for r in data)

    def test_vcpus_max(self):
        data, _ = get_live_databases(vcpus_max=4, limit=10)
        assert data
        assert all(r["vcpus"] <= 4 for r in data)

    def test_memory_min(self):
        data, _ = get_live_databases(memory_min=16, limit=10)
        assert data
        assert all(r["memory_amount"] >= 16 * 1024 for r in data)

    def test_engine_filter(self):
        data, _ = get_live_databases(engine="postgresql", limit=10)
        assert data
        assert all(r["engine"] == "postgresql" for r in data)

    def test_ha_filter(self):
        data, _ = get_live_databases(ha=["multi-zone"], limit=10)
        assert data
        assert all("multi-zone" in r["ha"] for r in data)

    def test_partial_name_or_id(self):
        data, _ = get_live_databases(
            partial_name_or_id="db.t3", vendor=["aws"], limit=10
        )
        assert data
        assert all(
            any(
                "db.t3" in (r.get(f) or "").lower()
                for f in ("database_id", "name", "api_reference", "display_name")
            )
            for r in data
        )

    def test_countries_filter(self):
        _, baseline = get_live_databases(
            vendor=["aws"], limit=1, add_total_count_header=True
        )
        _, filtered = get_live_databases(
            vendor=["aws"],
            countries=["DE"],
            limit=1,
            add_total_count_header=True,
        )
        assert int(filtered.headers["x-total-count"]) < int(
            baseline.headers["x-total-count"]
        )


class TestLiveCurrency:
    def test_eur_currency(self):
        data, _ = get_live_databases(limit=1, currency="EUR")
        assert data[0].get("currency", "EUR") == "EUR"

    def test_different_prices_for_different_currencies(self):
        usd, _ = get_live_databases(limit=1, currency="USD")
        eur, _ = get_live_databases(limit=1, currency="EUR")
        if usd[0]["min_price"] is not None and eur[0]["min_price"] is not None:
            assert usd[0]["min_price"] != eur[0]["min_price"]


class TestLiveBestPriceAllocation:
    def test_spot_only_rejected(self):
        resp = live_client.get(
            "/databases", params={"best_price_allocation": "SPOT_ONLY"}
        )
        assert resp.status_code == 422

    def test_ondemand_only(self):
        data, _ = get_live_databases(best_price_allocation="ONDEMAND_ONLY", limit=10)
        for row in data:
            if row["min_price_ondemand"] is not None:
                assert row["min_price"] == row["min_price_ondemand"]

    def test_monthly(self):
        data, _ = get_live_databases(best_price_allocation="MONTHLY", limit=10)
        for row in data:
            if row["min_price_ondemand_monthly"] is not None:
                assert row["min_price"] == row["min_price_ondemand_monthly"]


class TestLiveExtraStorage:
    def test_extra_storage_adds_to_price(self):
        base, _ = get_live_databases(vendor=["aws"], limit=5, order_by="vcpus")
        with_extra, _ = get_live_databases(
            vendor=["aws"],
            extra_storage_size=200,
            limit=5,
            order_by="vcpus",
        )
        base_by_id = {r["database_id"]: r for r in base}
        extra_by_id = {r["database_id"]: r for r in with_extra}
        common = base_by_id.keys() & extra_by_id.keys()
        assert common
        for database_id in common:
            b = base_by_id[database_id]
            e = extra_by_id[database_id]
            if b["min_price"] is not None and e["min_price"] is not None:
                assert e["min_price"] >= b["min_price"]

    def test_breakdown_components_sum_to_min_price(self):
        data, _ = get_live_databases(vendor=["aws"], extra_storage_size=100, limit=10)
        for row in data:
            pb = row["price_breakdown"]
            if row["min_price"] is not None and pb["compute_min_price"] is not None:
                expected = (pb["compute_min_price"] or 0) + (
                    pb["extra_storage_hourly"] or 0
                )
                assert abs(row["min_price"] - expected) < 0.001


class TestLiveDetailAndPrices:
    def test_database_detail(self):
        listing, _ = get_live_databases(vendor=["aws"], limit=1)
        assert listing
        vendor_id = listing[0]["vendor_id"]
        database_id = listing[0]["database_id"]
        resp = live_client.get(f"/database/{vendor_id}/{database_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vendor_id"] == vendor_id
        assert data["database_id"] == database_id
        assert "vendor" not in data

    def test_database_prices(self):
        listing, _ = get_live_databases(vendor=["aws"], limit=1)
        vendor_id = listing[0]["vendor_id"]
        database_id = listing[0]["database_id"]
        resp = live_client.get(
            f"/database/{vendor_id}/{database_id}/prices",
            params={"currency": "EUR"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        assert data[0]["database_id"] == database_id
        assert data[0]["currency"] == "EUR"
        assert "ha" in data[0]
        assert "ha_strategy" in data[0]


class TestLivePaging:
    def test_page_1_and_2_differ(self):
        page1, _ = get_live_databases(limit=5, page=1)
        page2, _ = get_live_databases(limit=5, page=2)
        assert page1
        assert page2
        assert [r["database_id"] for r in page1] != [r["database_id"] for r in page2]

    def test_total_count_header(self):
        _, resp = get_live_databases(limit=1, add_total_count_header=True)
        assert int(resp.headers["x-total-count"]) > 1

    def test_no_total_count_by_default(self):
        _, resp = get_live_databases(limit=1)
        assert "x-total-count" not in resp.headers
