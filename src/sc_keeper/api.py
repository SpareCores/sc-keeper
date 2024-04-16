from contextlib import asynccontextmanager
from enum import Enum
from typing import Annotated, List, Optional
from textwrap import dedent

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sc_crawler.table_bases import (
    CountryBase,
    DatacenterBase,
    ServerBase,
    ServerPriceBase,
    VendorBase,
    ZoneBase,
)
from sc_crawler.tables import Server, ServerPrice
from sqlmodel import Session, select

from .currency import CurrencyConverter
from .database import session


def get_db():
    db = session.sessionmaker
    try:
        yield db
    finally:
        db.close()


db = next(get_db())
example_server = db.exec(select(Server).limit(1)).one()


currency_converter = CurrencyConverter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # set one example for Swagger docs
    Server.model_config["json_schema_extra"] = {
        "examples": [example_server.model_dump()]
    }
    yield
    # shutdown
    pass


app = FastAPI(
    title="Spare Cores (SC) Keeper",
    description=dedent("""
    API to search and serve data collected on cloud compute resources.

    ## Licensing

    This is a free service provided by the Spare Cores team, without any warranty.
    The source code of the API is licensed under MPL-2.0, find more details at
    <https://github.com/SpareCores/sc-keeper>.

    ## References

    - Spare Cores: <https://sparecores.com>
    - SC Keeper: <https://github.com/SpareCores/sc-keeper>
    - SC Crawler: <https://github.com/SpareCores/sc-crawler>
    - SC Data: <https://github.com/SpareCores/sc-data>
    """),
    version="0.0.1",
    # terms_of_service="TODO",
    contact={
        "name": "Spare Cores Team",
        "email": "social@sparecores.com",
    },
    license_info={
        "name": "Mozilla Public License 2.0 (MPL 2.0)",
        "url": "http://mozilla.org/MPL/2.0/",
    },
    lifespan=lifespan,
)

# CORS: allows all origins, without spec headers and without auth
app.add_middleware(CORSMiddleware, allow_origins=["*"])

# aggressive compression
app.add_middleware(GZipMiddleware, minimum_size=100)


class ServerPKs(ServerBase):
    vendor: VendorBase


class OrderDir(Enum):
    ASC = "asc"
    DESC = "desc"


class FilterCategories(Enum):
    BASIC = "basic"
    PRICE = "price"
    PROCESSOR = "processor"
    MEMORY = "memory"


@app.get("/healthcheck")
def healthcheck(db: Session = Depends(get_db)) -> dict:
    """Return database hash and last udpated timestamp."""
    return {
        "database_last_updated": session.last_updated,
        "database_hash": session.db_hash,
    }


@app.get("/server/{vendor_id}/{server_id}")
def read_server(
    vendor_id: str, server_id: str, db: Session = Depends(get_db)
) -> ServerPKs:
    server = db.get(Server, (vendor_id, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


class DatacenterBaseWithPKs(DatacenterBase):
    country: CountryBase


class ServerPriceWithPKs(ServerPriceBase):
    vendor: VendorBase
    datacenter: DatacenterBaseWithPKs
    zone: ZoneBase
    server: ServerBase


@app.get("/search")
def search_server(
    vcpus_min: Annotated[
        int,
        Query(
            title="Processor number",
            description="Minimum number of virtual CPUs.",
            ge=1,
            le=128,
            json_schema_extra={
                "category_id": FilterCategories.PROCESSOR,
                "unit": "vCPUs",
            },
        ),
    ] = 1,
    memory_min: Annotated[
        Optional[int],
        Query(
            title="Memory amount",
            description="Minimum amount of memory in MBs.",
            json_schema_extra={
                "category_id": FilterCategories.MEMORY,
                "unit": "GB",
                "step": 0.1,
            },
        ),
    ] = None,
    price_max: Annotated[
        Optional[float],
        Query(
            title="Maximum price",
            description="Maximum price (USD/hr).",
            json_schema_extra={"category_id": FilterCategories.PRICE, "step": 0.0001},
        ),
    ] = None,
    only_active: Annotated[
        Optional[bool],
        Query(
            title="Active only",
            description="Show only active servers",
            json_schema_extra={"category_id": FilterCategories.BASIC},
        ),
    ] = None,
    limit: Annotated[
        int, Query(description="Maximum number of results. Set to -1 for unlimited")
    ] = 50,
    page: Annotated[Optional[int], Query(description="Page number.")] = None,
    order_by: Annotated[str, Query(description="Order by column.")] = "price",
    order_dir: Annotated[
        OrderDir, Query(description="Order direction.")
    ] = OrderDir.ASC,
    currency: Annotated[str, Query(description="Currency used for prices.")] = "USD",
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

    # ordering
    if order_by:
        if hasattr(ServerPrice, order_by):
            order_field = getattr(ServerPrice, order_by)
            if OrderDir(order_dir) == OrderDir.ASC:
                query = query.order_by(order_field)
            else:
                query = query.order_by(order_field.desc())

    # pagination
    if limit > 0:
        query = query.limit(limit)
    # only apply if limit is set
    if page and limit > 0:
        query = query.offset((page - 1) * limit)
    servers = db.exec(query).all()

    # update prices to currency requested
    for server in servers:
        if hasattr(server, "price") and hasattr(server, "currency"):
            if server.currency != currency:
                server.price = round(
                    currency_converter.convert(server.price, server.currency, currency),
                    4,
                )
                server.currency = currency

    return servers


## https://fastapi-filter.netlify.app/#examples
