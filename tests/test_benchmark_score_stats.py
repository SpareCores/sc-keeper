"""Regression tests for GET /benchmark_score_stats."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sc_crawler.table_fields import HashableDict, ResourceType, Status
from sc_crawler.tables import Benchmark, BenchmarkScore, Country, Vendor
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from sc_keeper.api import app

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)
BENCH_ID = "pgbench:heavy_read_only"


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
    session.add(
        Vendor(
            vendor_id="test",
            name="TestCloud",
            homepage="https://test.example.com",
            founding_year=2020,
            status=Status.ACTIVE,
            observed_at=NOW,
            country=session.get(Country, "US"),
        )
    )
    session.add(
        Benchmark(
            benchmark_id=BENCH_ID,
            category="Database",
            name="pgbench heavy read-only",
            description="pgbench",
            framework="pgbench",
            source={"kind": "measured"},
            config_fields={"concurrency": "Workload concurrency"},
            higher_is_better=True,
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.add(
        BenchmarkScore(
            vendor_id="test",
            resource_type=ResourceType.SERVER,
            resource_id="s1",
            benchmark_id=BENCH_ID,
            config=HashableDict({"concurrency": "single"}),
            score=10.0,
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.add(
        BenchmarkScore(
            vendor_id="test",
            resource_type=ResourceType.SERVER,
            resource_id="s2",
            benchmark_id=BENCH_ID,
            config=HashableDict({"concurrency": "single"}),
            score=20.0,
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.add(
        BenchmarkScore(
            vendor_id="test",
            resource_type=ResourceType.DATABASE,
            resource_id="db-1",
            benchmark_id=BENCH_ID,
            config=HashableDict({"concurrency": "peak"}),
            score=1000.0,
            status=Status.ACTIVE,
            observed_at=NOW,
        )
    )
    session.commit()


@pytest.fixture
def seeded_client():
    from sc_keeper.database import get_db

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_db(session)

    def _override():
        db = Session(autocommit=False, autoflush=False, bind=engine)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_mixed_resource_scores_use_servers_only(seeded_client):
    resp = seeded_client.get("/benchmark_score_stats")
    assert resp.status_code == 200
    row = next(item for item in resp.json() if item["benchmark_id"] == BENCH_ID)

    assert row["count"] == 2
    assert row["count_servers"] == 2
    assert row["configs"]["concurrency"]["examples"] == ["single"]
    assert row["histogram"] is not None
    assert row["histogram"]["breakpoints"][-1] < 1000
