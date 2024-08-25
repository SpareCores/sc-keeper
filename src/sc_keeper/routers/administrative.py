from importlib.metadata import version

from fastapi import (
    APIRouter,
    Depends,
)
from sqlmodel import Session

from ..database import get_db, session

router = APIRouter()


package_versions = {
    pkg: version(pkg)
    for pkg in ["sparecores-crawler", "sparecores-data", "sparecores-keeper"]
}


@router.get("/healthcheck", tags=["Administrative endpoints"])
def healthcheck(db: Session = Depends(get_db)) -> dict:
    """Return database hash and last udpated timestamp."""
    return {
        "packages": package_versions,
        "database_last_updated": session.last_updated,
        "database_hash": session.db_hash,
    }
