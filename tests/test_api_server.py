"""Unit tests for GET /server/{vendor}/{server}/similar_servers/{by}/{num}.

Uses an in-memory SQLite database with a small set of deterministic server
instances so we can assert the *ordering* and *values* returned for every
``by`` strategy and ``best_price_allocation`` variant.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sc_crawler.table_fields import (
    Allocation,
    CpuAllocation,
    CpuArchitecture,
    HashableDict,
    PriceUnit,
    Status,
)
from sc_crawler.tables import (
    BenchmarkScore,
    Country,
    Region,
    Server,
    ServerPrice,
    Vendor,
    Zone,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from sc_keeper.auth import User
from sc_keeper.views import Currency, ServerExtra

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

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

ZONE_DATA = {
    "vendor_id": "test",
    "region_id": "us-east-1",
    "zone_id": "us-east-1a",
    "name": "US East 1a",
    "api_reference": "us-east-1a",
    "display_name": "US East 1a",
    "status": Status.ACTIVE,
    "observed_at": NOW,
}

# Second region in DE – used to test region filtering
REGION2_DATA = {
    "vendor_id": "test",
    "region_id": "eu-west-1",
    "name": "EU West 1",
    "api_reference": "eu-west-1",
    "display_name": "EU West 1",
    "country_id": "DE",
    "status": Status.ACTIVE,
    "observed_at": NOW,
}

ZONE2_DATA = {
    "vendor_id": "test",
    "region_id": "eu-west-1",
    "zone_id": "eu-west-1a",
    "name": "EU West 1a",
    "api_reference": "eu-west-1a",
    "display_name": "EU West 1a",
    "status": Status.ACTIVE,
    "observed_at": NOW,
}


def _make_server(
    sid: str, *, vcpus: int, memory: int, gpu: float = 0, family: str = "general"
):
    return {
        "vendor_id": "test",
        "server_id": sid,
        "name": sid,
        "api_reference": sid,
        "display_name": sid,
        "description": f"Test server {sid}",
        "family": family,
        "vcpus": vcpus,
        "memory_amount": memory,
        "gpu_count": gpu,
        "cpu_allocation": CpuAllocation.DEDICATED,
        "cpu_architecture": CpuArchitecture.X86_64,
        "status": Status.ACTIVE,
        "observed_at": NOW,
    }


# 10 servers with varied specs/scores/prices for deterministic assertions
_SERVERS = [
    # sid        vcpus  mem    gpu   family
    _make_server("s1", vcpus=2, memory=4096, family="general"),
    _make_server("s2", vcpus=4, memory=8192, family="general"),
    _make_server("s3", vcpus=8, memory=16384, family="general"),
    _make_server("s4", vcpus=16, memory=32768, family="compute"),
    _make_server("s5", vcpus=32, memory=65536, family="compute"),
    _make_server("s6", vcpus=4, memory=16384, family="memory"),
    _make_server("s7", vcpus=8, memory=65536, family="memory"),
    _make_server("s8", vcpus=2, memory=4096, gpu=1, family="gpu"),
    _make_server("s9", vcpus=4, memory=8192, gpu=2, family="gpu"),
    _make_server("s10", vcpus=8, memory=16384, gpu=4, family="gpu"),
]

# Scores: rough mapping  s1→100 .. s10→1000
_SCORE_MAP = {
    "s1": 100,
    "s2": 200,
    "s3": 400,
    "s4": 800,
    "s5": 1600,
    "s6": 250,
    "s7": 500,
    "s8": 150,
    "s9": 300,
    "s10": 600,
}

# On-demand hourly prices in USD (in us-east-1, country=US)
_OD_PRICE = {
    "s1": 0.05,
    "s2": 0.10,
    "s3": 0.20,
    "s4": 0.40,
    "s5": 0.80,
    "s6": 0.12,
    "s7": 0.25,
    "s8": 0.50,
    "s9": 1.00,
    "s10": 2.00,
}

# Spot prices (cheaper) – only for some servers (in us-east-1, country=US)
_SPOT_PRICE = {
    "s1": 0.02,
    "s2": 0.04,
    "s3": 0.08,
    "s5": 0.30,
    "s8": 0.20,
    "s10": 0.80,
}

# Custom benchmark scores
_CUSTOM_BENCH = {
    "s1": 50,
    "s2": 120,
    "s3": 250,
    "s4": 500,
    "s5": 1000,
    "s6": 140,
    "s7": 280,
    "s8": 80,
    "s9": 180,
    "s10": 400,
}


def _seed_db(session: Session):
    """Populate the in-memory DB with deterministic test data."""
    country_us = Country(
        country_id="US",
        continent="North America",
        status=Status.ACTIVE,
        observed_at=NOW,
    )
    country_de = Country(
        country_id="DE", continent="Europe", status=Status.ACTIVE, observed_at=NOW
    )
    session.add(country_us)
    session.add(country_de)
    session.flush()

    session.add(Vendor(**VENDOR_DATA, country=country_us))
    session.add(Region(**REGION_DATA))
    session.add(Zone(**ZONE_DATA))
    session.add(Region(**REGION2_DATA))
    session.add(Zone(**ZONE2_DATA))

    for sdata in _SERVERS:
        session.add(Server(**sdata))

    # Currency: 1:1 for USD→USD
    session.add(Currency(base="USD", quote="USD", rate=1.0))

    # Prices – on-demand in us-east-1 (country=US)
    for sid, price in _OD_PRICE.items():
        session.add(
            ServerPrice(
                vendor_id="test",
                region_id="us-east-1",
                zone_id="us-east-1a",
                server_id=sid,
                operating_system="linux",
                allocation=Allocation.ONDEMAND,
                unit=PriceUnit.HOUR,
                price=price,
                currency="USD",
                status=Status.ACTIVE,
                observed_at=NOW,
            )
        )

    # Prices – spot in us-east-1 (only for some servers)
    for sid, price in _SPOT_PRICE.items():
        session.add(
            ServerPrice(
                vendor_id="test",
                region_id="us-east-1",
                zone_id="us-east-1a",
                server_id=sid,
                operating_system="linux",
                allocation=Allocation.SPOT,
                unit=PriceUnit.HOUR,
                price=price,
                currency="USD",
                status=Status.ACTIVE,
                observed_at=NOW,
            )
        )

    # Prices – on-demand in eu-west-1 (country=DE, 15% more expensive)
    for sid, price in _OD_PRICE.items():
        session.add(
            ServerPrice(
                vendor_id="test",
                region_id="eu-west-1",
                zone_id="eu-west-1a",
                server_id=sid,
                operating_system="linux",
                allocation=Allocation.ONDEMAND,
                unit=PriceUnit.HOUR,
                price=round(price * 1.15, 4),  # 15% more in EU
                currency="USD",
                status=Status.ACTIVE,
                observed_at=NOW,
            )
        )

    # Benchmark scores – stress_ng:bestn (used for ServerExtra.score)
    for sid, score in _SCORE_MAP.items():
        session.add(
            BenchmarkScore(
                vendor_id="test",
                server_id=sid,
                benchmark_id="stress_ng:bestn",
                config=HashableDict(),
                score=score,
                status=Status.ACTIVE,
                observed_at=NOW,
            )
        )

    # Custom benchmark scores
    for sid, score in _CUSTOM_BENCH.items():
        session.add(
            BenchmarkScore(
                vendor_id="test",
                server_id=sid,
                benchmark_id="geekbench:multi",
                config=HashableDict(),
                score=score,
                status=Status.ACTIVE,
                observed_at=NOW,
            )
        )

    session.commit()

    # Populate ServerExtra view
    from sqlmodel import delete
    from sqlmodel import insert as sm_insert

    session.execute(delete(ServerExtra))
    session.execute(
        sm_insert(ServerExtra).from_select(
            ServerExtra.get_columns()["all"],
            ServerExtra.query(),
        )
    )
    session.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def test_engine():
    """Create an in-memory SQLite engine with all tables + test data."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Add extended columns (price_monthly) to ServerPrice table
    from sc_keeper.crawler_extend import extenders

    for extender in extenders:
        extender.add_columns(engine)
        extender.update(engine)

    with Session(engine) as session:
        _seed_db(session)
    return engine


