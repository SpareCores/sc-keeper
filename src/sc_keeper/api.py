import logging
from contextlib import asynccontextmanager
from enum import Enum, StrEnum
from textwrap import dedent
from types import SimpleNamespace
from typing import Annotated, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from sc_crawler.table_bases import (
    BenchmarkScoreBase,
    CountryBase,
    RegionBase,
    ServerBase,
    ServerPriceBase,
    VendorBase,
    ZoneBase,
)
from sc_crawler.table_fields import Allocation, CpuArchitecture, Status, StorageType
from sc_crawler.tables import (
    Benchmark,
    BenchmarkScore,
    ComplianceFramework,
    Country,
    Region,
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
from .logger import LogMiddleware, get_request_id


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
Regions = StrEnum(
    "Regions",
    {m.region_id: m.region_id for m in db.exec(select(Region)).all()},
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


class IdNameAndDescription(NameAndDescription):
    id: str


class TableMetaData(BaseModel):
    table: NameAndDescription
    fields: List[IdNameAndDescription]


class IdNameAndDescriptionAndCategory(IdNameAndDescription):
    category: str


class ServerTableMetaData(TableMetaData):
    fields: List[IdNameAndDescriptionAndCategory]


class ServerPKs(ServerBase):
    vendor: VendorBase


class ServerPricePKs(ServerPriceBase):
    region: RegionBase
    zone: ZoneBase


class ServerPKsWithPrices(ServerPKs):
    prices: List[ServerPricePKs]
    benchmark_scores: List[BenchmarkScoreBase]


class RegionPKs(RegionBase):
    vendor: VendorBase


class RegionBaseWithPKs(RegionBase):
    country: CountryBase


class ServerPriceWithPKs(ServerPriceBase):
    vendor: VendorBase
    region: RegionBaseWithPKs
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
    REGION = "region"
    VENDOR = "vendor"
    STORAGE = "storage"
    GPU = "gpu"


# load examples for the docs
example_data = {
    "benchmark": db.exec(
        select(Benchmark).where(Benchmark.benchmark_id == "geekbench:hdr")
    ).one(),
    "country": db.exec(select(Country).limit(1)).one(),
    "compliance_framework": db.exec(select(ComplianceFramework).limit(1)).one(),
    "vendor": db.exec(select(Vendor).where(Vendor.vendor_id == "aws")).one(),
    "region": db.exec(select(Region).where(Region.vendor_id == "aws").limit(1)).one(),
    "zone": db.exec(select(Zone).where(Zone.vendor_id == "aws").limit(1)).one(),
    "server": db.exec(select(Server).where(Server.vendor_id == "aws").limit(1)).one(),
    "storage": db.exec(
        select(Storage).where(Storage.vendor_id == "aws").limit(1)
    ).one(),
    "prices": db.exec(
        select(ServerPrice).where(ServerPrice.vendor_id == "aws").limit(5)
    ).all(),
}

Benchmark.model_config["json_schema_extra"] = {
    "examples": [example_data["benchmark"].model_dump()]
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
Region.model_config["json_schema_extra"] = {
    "examples": [example_data["region"].model_dump()]
}
RegionPKs.model_config["json_schema_extra"] = {
    "examples": [
        example_data["region"].model_dump()
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
                    "region": example_data["region"].model_dump(),
                    "zone": example_data["zone"].model_dump(),
                }
                for p in example_data["prices"]
            ],
            "benchmarks": [example_data["benchmark"].model_dump()],
        }
    ]
}
ServerPriceWithPKs.model_config["json_schema_extra"] = {
    "examples": [
        example_data["prices"][0].model_dump()
        | {
            "vendor": example_data["vendor"].model_dump(),
            "region": example_data["region"].model_dump()
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
# Shared parameters

options = SimpleNamespace(
    vendor=Annotated[
        Optional[List[Vendors]],
        Query(
            title="Vendor id",
            description="Identifier of the cloud provider vendor.",
            json_schema_extra={
                "category_id": FilterCategories.VENDOR,
                "enum": [m.value for m in Vendors],
            },
        ),
    ],
    vcpus_min=Annotated[
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
    ],
    architecture=Annotated[
        Optional[List[CpuArchitecture]],
        Query(
            title="Processor architecture",
            description="Processor architecture.",
            json_schema_extra={
                "category_id": FilterCategories.PROCESSOR,
                "enum": [e.value for e in CpuArchitecture],
            },
        ),
    ],
    memory_min=Annotated[
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
    ],
    price_max=Annotated[
        Optional[float],
        Query(
            title="Maximum price",
            description="Maximum price (USD/hr).",
            json_schema_extra={
                "category_id": FilterCategories.PRICE,
                "step": 0.0001,
            },
        ),
    ],
    only_active=Annotated[
        Optional[bool],
        Query(
            title="Active only",
            description="Filter for active servers only.",
            json_schema_extra={"category_id": FilterCategories.BASIC},
        ),
    ],
    green_energy=Annotated[
        Optional[bool],
        Query(
            title="Green energy",
            description="Filter for regions with kow CO2 emission only.",
            json_schema_extra={"category_id": FilterCategories.REGION},
        ),
    ],
    allocation=Annotated[
        Optional[Allocation],
        Query(
            title="Allocation",
            description="Server allocation method.",
            json_schema_extra={
                "enum": [m.value for m in Allocation],
            },
        ),
    ],
    regions=Annotated[
        Optional[List[Regions]],
        Query(
            title="region id",
            description="Identifier of the region.",
            json_schema_extra={
                "category_id": FilterCategories.REGION,
                "enum": [m.value for m in Regions],
            },
        ),
    ],
    compliance_framework=Annotated[
        Optional[List[ComplianceFrameworks]],
        Query(
            title="Compliance Framework id",
            description="Compliance framework implemented at the vendor.",
            json_schema_extra={
                "category_id": FilterCategories.VENDOR,
                "enum": [m.value for m in ComplianceFrameworks],
            },
        ),
    ],
    storage_size=Annotated[
        Optional[float],
        Query(
            title="Storage Size",
            description="Minimum amount of storage (GBs) attached to the server.",
            json_schema_extra={
                "category_id": FilterCategories.STORAGE,
                "step": 0.1,
                "unit": "GB",
            },
        ),
    ],
    storage_type=Annotated[
        Optional[List[StorageType]],
        Query(
            title="Storage Type",
            description="Type of the storage attached to the server.",
            json_schema_extra={
                "category_id": FilterCategories.STORAGE,
                "enum": [e.value for e in StorageType],
            },
        ),
    ],
    countries=Annotated[
        Optional[List[str]],
        Query(
            title="Countries",
            description="Filter for regions in the provided list of countries.",
            json_schema_extra={
                "category_id": FilterCategories.REGION,
                "enum": [e.value for e in Countries],
            },
        ),
    ],
    gpu_min=Annotated[
        Optional[int],
        Query(
            title="GPU count",
            description="Minimum number of GPUs.",
            json_schema_extra={
                "category_id": FilterCategories.GPU,
                "unit": "GPUs",
            },
        ),
    ],
    gpu_memory_min=Annotated[
        Optional[float],
        Query(
            title="GPU memory",
            description="Minimum amount of GPU memory in GBs.",
            json_schema_extra={
                "category_id": FilterCategories.GPU,
                "unit": "GB",
                "step": 0.1,
            },
        ),
    ],
    limit=Annotated[
        int, Query(description="Maximum number of results. Set to -1 for unlimited")
    ],
    page=Annotated[Optional[int], Query(description="Page number.")],
    order_by=Annotated[str, Query(description="Order by column.")],
    order_dir=Annotated[OrderDir, Query(description="Order direction.")],
    currency=Annotated[str, Query(description="Currency used for prices.")],
    add_total_count_header=Annotated[
        bool,
        Query(
            description="Add the X-Total-Count header to the response with the overall number of items (without paging). Note that it might reduce response times."
        ),
    ],
)

# ##############################################################################
# API endpoints


@app.get("/healthcheck", tags=["Administrative endpoints"])
def healthcheck(db: Session = Depends(get_db)) -> dict:
    """Return database hash and last udpated timestamp."""
    return {
        "database_last_updated": session.last_updated,
        "database_hash": session.db_hash,
    }


@app.get("/table/benchmark", tags=["Table dumps"])
def table_benchmark(db: Session = Depends(get_db)) -> List[Benchmark]:
    """Return the Benchmark table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Benchmark)).all()


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


@app.get("/table/region", tags=["Table dumps"])
def table_region(db: Session = Depends(get_db)) -> List[Region]:
    """Return the Region table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Region)).all()


@app.get("/table/zone", tags=["Table dumps"])
def table_zone(db: Session = Depends(get_db)) -> List[Zone]:
    """Return the Zone table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Zone)).all()


@app.get("/table/server", tags=["Table dumps"])
def table_server(db: Session = Depends(get_db)) -> List[Server]:
    """Return the Server table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Server)).all()


