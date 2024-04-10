from contextlib import asynccontextmanager
from typing import List, Optional, Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from sc_crawler.tables import Server, ServerPrice
from sc_crawler.table_bases import (
    VendorBase,
    DatacenterBase,
    ZoneBase,
    ServerBase,
    ServerPriceBase,
)
from sqlmodel import Session, select
from .database import session


def get_db():
    db = session.sessionmaker
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # set one example for Swagger docs
    db = next(get_db())
    example_server = db.exec(select(Server).limit(1)).one()
    Server.model_config["json_schema_extra"] = {
        "examples": [example_server.model_dump()]
    }
    yield
    # shutdown
    pass


app = FastAPI(lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# aggressive compression
app.add_middleware(GZipMiddleware, minimum_size=100)


class ServerPKs(ServerBase):
    vendor: VendorBase


@app.get("/server/{vendor_id}/{server_id}")
def read_server(
    vendor_id: str, server_id: str, db: Session = Depends(get_db)
) -> ServerPKs:
    server = db.get(Server, (vendor_id, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


class ServerPriceWithPKs(ServerPriceBase):
    vendor: VendorBase
    datacenter: DatacenterBase
    zone: ZoneBase
    server: ServerBase


@app.get("/search")
def search_server(
    vcpus_min: Annotated[int, Query(description="Minimum number of virtual CPUs.")] = 1,
    memory_min: Annotated[Optional[int], Query(description="Minimum amount of memory in MBs.")] = None,
    price_max: Annotated[
        Optional[float], Query(description="Maximum price (USD/hr).")
    ] = None,
    limit: Annotated[int, Query(description="Maximum number of results. Set to -1 for unlimited")] = 50,
    page: Annotated[Optional[int], Query(description="Page number.")] = None,
    orderBy: Annotated[Optional[str], Query(description="Order by column.")] = 'price',
    orderDir: Annotated[Optional[str], Query(description="Order direction.")] = 'asc',
    db: Session = Depends(get_db),
) -> List[ServerPriceWithPKs]:
    query = (
        select(ServerPrice)
        .join(ServerPrice.vendor)
        .join(ServerPrice.datacenter)
        .join(ServerPrice.zone)
        .join(ServerPrice.server)
    )
    if vcpus_min:
        query = query.where(Server.vcpus >= vcpus_min)
    if memory_min:
        query = query.where(Server.memory >= memory_min)
    if price_max:
        query = query.where(ServerPrice.price <= price_max)

    #ordering
    if orderBy:
        if hasattr(ServerPrice, orderBy):
            order_by = getattr(ServerPrice, orderBy)
            if orderDir == 'asc':
                query = query.order_by(order_by)
            else:
                query = query.order_by(order_by.desc())
    
    #pagination
    if limit > 0:
        query = query.limit(limit)
    # only apply if limit is set
    if page and limit > 0:
        query = query.offset((page - 1) * limit)
    servers = db.exec(query).all()
    return servers


## https://fastapi-filter.netlify.app/#examples