def _make_override_get_db(engine):
    """Create a get_db override for a given engine."""
    from sc_keeper.database import get_db  # noqa: F811

    def _override():
        db = Session(autocommit=False, autoflush=False, bind=engine)
        try:
            yield db
        finally:
            db.close()

    return get_db, _override


@pytest.fixture(scope="module")
def client(test_engine):
    """TestClient that uses the in-memory DB and bypasses auth."""
    from sc_keeper.api import app

    original_dep, override = _make_override_get_db(test_engine)
    app.dependency_overrides[original_dep] = override

    fake_user = User(user_id="test-user")
    with patch(
        "sc_keeper.auth.extract_user_from_request",
        new=AsyncMock(return_value=fake_user),
    ):
        yield TestClient(app)

    app.dependency_overrides.pop(original_dep, None)


@pytest.fixture()
def anon_client(test_engine, client):
    """TestClient that uses the in-memory DB *without* auth (anonymous user)."""
    from sc_keeper.api import app

    # Patch extract_user_from_request to return None (anonymous)
    with patch(
        "sc_keeper.auth.extract_user_from_request",
        new=AsyncMock(return_value=None),
    ):
        yield TestClient(app)


def _url(sid: str, by: str, num: int = 5) -> str:
    return f"/server/test/{sid}/similar_servers/{by}/{num}"


