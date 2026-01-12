from importlib.metadata import version

from fastapi import APIRouter, Depends
from sqlmodel import Session, text

from ..auth import User, require_auth
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
def me(user: User = Depends(require_auth)) -> User:
    """Return the current user after authentication."""
    return user
