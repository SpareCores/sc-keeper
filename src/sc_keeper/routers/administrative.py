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

# per-benchmark aggregates (count, count_servers, min, max) for ACTIVE non-null scores.
_AGGREGATE_SCORES_SQL = text("""
    SELECT
        benchmark_id,
        count(*) AS cnt,
        count(DISTINCT vendor_id || ':' || server_id) AS cnt_servers,
        min(score) AS min_s,
        max(score) AS max_s
    FROM benchmark_score
    WHERE status = :status AND score IS NOT NULL
    GROUP BY benchmark_id
""").bindparams(status=Status.ACTIVE.name)

# histogram bin counts per benchmark using a CTE for min/max, then bin index in SQL.
# note that bin min/max ranges are NOT returned, only the bin index and count
_HISTOGRAM_BINS_SQL = text("""
    WITH bounds AS (
        SELECT
            benchmark_id,
            min(score) AS lo,
            max(score) AS hi
        FROM benchmark_score
        WHERE status = :status AND score IS NOT NULL
        GROUP BY benchmark_id
    )
    SELECT
        s.benchmark_id,
        CASE
            WHEN b.hi = b.lo THEN 0
            WHEN (s.score - b.lo) * 1.0 / (b.hi - b.lo) * :num_bins >= :num_bins THEN :max_bin
            WHEN (s.score - b.lo) * 1.0 / (b.hi - b.lo) * :num_bins < 0 THEN 0
            ELSE CAST((s.score - b.lo) * 1.0 / (b.hi - b.lo) * :num_bins AS INTEGER)
        END AS bin,
        count(*) AS cnt
    FROM benchmark_score s
    JOIN bounds b ON s.benchmark_id = b.benchmark_id
    WHERE s.status = :status AND s.score IS NOT NULL
    GROUP BY s.benchmark_id, bin
    ORDER BY s.benchmark_id, bin
""").bindparams(
    status=Status.ACTIVE.name,
    num_bins=NUM_HISTOGRAM_BINS,
    max_bin=NUM_HISTOGRAM_BINS - 1,
)

_BENCHMARK_CONFIG_VALUES_SQL = text("""
    SELECT DISTINCT
        bs.benchmark_id AS benchmark_id,
        je.key AS config_key,
        je.value AS config_value
    FROM benchmark_score bs, json_each(bs.config) je
    WHERE bs.status = :status AND bs.score IS NOT NULL AND bs.config IS NOT NULL AND je.value IS NOT NULL
    ORDER BY bs.benchmark_id, je.key
""").bindparams(status=Status.ACTIVE.name)


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

    Aggregation and histogram binning are done in SQL to avoid transferring
    millions of BenchmarkScore rows.
    Due to the complexity of the query, not using sqlalchemy markup.
    """

    benchmarks = db.exec(select(Benchmark).order_by(Benchmark.benchmark_id)).all()

    agg_rows = db.exec(_AGGREGATE_SCORES_SQL).all()
    agg_by_benchmark = {row[0]: row for row in agg_rows}

    bin_rows = db.exec(_HISTOGRAM_BINS_SQL).all()
    bins_by_benchmark: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in bin_rows:
        bins_by_benchmark[row[0]].append((row[1], row[2]))

    config_rows = db.exec(_BENCHMARK_CONFIG_VALUES_SQL).all()
    config_values: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for benchmark_id, config_key, config_value in config_rows:
        config_values[benchmark_id][config_key].add(config_value)

    def _sorted_examples(values: set) -> list:
        def _key(v):
            if isinstance(v, (int, float)):
                return (0, float(v))
            # keep bool stable (SQLite may emit 0/1 as int; if bool, sort after numbers)
            if isinstance(v, bool):
                return (1, int(v))
            # JSON values can come as strings; try numeric sorting if possible
            if isinstance(v, str):
                try:
                    return (0, float(v))
                except ValueError:
                    return (2, v)
            return (3, str(v))

        return sorted(values, key=_key)

    result = []
    for benchmark in benchmarks:
        bid = benchmark.benchmark_id
        agg = agg_by_benchmark.get(bid)
        count = agg[1] if agg else 0
        count_servers = agg[2] if agg else 0

        # merge Benchmark.config_fields with observed example values from BenchmarkScore.config
        configs: dict = {}
        config_fields = (
            benchmark.config_fields if isinstance(benchmark.config_fields, dict) else {}
        )
        if isinstance(config_fields, dict):
            for config_key, description in config_fields.items():
                configs[config_key] = {
                    "description": description,
                    "examples": _sorted_examples(
                        config_values.get(bid, {}).get(config_key, set())
                    ),
                }

        histogram = None
        if agg and count > 0:
            min_val = agg[3]
            max_val = agg[4]
            # recompute bin min/max for histogram as not passed from SQL
            if min_val == max_val:
                half = abs(min_val) * 0.05 if min_val != 0 else 1.0
                min_val = min_val - half
                max_val = max_val + half
            step = (max_val - min_val) / NUM_HISTOGRAM_BINS
            breakpoints = [min_val + i * step for i in range(NUM_HISTOGRAM_BINS + 1)]
            counts = [0] * NUM_HISTOGRAM_BINS
            for bin_idx, cnt in bins_by_benchmark.get(bid, []):
                if 0 <= bin_idx < NUM_HISTOGRAM_BINS:
                    counts[bin_idx] = cnt
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
                status=(
                    benchmark.status.name
                    if hasattr(benchmark.status, "name")
                    else benchmark.status
                ),
                configs=configs,
                count=count,
                count_servers=count_servers,
                histogram=histogram,
            )
        )

    return result
