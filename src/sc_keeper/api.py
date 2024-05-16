from contextlib import asynccontextmanager
from enum import Enum, StrEnum
from textwrap import dedent
from typing import Annotated, List, Optional
import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from sc_crawler.table_bases import (
    CountryBase,
    DatacenterBase,
    ServerBase,
    ServerPriceBase,
    VendorBase,
    ZoneBase,
)
from sc_crawler.table_fields import Allocation, CpuArchitecture, Status, StorageType
from sc_crawler.tables import (
    ComplianceFramework,
    Country,
    Datacenter,
    Server,
    ServerPrice,
    Storage,
    Vendor,
    VendorComplianceLink,
    Zone,
)
from sqlmodel import Session, func, select

from .ai import openai_extract_filters
from .currency import CurrencyConverter
from .database import session
from .logger import get_request_id, LogMiddleware


def get_db():
    db = session.sessionmaker
    try:
        yield db
    finally:
        db.close()


db = next(get_db())
currency_converter = CurrencyConverter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    yield
    # shutdown
    pass


# make sure we have a fresh database
session.updated.wait()

# ##############################################################################
# Helper classes

# create enums from DB values for filtering options
Countries = StrEnum(
    "Countries", {m.country_id: m.country_id for m in db.exec(select(Country)).all()}
)
Vendors = StrEnum(
    "Vendors", {m.vendor_id: m.vendor_id for m in db.exec(select(Vendor)).all()}
)
Datacenters = StrEnum(
    "Datacenters",
    {m.display_name: m.datacenter_id for m in db.exec(select(Datacenter)).all()},
)
ComplianceFrameworks = StrEnum(
    "ComplianceFrameworks",
    {
        m.compliance_framework_id: m.compliance_framework_id
        for m in db.exec(select(ComplianceFramework)).all()
    },
)


class NameAndDescription(BaseModel):
    name: str
    description: str


class TableMetaData(BaseModel):
    table: NameAndDescription
    fields: List[NameAndDescription]


class ServerPKs(ServerBase):
    vendor: VendorBase


class ServerPricePKs(ServerPriceBase):
    datacenter: DatacenterBase
    zone: ZoneBase


class ServerPKsWithPrices(ServerPKs):
    prices: List[ServerPricePKs]


class DatacenterPKs(DatacenterBase):
    vendor: VendorBase


class DatacenterBaseWithPKs(DatacenterBase):
    country: CountryBase


class ServerPriceWithPKs(ServerPriceBase):
    vendor: VendorBase
    datacenter: DatacenterBaseWithPKs
    zone: ZoneBase
    server: ServerBase


class OrderDir(Enum):
    ASC = "asc"
    DESC = "desc"


class FilterCategories(Enum):
    BASIC = "basic"
    PRICE = "price"
    PROCESSOR = "processor"
    MEMORY = "memory"
    DATACENTER = "datacenter"
    VENDOR = "vendor"
    STORAGE = "storage"
    GPU = "gpu"


FILTERS = {
    "vendor": Annotated[
        Optional[List[Vendors]],
        Query(
            title="Vendor id",
            description="Cloud provider vendor.",
            json_schema_extra={
                "category_id": FilterCategories.VENDOR,
                "enum": [m.value for m in Vendors],
            },
        ),
    ]
}

# load examples for the docs
example_data = {
    "country": db.exec(select(Country).limit(1)).one(),
    "compliance_framework": db.exec(select(ComplianceFramework).limit(1)).one(),
    "vendor": db.exec(select(Vendor).where(Vendor.vendor_id == "aws")).one(),
    "datacenter": db.exec(
        select(Datacenter).where(Datacenter.vendor_id == "aws").limit(1)
    ).one(),
    "zone": db.exec(select(Zone).where(Zone.vendor_id == "aws").limit(1)).one(),
    "server": db.exec(select(Server).where(Server.vendor_id == "aws").limit(1)).one(),
    "storage": db.exec(
        select(Storage).where(Storage.vendor_id == "aws").limit(1)
    ).one(),
    "prices": db.exec(
        select(ServerPrice).where(ServerPrice.vendor_id == "aws").limit(5)
    ).all(),
}

