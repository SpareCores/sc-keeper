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
from sqlmodel import Session, func, select, text

from .. import parameters as options
from ..auth import User, current_user
from ..database import get_db, session
from ..references import HealthcheckResponse

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