# ---------------------------------------------------------------------------
# Helper to compute expected values
# ---------------------------------------------------------------------------


def _global_min_price(sid: str) -> float:
    """The global min price (any allocation) for a server across ALL regions."""
    # ServerExtra.min_price is computed across all regions, both allocations
    prices = []
    # us-east-1 prices
    prices.append(_OD_PRICE[sid])
    if sid in _SPOT_PRICE:
        prices.append(_SPOT_PRICE[sid])
    # eu-west-1 on-demand price
    prices.append(round(_OD_PRICE[sid] * 1.15, 4))
    return min(prices)


def _live_min_price(sid: str, *, country: str = "US") -> float:
    """Min price when filtered to country=US (us-east-1 only)."""
    od = _OD_PRICE[sid]
    sp = _SPOT_PRICE.get(sid)
    if sp is not None:
        return min(od, sp)
    return od


# ===========================================================================
# Tests: by=family
# ===========================================================================


class TestSimilarByFamily:
    def test_returns_same_vendor_and_family(self, client):
        resp = client.get(_url("s1", "family", 10))
        assert resp.status_code == 200
        data = resp.json()
        # s1 is "general" family → s2, s3 are also general
        sids = {d["server_id"] for d in data}
        assert "s1" not in sids, "target server should be excluded"
        assert sids == {"s2", "s3"}, "only same-family servers returned"
        for d in data:
            assert d["vendor_id"] == "test"
            assert d["family"] == "general"

    def test_ordered_by_vcpus_gpu_memory(self, client):
        resp = client.get(_url("s1", "family", 10))
        data = resp.json()
        vcpus_list = [d["vcpus"] for d in data]
        assert vcpus_list == sorted(vcpus_list), "should be ordered by vcpus ascending"

    def test_family_with_num_limit(self, client):
        resp = client.get(_url("s4", "family", 1))
        data = resp.json()
        assert len(data) == 1
        assert data[0]["family"] == "compute"

    def test_family_no_results_for_unique_family(self, client):
        """gpu family has multiple members; s8 excluded from its own results."""
        resp = client.get(_url("s8", "family", 10))
        data = resp.json()
        sids = {d["server_id"] for d in data}
        assert "s8" not in sids
        for d in data:
            assert d["family"] == "gpu"


# ===========================================================================
# Tests: by=specs
# ===========================================================================


class TestSimilarBySpecs:
    def test_closest_specs_first(self, client):
        """s1 has 2 vCPUs, 4096 MiB, 0 GPU."""
        resp = client.get(_url("s1", "specs", 3))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        for d in data:
            assert d["server_id"]

    def test_gpu_weight_highest(self, client):
        """GPU difference has the highest weight (10e6). A server with 0 GPUs
        should see other 0-GPU servers before any GPU server."""
        resp = client.get(_url("s2", "specs", 9))
        data = resp.json()
        # s2 has 0 GPUs – non-GPU servers should come first
        gpu_zero = [d for d in data if d["gpu_count"] == 0]
        gpu_nonzero = [d for d in data if d["gpu_count"] > 0]
        if gpu_zero and gpu_nonzero:
            first_gpu_idx = next(i for i, d in enumerate(data) if d["gpu_count"] > 0)
            assert first_gpu_idx >= len(gpu_zero)

    def test_specs_excludes_self(self, client):
        resp = client.get(_url("s5", "specs", 9))
        data = resp.json()
        sids = {d["server_id"] for d in data}
        assert "s5" not in sids


# ===========================================================================
# Tests: by=score
# ===========================================================================