Country.model_config["json_schema_extra"] = {
    "examples": [example_data["country"].model_dump()]
}
ComplianceFramework.model_config["json_schema_extra"] = {
    "examples": [example_data["compliance_framework"].model_dump()]
}
Vendor.model_config["json_schema_extra"] = {
    "examples": [example_data["vendor"].model_dump()]
}
Datacenter.model_config["json_schema_extra"] = {
    "examples": [example_data["datacenter"].model_dump()]
}
DatacenterPKs.model_config["json_schema_extra"] = {
    "examples": [
        example_data["datacenter"].model_dump()
        | {"vendor": example_data["vendor"].model_dump()}
    ]
}
Zone.model_config["json_schema_extra"] = {
    "examples": [example_data["zone"].model_dump()]
}
Server.model_config["json_schema_extra"] = {
    "examples": [example_data["server"].model_dump()]
}
Storage.model_config["json_schema_extra"] = {
    "examples": [example_data["storage"].model_dump()]
}
ServerPKsWithPrices.model_config["json_schema_extra"] = {
    "examples": [
        example_data["server"].model_dump()
        | {
            "vendor": example_data["vendor"].model_dump(),
            "prices": [
                p.model_dump()
                | {
                    "datacenter": example_data["datacenter"].model_dump(),
                    "zone": example_data["zone"].model_dump(),
                }
                for p in example_data["prices"]
            ],
        }
    ]
}
ServerPriceWithPKs.model_config["json_schema_extra"] = {
    "examples": [
        example_data["prices"][0].model_dump()
        | {
            "vendor": example_data["vendor"].model_dump(),
            "datacenter": example_data["datacenter"].model_dump()
            | {"country": example_data["country"].model_dump()},
            "zone": example_data["zone"].model_dump(),
            "server": example_data["server"].model_dump(),
        }
    ]
}

# ##############################################################################
# API metadata

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

# ##############################################################################
# Middlewares

# logging
app.add_middleware(LogMiddleware)

# CORS: allows all origins, without spec headers and without auth
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], expose_headers=["X-Total-Count"]
)

# aggressive compression
app.add_middleware(GZipMiddleware, minimum_size=100)


# ##############################################################################
# API endpoints


@app.get("/healthcheck", tags=["Administrative endpoints"])
def healthcheck(db: Session = Depends(get_db)) -> dict:
    """Return database hash and last udpated timestamp."""
    return {
        "database_last_updated": session.last_updated,
        "database_hash": session.db_hash,
    }


@app.get("/table/country", tags=["Table dumps"])
def table_country(db: Session = Depends(get_db)) -> List[Country]:
    """Return the Country table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Country)).all()


@app.get("/table/compliance_framework", tags=["Table dumps"])
def table_compliance_frameworks(
    db: Session = Depends(get_db),
) -> List[ComplianceFramework]:
    """Return the ComplianceFramework table as-is, without filtering options or relationships resolved."""
    return db.exec(select(ComplianceFramework)).all()


@app.get("/table/vendor", tags=["Table dumps"])
def table_vendor(db: Session = Depends(get_db)) -> List[Vendor]:
    """Return the Vendor table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Vendor)).all()


@app.get("/table/datacenter", tags=["Table dumps"])
def table_datacenter(db: Session = Depends(get_db)) -> List[Datacenter]:
    """Return the Datacenter table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Datacenter)).all()


@app.get("/table/zone", tags=["Table dumps"])
def table_zone(db: Session = Depends(get_db)) -> List[Zone]:
    """Return the Zone table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Zone)).all()


