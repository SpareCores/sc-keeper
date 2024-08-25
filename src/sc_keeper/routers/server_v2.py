from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Path,
)
from sqlmodel import Session

from ..database import get_db
from ..helpers import get_server_base
from ..references import ServerPKs

router = APIRouter()


@router.get("/server/{vendor}/{server}", tags=["Server Details"])
def get_server(
    vendor: Annotated[str, Path(description="Vendor ID.")],
    server: Annotated[str, Path(description="Server ID or API reference.")],
    db: Session = Depends(get_db),
) -> ServerPKs:
    """Query a single server by its vendor id and either the server id or its API reference."""
    return get_server_base(vendor, server, db)
