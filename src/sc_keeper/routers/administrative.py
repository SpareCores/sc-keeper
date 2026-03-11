from collections import defaultdict
from importlib.metadata import version
from typing import List

from fastapi import APIRouter, Depends, Security
from sc_crawler.tables import (
    Benchmark,
    BenchmarkScore,
    Region,
    Server,
    ServerPrice,
    Status,
    Vendor,
    Zone,
)
from sqlmodel import Session, case, func, select, text

from .. import parameters as options
from ..auth import User, current_user
from ..database import get_db, session
from ..references import (
    BenchmarkHistogram,
    BenchmarkScoreStatsItem,
    DebugInfoResponse,
    HealthcheckResponse,
)
from ..views import ServerExtra

router = APIRouter()


package_versions = {
    pkg: version(pkg)
    for pkg in ["sparecores-crawler", "sparecores-data", "sparecores-keeper"]
}


@router.get("/healthcheck")
def healthcheck(db: Session = Depends(get_db)) -> HealthcheckResponse:
    """Quickly return package and database version information."""
    return {
        "packages": package_versions,
        "database_last_updated": session.last_updated,
        "database_hash": session.db_hash,
        "database_alembic_version": db.exec(
            text("SELECT version_num FROM zzz_alembic_version")
        ).one()[0],
    }


@router.get("/me")
def me(user: User = Security(current_user)) -> User:
    """Return the current user after authentication."""
    return user


@router.get("/stats")
def get_stats(
    vendor: options.vendor = None,
    only_active: options.only_active = False,
    db: Session = Depends(get_db),
) -> dict:
    """Return counts of records in each table, optionally filtered by vendor and status."""

    def _count(table):
        """Execute count query with optional vendor and status filters."""
        query = select(func.count()).select_from(table)
        if vendor:
            query = query.where(table.vendor_id.in_(vendor))
        if only_active:
            query = query.where(table.status == Status.ACTIVE)
        return db.exec(query).one()

    return {
        "total_vendors": _count(Vendor),
        "total_regions": _count(Region),
        "total_zones": _count(Zone),
        "total_server_types": _count(Server),
        "total_server_prices": _count(ServerPrice),
        "total_benchmark_scores": _count(BenchmarkScore),
    }


@router.get("/debug")
def get_debug_info(db: Session = Depends(get_db)) -> DebugInfoResponse:
    """Return debug information about the availability of benchmark scores for servers.

    Returns vendor-level statistics, per-server details, and a list of all benchmark families.
    """

    servers_query = (
        select(Server, ServerExtra.min_price)
        .join(
            ServerExtra,
            (Server.vendor_id == ServerExtra.vendor_id)
            & (Server.server_id == ServerExtra.server_id),
            isouter=True,
        )
        .order_by(Server.vendor_id, Server.server_id)
    )
    servers_data = db.exec(servers_query).all()

    servers = []
    for server, min_price in servers_data:
        servers.append(
            {
                "vendor_id": server.vendor_id,
                "server_id": server.server_id,
                "api_reference": server.api_reference,
                "status": server.status if server.status else None,
                "has_hw_info": bool(server.cpu_flags),
                "has_price": bool(min_price),
                "has_benchmarks": False,
                "benchmarks": {},
            }
        )

    # group benchmark ids into families for higher level reporting
    benchmark_family = case(
        (
            func.instr(BenchmarkScore.benchmark_id, ":") > 0,
            func.substr(
                BenchmarkScore.benchmark_id,
                1,
                func.instr(BenchmarkScore.benchmark_id, ":") - 1,
            ),
        ),
        else_=BenchmarkScore.benchmark_id,
    ).label("benchmark_family")

    scores_query = (
        select(
            BenchmarkScore.vendor_id,
            BenchmarkScore.server_id,
            benchmark_family,
        )
        .where(BenchmarkScore.status == Status.ACTIVE)
        .where(BenchmarkScore.score.isnot(None))
        .group_by(
            BenchmarkScore.vendor_id,
            BenchmarkScore.server_id,
            benchmark_family,
        )
        .order_by(benchmark_family)
    )
    scores_data = db.exec(scores_query).all()

    benchmark_families = set()
    servers_with_scores = {}
    for vendor_id, server_id, family in scores_data:
        benchmark_families.add(family)
        key = f"{vendor_id}:{server_id}"
        if key not in servers_with_scores:
            servers_with_scores[key] = {}
        servers_with_scores[key][family] = True
    benchmark_families = sorted(benchmark_families)

    for server in servers:
        key = f"{server['vendor_id']}:{server['server_id']}"
        server["benchmarks"] = {
            family: servers_with_scores.get(key, {}).get(family, False)
            for family in benchmark_families
        }
        server["has_benchmarks"] = any(server["benchmarks"].values())

    vendor_stats = {}
    for server in servers:
        vendor_id = server["vendor_id"]
        if vendor_id not in vendor_stats:
            vendor_stats[vendor_id] = {
                "vendor_id": vendor_id,
                "all": 0,
                "inactive": 0,
                "active": 0,
                "evaluated": 0,
                "missing": 0,
            }
        vendor_stats[vendor_id]["all"] += 1

        has_price = server["has_price"]
        is_active = server["status"] == Status.ACTIVE
        has_benchmarks = server["has_benchmarks"]

        if is_active and has_price:
            vendor_stats[vendor_id]["active"] += 1
        if is_active and has_benchmarks:
            vendor_stats[vendor_id]["evaluated"] += 1
        elif is_active and has_price and not has_benchmarks:
            vendor_stats[vendor_id]["missing"] += 1
        elif not is_active or not has_price:
            vendor_stats[vendor_id]["inactive"] += 1
        else:
            raise ValueError(f"Unexpected server category: {server}")
    vendors = sorted(vendor_stats.values(), key=lambda x: x["vendor_id"])

    return {
        "vendors": vendors,
        "servers": servers,
        "benchmark_families": benchmark_families,
    }