@app.get("/table/server", tags=["Table dumps"])
def table_server(db: Session = Depends(get_db)) -> List[Server]:
    """Return the Server table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Server)).all()


@app.get("/table/server/meta", tags=["Table metadata"])
def table_server(db: Session = Depends(get_db)) -> TableMetaData:
    """Server table and column names and comments."""
    table = {
        "name": Server.get_table_name(),
        "description": Server.__doc__.splitlines()[0],
    }
    fields = [
        {"name": k, "description": v.description}
        for k, v in Server.model_fields.items()
    ]
    return {"table": table, "fields": fields}


@app.get("/table/storage", tags=["Table dumps"])
def table_storage(db: Session = Depends(get_db)) -> List[Storage]:
    """Return the Storage table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Storage)).all()


@app.get("/datacenters", tags=["Query Resources"])
def search_datacenters(
    vendor: FILTERS["vendor"] = None,  # noqa F821
    db: Session = Depends(get_db),
) -> List[DatacenterPKs]:
    query = select(Datacenter)
    if vendor:
        query = query.where(Datacenter.vendor_id.in_(vendor))
    return db.exec(query).all()


@app.get("/server/{vendor_id}/{server_id}", tags=["Query Resources"])
def get_server(
    vendor_id: str, server_id: str, db: Session = Depends(get_db)
) -> ServerPKsWithPrices:
    # TODO async
    server = db.get(Server, (vendor_id, server_id))
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    prices = db.exec(
        select(ServerPrice)
        .where(ServerPrice.vendor_id == vendor_id)
        .where(ServerPrice.server_id == server_id)
    ).all()
    server.prices = prices
    return server


