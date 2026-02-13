from importlib.metadata import version

from fastapi import APIRouter, Depends, Security
from sc_crawler.tables import (
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
from ..references import HealthcheckResponse
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
def get_debug_info(db: Session = Depends(get_db)) -> dict:
    """Return debug information about the availability of benchmark scores for servers."""

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

        if is_active:
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