NUM_HISTOGRAM_BINS = 20


@router.get("/benchmark_score_stats")
def get_benchmark_score_stats(
    db: Session = Depends(get_db),
) -> List[BenchmarkScoreStatsItem]:
    """Return aggregate stats and score distribution histograms for each benchmark.

    For every benchmark in the Benchmark table, returns:
    - Basic benchmark metadata (name, framework, unit, etc.)
    - Count of active, non-null score records
    - Count of distinct (vendor_id, server_id) pairs
    - A 20-bin histogram of the score distribution (breakpoints + per-bucket counts)
    """

    benchmarks = db.exec(select(Benchmark).order_by(Benchmark.benchmark_id)).all()

    scores_query = select(
        BenchmarkScore.benchmark_id,
        BenchmarkScore.vendor_id,
        BenchmarkScore.server_id,
        BenchmarkScore.score,
    ).where(
        BenchmarkScore.status == Status.ACTIVE,
        BenchmarkScore.score.isnot(None),
    )
    scores_data = db.exec(scores_query).all()

    scores_by_benchmark: dict[str, list[float]] = defaultdict(list)
    servers_by_benchmark: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for benchmark_id, vendor_id, server_id, score in scores_data:
        scores_by_benchmark[benchmark_id].append(score)
        servers_by_benchmark[benchmark_id].add((vendor_id, server_id))

    result = []
    for benchmark in benchmarks:
        bid = benchmark.benchmark_id
        scores = scores_by_benchmark.get(bid, [])
        count = len(scores)
        count_servers = len(servers_by_benchmark.get(bid, set()))

        histogram = None
        if count > 0:
            min_val = min(scores)
            max_val = max(scores)

            if min_val == max_val:
                # All scores identical – widen the range slightly so buckets have width
                half = abs(min_val) * 0.05 if min_val != 0 else 1.0
                min_val = min_val - half
                max_val = max_val + half

            step = (max_val - min_val) / NUM_HISTOGRAM_BINS
            breakpoints = [min_val + i * step for i in range(NUM_HISTOGRAM_BINS + 1)]

            counts = [0] * NUM_HISTOGRAM_BINS
            for score in scores:
                idx = int((score - breakpoints[0]) / step)
                # Clamp: the maximum value lands exactly on the last breakpoint
                idx = min(idx, NUM_HISTOGRAM_BINS - 1)
                counts[idx] += 1

            histogram = BenchmarkHistogram(breakpoints=breakpoints, counts=counts)

        result.append(
            BenchmarkScoreStatsItem(
                benchmark_id=bid,
                name=benchmark.name,
                description=benchmark.description,
                framework=benchmark.framework,
                measurement=benchmark.measurement,
                unit=benchmark.unit,
                higher_is_better=benchmark.higher_is_better,
                count=count,
                count_servers=count_servers,
                histogram=histogram,
            )
        )

    return result