@app.get("/table/storage", tags=["Table dumps"])
def table_storage(db: Session = Depends(get_db)) -> List[Storage]:
    """Return the Storage table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Storage)).all()


def _get_category(server_column_name: str) -> str:
    if server_column_name not in Server.get_columns()["all"]:
        raise KeyError("Unknown Server column name.")
    if server_column_name in [
        "vendor_id",
        "server_id",
        "name",
        "api_reference",
        "display_name",
        "description",
        "family",
        "status",
        "observed_at",
    ]:
        return "meta"
    if server_column_name in ["vcpus", "hypervisor"] or server_column_name.startswith(
        "cpu"
    ):
        return "cpu"
    if server_column_name.startswith("memory"):
        return "memory"
    if server_column_name.startswith("gpu"):
        return "gpu"
    if server_column_name.startswith("storage"):
        return "storage"
    if (
        server_column_name.endswith("_traffic")
        or server_column_name.startswith("network")
        or server_column_name == "ipv4"
    ):
        return "network"


def _get_name(server_column_name: str) -> str:
    # special cases
    mapping = {
        "vcpus": "vCPUs",
        "cpus": "CPUs",
        "gpus": "GPUs",
        "ipv4": "IPv4",
    }
    if server_column_name in mapping:
        return mapping[server_column_name]
    name = server_column_name.replace("_", " ").title()
    name = name.replace(" Id", " ID")
    name = name.replace("Api ", "API ")
    name = name.replace("Cpu ", "CPU ")
    name = name.replace("Gpu ", "GPU ")
    name = name.replace(" Ecc", " ECC")
    return name


@app.get("/table/server/meta", tags=["Table metadata"])
def table_metadata_server(db: Session = Depends(get_db)) -> ServerTableMetaData:
    """Server table and column names and comments."""
    table = {
        "name": Server.get_table_name(),
        "description": Server.__doc__.splitlines()[0],
    }
    fields = [
        {
            "id": k,
            "name": _get_name(k),
            "description": v.description,
            "category": _get_category(k),
        }
        for k, v in Server.model_fields.items()
    ]
    return {"table": table, "fields": fields}


@app.get("/regions", tags=["Query Resources"])
def search_regions(
    vendor: options.vendor = None,
    db: Session = Depends(get_db),
) -> List[RegionPKs]:
    query = select(Region)
    if vendor:
        query = query.where(Region.vendor_id.in_(vendor))
    return db.exec(query).all()


@app.get("/server/{vendor}/{server}", tags=["Query Resources"])
def get_server(
    vendor: Annotated[str, Path(description="Vendor ID.")],
    server: Annotated[str, Path(description="Server ID or API reference.")],
    db: Session = Depends(get_db),
) -> ServerPKsWithPrices:
    """Query a single server by its vendor id and either the server or, or its API reference.

    Return dictionary includes all server fields, along
    with the current prices per zone, and
    the available benchmark scores.
    """
    # TODO async
    res = db.exec(
        select(Server)
        .where(Server.vendor_id == vendor)
        .where((Server.server_id == server) | (Server.api_reference == server))
    ).all()
    if not res:
        raise HTTPException(status_code=404, detail="Server not found")
    res = res[0]
    prices = db.exec(
        select(ServerPrice)
        .where(ServerPrice.status == Status.ACTIVE)
        .where(ServerPrice.vendor_id == vendor)
        .where(ServerPrice.server_id == server)
    ).all()
    res.prices = prices
    benchmarks = db.exec(
        select(BenchmarkScore)
        .where(BenchmarkScore.status == Status.ACTIVE)
        .where(BenchmarkScore.vendor_id == vendor)
        .where(BenchmarkScore.server_id == server)
    ).all()
    res.benchmark_scores = benchmarks
    return res


@app.get("/servers", tags=["Query Resources"])
def search_servers(
    response: Response,
    vcpus_min: options.vcpus_min = 1,
    architecture: options.architecture = None,
    memory_min: options.memory_min = None,
    only_active: options.only_active = True,
    vendor: options.vendor = None,
    compliance_framework: options.compliance_framework = None,
    storage_size: options.storage_size = None,
    storage_type: options.storage_type = None,
    gpu_min: options.gpu_min = None,
    gpu_memory_min: options.gpu_memory_min = None,
    limit: options.limit = 50,
    page: options.page = None,
    order_by: options.order_by = "vcpus",
    order_dir: options.order_dir = OrderDir.ASC,
    add_total_count_header: options.add_total_count_header = False,
    db: Session = Depends(get_db),
) -> List[ServerPKs]:
    query = (
        select(Server)
        .join(Server.vendor)
        .join(Vendor.compliance_framework_links)
        .join(VendorComplianceLink.compliance_framework)
    )

    if vcpus_min:
        query = query.where(Server.vcpus >= vcpus_min)
    if memory_min:
        query = query.where(Server.memory_amount >= memory_min * 1024)
    if storage_size:
        query = query.where(Server.storage_size >= storage_size)
    if gpu_min:
        query = query.where(Server.gpu_count >= gpu_min)
    if gpu_memory_min:
        query = query.where(Server.gpu_memory_min >= gpu_memory_min * 1024)
    if only_active:
        query = query.where(Server.status == Status.ACTIVE)
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

    # ordering
    if order_by:
        order_obj = [o for o in [Server] if hasattr(o, order_by)]
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

    return servers


@app.get("/server_prices", tags=["Query Resources"])
def search_server_prices(
    response: Response,
    vcpus_min: options.vcpus_min = 1,
    architecture: options.architecture = None,
    memory_min: options.memory_min = None,
    price_max: options.price_max = None,
    only_active: options.only_active = True,
    green_energy: options.green_energy = None,
    allocation: options.allocation = None,
    vendor: options.vendor = None,
    regions: options.regions = None,
    compliance_framework: options.compliance_framework = None,
    storage_size: options.storage_size = None,
    storage_type: options.storage_type = None,
    countries: options.countries = None,
    gpu_min: options.gpu_min = None,
    gpu_memory_min: options.gpu_memory_min = None,
    limit: options.limit = 50,
    page: options.page = None,
    order_by: options.order_by = "price",
    order_dir: options.order_dir = OrderDir.ASC,
    currency: options.currency = "USD",
    add_total_count_header: options.add_total_count_header = False,
    db: Session = Depends(get_db),
) -> List[ServerPriceWithPKs]:
    query = (
        select(ServerPrice)
        .join(ServerPrice.vendor)
        .join(Vendor.compliance_framework_links)
        .join(VendorComplianceLink.compliance_framework)
        .join(ServerPrice.region)
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
        query = query.where(Server.memory_amount >= memory_min * 1024)
    if storage_size:
        query = query.where(Server.storage_size >= storage_size)
    if gpu_min:
        query = query.where(Server.gpu_count >= gpu_min)
    if gpu_memory_min:
        query = query.where(Server.gpu_memory_min >= gpu_memory_min * 1024)
    if only_active:
        query = query.where(Server.status == Status.ACTIVE)
    if green_energy:
        query = query.where(Region.green_energy == green_energy)
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
    if regions:
        query = query.where(ServerPrice.region_id.in_(regions))
    if countries:
        query = query.where(Region.country_id.in_(countries))

    # ordering
    if order_by:
        order_obj = [o for o in [ServerPrice, Server, Region] if hasattr(o, order_by)]
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


@app.get("/ai/assist_server_filters", tags=["AI"])
def assist_server_filters(text: str, request: Request) -> dict:
    """Extract Server JSON filters from freetext."""
    res = openai_extract_filters(text, endpoint="/servers")
    logging.info(
        "openai response",
        extra={
            "event": "assist_filters response",
            "res": res,
            "request_id": get_request_id(),
        },
    )
    return res


@app.get("/ai/assist_server_price_filters", tags=["AI"])
def assist_server_price_filters(text: str, request: Request) -> dict:
    """Extract ServerPrice JSON filters from freetext."""
    res = openai_extract_filters(text, endpoint="/server_prices")
    logging.info(
        "openai response",
        extra={
            "event": "assist_filters response",
            "res": res,
            "request_id": get_request_id(),
        },
    )
    return res