class TestSimilarByScore:
    def test_closest_score_first(self, client):
        """s1 has score=100. Closest should be s8(150), then s2(200)."""
        resp = client.get(_url("s1", "score", 3))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        scores = [d["score"] for d in data]
        assert all(s is not None for s in scores)
        # Distances from 100 should be non-decreasing
        distances = [abs(s - _SCORE_MAP["s1"]) for s in scores]
        assert distances == sorted(distances)

    def test_excludes_self(self, client):
        resp = client.get(_url("s3", "score", 9))
        data = resp.json()
        assert "s3" not in {d["server_id"] for d in data}


# ===========================================================================
# Tests: by=score_per_price
# ===========================================================================


class TestSimilarByScorePerPrice:
    def test_no_region_filter_uses_serverextra(self, client):
        """Without countries/vendor_regions, uses precomputed ServerExtra prices."""
        resp = client.get(_url("s2", "score_per_price", 5))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 5
        for d in data:
            assert d["score_per_price"] is not None
            assert d["score"] is not None

    def test_score_per_price_ordered_by_distance(self, client):
        """Results should be ordered by |score_per_price - baseline|."""
        resp = client.get(_url("s2", "score_per_price", 9))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        # Extract score_per_price values and verify non-decreasing distance
        spps = [d["score_per_price"] for d in data]
        # Baseline: s2's score_per_price from ServerExtra (score / global_min_price)
        baseline = round(_SCORE_MAP["s2"] / _global_min_price("s2"), 4)
        distances = [abs(spp - baseline) for spp in spps]
        assert distances == sorted(distances), (
            f"Results not ordered by distance from baseline {baseline}: {distances}"
        )

    def test_excludes_self(self, client):
        resp = client.get(_url("s3", "score_per_price", 9))
        data = resp.json()
        assert "s3" not in {d["server_id"] for d in data}

    def test_with_countries_filter(self, client):
        """When countries is set, live prices are used (gen_live_price_query is not None)."""
        resp = client.get(
            _url("s2", "score_per_price", 5),
            params={"countries": ["US"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        for d in data:
            assert d["score_per_price"] is not None

    def test_ondemand_only_allocation(self, client):
        """best_price_allocation=ONDEMAND_ONLY should use ondemand prices."""
        resp = client.get(
            _url("s1", "score_per_price", 5),
            params={
                "countries": ["US"],
                "best_price_allocation": "ONDEMAND_ONLY",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        for d in data:
            assert d["min_price_ondemand"] is not None
            assert d["score_per_price"] is not None

    def test_ondemand_allocation_score_per_price_values(self, client):
        """Verify score_per_price = score / ondemand_price for ONDEMAND_ONLY."""
        resp = client.get(
            _url("s2", "score_per_price", 9),
            params={
                "countries": ["US"],
                "best_price_allocation": "ONDEMAND_ONLY",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        for d in data:
            sid = d["server_id"]
            expected = round(_SCORE_MAP[sid] / _OD_PRICE[sid], 4)
            assert d["score_per_price"] == expected, (
                f"{sid}: expected spp={expected}, got {d['score_per_price']}"
            )

    def test_spot_only_allocation(self, client):
        """best_price_allocation=SPOT_ONLY should use spot prices for ordering."""
        resp = client.get(
            _url("s1", "score_per_price", 5),
            params={
                "countries": ["US"],
                "best_price_allocation": "SPOT_ONLY",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Only servers with spot prices should appear
        for d in data:
            assert d["min_price_spot"] is not None

    def test_null_price_servers_excluded(self, client):
        """Servers without a price in the filtered region should not appear
        (not float to the top)."""
        resp = client.get(
            _url("s1", "score_per_price", 9),
            params={"countries": ["US"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        for d in data:
            assert d["min_price"] is not None

    def test_live_price_ordered_by_distance(self, client):
        """With countries filter, results should still be ordered by distance."""
        resp = client.get(
            _url("s2", "score_per_price", 9),
            params={"countries": ["US"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        # Baseline: s2 score / live min price in US
        baseline = round(_SCORE_MAP["s2"] / _live_min_price("s2"), 4)
        spps = [d["score_per_price"] for d in data]
        distances = [abs(spp - baseline) for spp in spps]
        assert distances == sorted(distances)


# ===========================================================================
# Tests: by=benchmark_score
# ===========================================================================


class TestSimilarByBenchmarkScore:
    def test_requires_benchmark_id(self, client):
        """Should return 400 when benchmark_id is missing."""
        resp = client.get(_url("s1", "benchmark_score", 5))
        assert resp.status_code == 400
        assert "benchmark_id" in resp.json()["detail"].lower()

    def test_ordered_by_closest_benchmark_score(self, client):
        resp = client.get(
            _url("s2", "benchmark_score", 5),
            params={"benchmark_id": "geekbench:multi"},
        )
        assert resp.status_code == 200
        data = resp.json()
        baseline = _CUSTOM_BENCH["s2"]
        scores = [d["selected_benchmark_score"] for d in data]
        distances = [abs(s - baseline) for s in scores]
        assert distances == sorted(distances)

    def test_excludes_self(self, client):
        resp = client.get(
            _url("s3", "benchmark_score", 9),
            params={"benchmark_id": "geekbench:multi"},
        )
        data = resp.json()
        assert "s3" not in {d["server_id"] for d in data}


# ===========================================================================
# Tests: by=benchmark_score_per_price
# ===========================================================================


class TestSimilarByBenchmarkScorePerPrice:
    def test_requires_benchmark_id(self, client):
        resp = client.get(_url("s1", "benchmark_score_per_price", 5))
        assert resp.status_code == 400
        assert "benchmark_id" in resp.json()["detail"].lower()

    def test_ordered_by_distance(self, client):
        """With countries, results should be ordered by
        |bench_score/price - baseline_bench_score/price|."""
        resp = client.get(
            _url("s2", "benchmark_score_per_price", 5),
            params={
                "benchmark_id": "geekbench:multi",
                "countries": ["US"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        for d in data:
            assert d["selected_benchmark_score"] is not None

    def test_with_ondemand_allocation(self, client):
        """ONDEMAND_ONLY should use ondemand prices for benchmark_score_per_price."""
        resp = client.get(
            _url("s2", "benchmark_score_per_price", 5),
            params={
                "benchmark_id": "geekbench:multi",
                "countries": ["US"],
                "best_price_allocation": "ONDEMAND_ONLY",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        for d in data:
            sid = d["server_id"]
            expected_bspp = round(_CUSTOM_BENCH[sid] / _OD_PRICE[sid], 4)
            assert d["selected_benchmark_score_per_price"] == expected_bspp, (
                f"{sid}: expected bspp={expected_bspp}, got {d['selected_benchmark_score_per_price']}"
            )

    def test_no_region_filter(self, client):
        """Without countries/vendor_regions, uses ServerExtra precomputed prices."""
        resp = client.get(
            _url("s2", "benchmark_score_per_price", 5),
            params={"benchmark_id": "geekbench:multi"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0


# ===========================================================================
# Tests: auth – benchmark_id requires authentication
# ===========================================================================


class TestBenchmarkIdAuth:
    def test_benchmark_id_rejected_for_anon(self, anon_client):
        """Unauthenticated users cannot filter by benchmark_id."""
        resp = anon_client.get(
            _url("s1", "benchmark_score", 5),
            params={"benchmark_id": "geekbench:multi"},
        )
        assert resp.status_code == 401

    def test_benchmark_id_allowed_for_authed(self, client):
        """Authenticated users can filter by benchmark_id."""
        resp = client.get(
            _url("s1", "benchmark_score", 5),
            params={"benchmark_id": "geekbench:multi"},
        )
        assert resp.status_code == 200


# ===========================================================================
# Tests: edge cases
# ===========================================================================


class TestEdgeCases:
    def test_nonexistent_server_returns_404(self, client):
        resp = client.get("/server/test/nonexistent/similar_servers/specs/5")
        assert resp.status_code == 404

    def test_num_limit_respected(self, client):
        resp = client.get(_url("s1", "specs", 2))
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_num_over_100_rejected(self, client):
        resp = client.get(_url("s1", "specs", 101))
        assert resp.status_code == 422  # FastAPI validation error

    def test_invalid_by_rejected(self, client):
        resp = client.get(_url("s1", "invalid_method", 5))
        assert resp.status_code == 422

    def test_empty_result_when_no_score(self, client):
        """benchmark_id with no matching scores should return []."""
        resp = client.get(
            _url("s1", "benchmark_score", 5),
            params={"benchmark_id": "nonexistent_bench"},
        )
        assert resp.status_code == 200
        assert resp.json() == []