@app.get("/servers", tags=["Query Resources"])
def search_servers(
    response: Response,
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
    architecture: Annotated[
        Optional[List[CpuArchitecture]],
        Query(
            title="Processor architecture",
            description="Processor architecture.",
            json_schema_extra={
                "category_id": FilterCategories.PROCESSOR,
                "enum": [e.value for e in CpuArchitecture],
            },
        ),
    ] = None,
    memory_min: Annotated[
        Optional[float],
        Query(
            title="Memory amount",
            description="Minimum amount of memory in GBs.",
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
            json_schema_extra={
                "category_id": FilterCategories.PRICE,
                "step": 0.0001,
            },
        ),
    ] = None,
    only_active: Annotated[
        Optional[bool],
        Query(
            title="Active only",
            description="Show only active servers",
            json_schema_extra={"category_id": FilterCategories.BASIC},
        ),
    ] = True,
    green_energy: Annotated[
        Optional[bool],
        Query(
            title="Green energy",
            description="Low CO2 emission only.",
            json_schema_extra={"category_id": FilterCategories.DATACENTER},
        ),
    ] = None,
    allocation: Annotated[
        Optional[Allocation],
        Query(
            title="Allocation",
            description="Server allocation method.",
            json_schema_extra={
                "enum": [m.value for m in Allocation],
            },
        ),
    ] = None,
    vendor: FILTERS["vendor"] = None,  # noqa F821
    datacenters: Annotated[
        Optional[List[Datacenters]],
        Query(
            title="Datacenter id",
            description="Datacenter.",
            json_schema_extra={
                "category_id": FilterCategories.DATACENTER,
                "enum": [m.value for m in Datacenters],
                "labels": [m.name for m in Datacenters],
            },
        ),
    ] = None,
    compliance_framework: Annotated[
        Optional[List[ComplianceFrameworks]],
        Query(
            title="Compliance Framework id",
            description="Compliance framework implemented at the vendor.",
            json_schema_extra={
                "category_id": FilterCategories.VENDOR,
                "enum": [m.value for m in ComplianceFrameworks],
            },
        ),
    ] = None,
    storage_size: Annotated[
        Optional[float],
        Query(
            title="Storage Size",
            description="Reserver storage size in GBs.",
            json_schema_extra={
                "category_id": FilterCategories.STORAGE,
                "step": 0.1,
                "unit": "GB",
            },
        ),
    ] = None,
    storage_type: Annotated[
        Optional[List[StorageType]],
        Query(
            title="Storage Type",
            description="Storage type.",
            json_schema_extra={
                "category_id": FilterCategories.STORAGE,
                "enum": [e.value for e in StorageType],
            },
        ),
    ] = None,
    countries: Annotated[
        Optional[List[str]],
        Query(
            title="Countries",
            description="Datacenter countries.",
            json_schema_extra={
                "category_id": FilterCategories.DATACENTER,
                "enum": [e.value for e in Countries],
            },
        ),
    ] = None,
    gpu_min: Annotated[
        Optional[int],
        Query(
            title="GPU count",
            description="Number of GPUs.",
            json_schema_extra={
                "category_id": FilterCategories.GPU,
                "unit": "GPUs",
            },
        ),
    ] = None,
    gpu_memory_min: Annotated[
        Optional[float],
        Query(
            title="GPU memory",
            description="Amount of GPU memory in GBs.",
            json_schema_extra={
                "category_id": FilterCategories.GPU,
                "unit": "GB",
                "step": 0.1,
            },
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
    add_total_count_header: Annotated[
        bool,
        Query(
            description="Add the X-Total-Count header to the response with the overall number of items (without paging). Note that it might reduce response times."
        ),
    ] = False,
    db: Session = Depends(get_db),
) -> List[ServerPriceWithPKs]:
    query = (
        select(ServerPrice)
        .join(ServerPrice.vendor)
        .join(Vendor.compliance_framework_links)
        .join(VendorComplianceLink.compliance_framework)
        .join(ServerPrice.datacenter)
        .join(ServerPrice.zone)
        .join(ServerPrice.server)
    )

    if price_max:
        if currency != "USD":
            price_max = currency_converter.convert(price_max, currency, "USD")
        query = query.where(ServerPrice.price <= price_max)

    if vcpus_min:
        query = query.where(Server.vcpus >= vcpus_min)
    if memory_min:
        query = query.where(Server.memory >= memory_min * 1024)
    if storage_size:
        query = query.where(Server.storage_size >= storage_size)
    if gpu_min:
        query = query.where(Server.gpu_count >= gpu_min)
    if gpu_memory_min:
        query = query.where(Server.gpu_memory_min >= gpu_memory_min * 1024)
    if only_active:
        query = query.where(Server.status == Status.ACTIVE)
    if green_energy:
        query = query.where(Datacenter.green_energy == green_energy)
    if allocation:
        query = query.where(ServerPrice.allocation == allocation)
    if architecture:
        query = query.where(Server.cpu_architecture.in_(architecture))
    if storage_type:
        query = query.where(Server.storage_type.in_(storage_type))
    if vendor:
        query = query.where(Server.vendor_id.in_(vendor))
    if compliance_framework:
        query = query.where(
            VendorComplianceLink.compliance_framework_id.in_(compliance_framework)
        )
    if datacenters:
        query = query.where(ServerPrice.datacenter_id.in_(datacenters))
    if countries:
        query = query.where(Datacenter.country_id.in_(countries))

    # ordering
    if order_by:
        order_obj = [
            o for o in [ServerPrice, Server, Datacenter] if hasattr(o, order_by)
        ]
        if len(order_obj) == 0:
            raise HTTPException(status_code=400, detail="Unknown order_by field.")
        if len(order_obj) > 1:
            raise HTTPException(status_code=400, detail="Unambiguous order_by field.")
        order_field = getattr(order_obj[0], order_by)
        if OrderDir(order_dir) == OrderDir.ASC:
            query = query.order_by(order_field)
        else:
            query = query.order_by(order_field.desc())

    # avoid duplicate rows introduced by the many-to-many relationships
    query = query.distinct()

    # count all records to be returned in header
    if add_total_count_header:
        count_query = select(func.count()).select_from(query.alias("subquery"))
        response.headers["X-Total-Count"] = str(db.exec(count_query).one())

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


@app.get("/ai/assist_filters", tags=["AI"])
def assist_filters(text: str, request: Request) -> dict:
    """Extract JSON filters from freetext."""
    res = openai_extract_filters(text)
    logging.info(
        "openai response",
        extra={
            "event": "assist_filters response",
            "res": res,
            "request_id": get_request_id(),
        },
    )
    return res
