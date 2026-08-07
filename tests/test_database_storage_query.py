"""Unit tests for gen_database_storage_price_query (no live DB import)."""

from datetime import datetime, timezone

from sc_crawler.table_fields import (
    DatabaseEngine,
    DatabaseHaLevel,
    DatabaseStorageScope,
    DatabaseWireProtocol,
    PriceUnit,
    Status,
)
from sc_crawler.tables import (
    Country,
    Database,
    DatabaseStorage,
    DatabaseStoragePrice,
    Region,
    Vendor,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from sc_keeper.queries import gen_database_storage_price_query
from sc_keeper.views import Currency

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _seed(session: Session):
    session.add(
        Country(
            country_id="US",
            continent="North America",
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.flush()
    us = session.get(Country, "US")
    session.add(
        Vendor(
            vendor_id="test",
            name="Test",
            homepage="https://t",
            founding_year=2020,
            status=Status.ACTIVE,
            observed_at=NOW,
            country=us,
        )
    )
    session.add(
        Region(
            vendor_id="test",
            region_id="us-east-1",
            name="US",
            api_reference="us-east-1",
            display_name="US",
            country_id="US",
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    # Mirrors db.r6gd.4xlarge: 1020 bundled, 5 min, 70369 max
    session.add(
        Database(
            vendor_id="test",
            database_id="db-nvme",
            name="db-nvme",
            api_reference="db-nvme",
            display_name="db-nvme",
            description="nvme",
            engine=DatabaseEngine.POSTGRESQL,
            wire_protocol=DatabaseWireProtocol.POSTGRESQL,
            engine_versions=["15"],
            vcpus=2,
            memory_amount=4096,
            storage_size=1020,
            storage_extra_min=5,
            storage_extra_max=70369,
            ha=DatabaseHaLevel.SINGLE_ZONE,
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.add(
        Database(
            vendor_id="test",
            database_id="db-plain",
            name="db-plain",
            api_reference="db-plain",
            display_name="db-plain",
            description="plain",
            engine=DatabaseEngine.POSTGRESQL,
            wire_protocol=DatabaseWireProtocol.POSTGRESQL,
            engine_versions=["15"],
            vcpus=2,
            memory_amount=4096,
            storage_size=None,
            storage_extra_min=10,
            storage_extra_max=10000,
            ha=DatabaseHaLevel.NONE,
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


def _prices(session: Session, size: int) -> dict[str, float]:
    q = gen_database_storage_price_query(size)
    return {
        database_id: total_storage_price
        for database_id, total_storage_price in session.exec(
            select(q.c.database_id, q.c.total_storage_price)
        ).all()
    }


def test_storage_extra_min_floor_and_max_filter():
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
        _seed(session)

        # 1021 → bill storage_extra_min (5 GB), not just 1 GB remainder
        rows = _prices(session, 1021)
        assert abs(rows["db-nvme"] - 0.5) < 1e-6
        assert abs(rows["db-plain"] - 102.1) < 1e-6

        # 71390 → filtered (1020 + 70369 = 71389)
        assert _prices(session, 71390) == {}

        # 71389 → nvme kept, bills 70369 GB extra
        rows = _prices(session, 71389)
        assert abs(rows["db-nvme"] - 7036.9) < 1e-6
        assert "db-plain" not in rows

        # Bundled covers request → free
        assert _prices(session, 1000)["db-nvme"] == 0.0
