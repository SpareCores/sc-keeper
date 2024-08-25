import logging
from contextlib import asynccontextmanager
from importlib.metadata import version
from os import environ
from textwrap import dedent
from typing import Annotated, List, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sc_crawler.table_fields import Status
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
from sqlalchemy.orm import contains_eager
from sqlmodel import Session, and_, func, not_, or_, select

from . import parameters as options
from .ai import openai_extract_filters
from .currency import CurrencyConverter
from .database import get_db, session
from .helpers import get_server_base
from .logger import LogMiddleware, get_request_id
from .lookups import min_server_price
from .query import max_score_per_server
from .references import (
    OrderDir,
    RegionPKs,
    ServerPKs,
    ServerPKsWithPrices,
    ServerPriceWithPKs,
    ServerTableMetaData,
)
from .routers import server_v2

package_versions = {
    pkg: version(pkg)
    for pkg in ["sparecores-crawler", "sparecores-data", "sparecores-keeper"]
}

if environ.get("SENTRY_DSN"):
    import sentry_sdk

    sentry_sdk.init(
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )


db = next(get_db())
currency_converter = CurrencyConverter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    yield
    # shutdown
    pass


# ##############################################################################
# Load examples for the API docs

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
ServerPKs.model_config["json_schema_extra"] = Server.model_config["json_schema_extra"]
ServerPKs.model_config["json_schema_extra"]["examples"][0]["score"] = 42
ServerPKs.model_config["json_schema_extra"]["examples"][0]["price"] = 7
ServerPKs.model_config["json_schema_extra"]["examples"][0]["score_per_price"] = 42 / 7
Storage.model_config["json_schema_extra"] = {
    "examples": [example_data["storage"].model_dump()]
}
ServerPKsWithPrices.model_config["json_schema_extra"] = {
    "examples": [
        ServerPKs.model_config["json_schema_extra"]["examples"][0]
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
            "benchmark_scores": [example_data["benchmark"].model_dump()],
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
            "server": ServerPKs.model_config["json_schema_extra"]["examples"][0],
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
    version=version("sparecores-keeper"),
    terms_of_service="https://sparecores.com/legal/terms-of-service",
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

app.include_router(server_v2.router, prefix="/v2")


# ##############################################################################
# Middlewares

# logging
app.add_middleware(LogMiddleware)

# CORS: allows all origins, without spec headers and without auth
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["sentry-trace", "baggage"],
    expose_headers=["X-Total-Count"],
)

# aggressive compression
app.add_middleware(GZipMiddleware, minimum_size=100)


# ##############################################################################
# API endpoints


@app.get("/healthcheck", tags=["Administrative endpoints"])
def healthcheck(db: Session = Depends(get_db)) -> dict:
    """Return database hash and last udpated timestamp."""
    return {
        "packages": package_versions,
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


def _get_unit(server_column_name: str) -> str:
    mapping = {
        "cpu_speed": "GHz",
        "cpu_l1_cache": "byte",
        "cpu_l2_cache": "byte",
        "cpu_l3_cache": "byte",
        "memory_amount": "MiB",
        "memory_speed": "Mhz",
        "gpu_memory_min": "MiB",
        "gpu_memory_total": "MiB",
        "storage_size": "GB",
        "network_speed": "Gbps",
        "inbound_traffic": "GB/month",
        "outbound_traffic": "GB/month",
    }
    if server_column_name in mapping:
        return mapping[server_column_name]
    return None


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
            "unit": _get_unit(k),
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


@app.get("/server/{vendor}/{server}", tags=["Query Resources"], deprecated=True)
def get_server(
    vendor: Annotated[str, Path(description="Vendor ID.")],
    server: Annotated[str, Path(description="Server ID or API reference.")],
    currency: options.currency = None,
    db: Session = Depends(get_db),
) -> ServerPKsWithPrices:
    """Query a single server by its vendor id and either the server or, or its API reference.

    Return dictionary includes all server fields, along
    with the current prices per zone, and
    the available benchmark scores.
    """
    # TODO async
    res = get_server_base(vendor, server, db)
    prices = db.exec(
        select(ServerPrice)
        .where(ServerPrice.status == Status.ACTIVE)
        .where(ServerPrice.vendor_id == vendor)
        .where(ServerPrice.server_id == res.server_id)
        .join(ServerPrice.zone)
        .options(contains_eager(ServerPrice.zone))
        .join(ServerPrice.region)
        .options(contains_eager(ServerPrice.region))
    ).all()
    if currency:
        for price in prices:
            if hasattr(price, "price") and hasattr(price, "currency"):
                if price.currency != currency:
                    price.price = round(
                        currency_converter.convert(
                            price.price, price.currency, currency
                        ),
                        4,
                    )
                    price.currency = currency

    res.prices = prices
    benchmarks = db.exec(
        select(BenchmarkScore)
        .where(BenchmarkScore.status == Status.ACTIVE)
        .where(BenchmarkScore.vendor_id == vendor)
        .where(BenchmarkScore.server_id == res.server_id)
    ).all()
    res.benchmark_scores = benchmarks
    # SCore and $Core
    res = ServerPKsWithPrices.from_orm(res)
    res.score = max(
        [b.score for b in benchmarks if b.benchmark_id == "stress_ng:cpu_all"],
        default=None,
    )
    try:
        res.price = min_server_price(db, res.vendor_id, res.server_id)
    except KeyError:
        res.price = None
    res.score_per_price = res.score / res.price if res.price and res.score else None

    return res


@app.get("/server/{vendor}/{server}/similar_servers/{by}/{n}", tags=["Query Resources"])
def get_similar_servers(
    vendor: Annotated[str, Path(description="Vendor ID.")],
    server: Annotated[str, Path(description="Server ID or API reference.")],
    by: Annotated[
        Literal["family", "specs", "score"],
        Path(description="Algorithm to look for similar servers."),
    ],
    n: Annotated[
        int,
        Path(description="Number of servers to get.", le=100),
    ],
    db: Session = Depends(get_db),
) -> List[ServerPKs]:
    """Search similar servers to the provided one.

    The "family" method returns all servers from the same family of
    the same vendor.

    The "specs" approach will prioritize the number of
    GPUs, then CPUs, lastly the amount of memory.

    The "score" method will find the servers with the closest
    performance using the multi-core SCore.
    """
    serverobj = get_server_base(vendor, server, db)

    max_scores = max_score_per_server()
    query = (
        select(Server, max_scores.c.score)
        .join(
            max_scores,
            (Server.vendor_id == max_scores.c.vendor_id)
            & (Server.server_id == max_scores.c.server_id),
            isouter=True,
        )
        .where(
            not_(
                and_(
                    Server.vendor_id == serverobj.vendor_id,
                    Server.server_id == serverobj.server_id,
                )
            )
        )
    )

    if by == "family":
        query = (
            query.where(Server.vendor_id == serverobj.vendor_id)
            .where(Server.family == serverobj.family)
            .order_by(Server.vcpus, Server.gpu_count, Server.memory_amount)
        )

    if by == "specs":
        query = query.order_by(
            func.abs(Server.gpu_count - serverobj.gpu_count) * 10e6
            + func.abs(Server.vcpus - serverobj.vcpus) * 10e3
            + func.abs(Server.memory_amount - serverobj.memory_amount) / 1e03
        )

    if by == "score":
        max_score = db.exec(
            select(max_scores.c.score)
            .where(max_scores.c.vendor_id == serverobj.vendor_id)
            .where(max_scores.c.server_id == serverobj.server_id)
        ).one()
        query = query.order_by(func.abs(max_scores.c.score - max_score))

    servers = db.exec(query.limit(n)).all()

    serverlist = []
    for server in servers:
        serveri = ServerPKs.from_orm(server[0])
        serveri.score = server[1]
        try:
            serveri.price = min_server_price(db, serveri.vendor_id, serveri.server_id)
            serveri.score_per_price = serveri.score / serveri.price
        except Exception:
            serveri.score_per_price = None
        serverlist.append(serveri)

    return serverlist


@app.get("/servers", tags=["Query Resources"])
def search_servers(
    response: Response,
    partial_name_or_id: options.partial_name_or_id = None,
    vcpus_min: options.vcpus_min = 1,
    architecture: options.architecture = None,
    benchmark_score_stressng_cpu_min: options.benchmark_score_stressng_cpu_min = None,
    memory_min: options.memory_min = None,
    only_active: options.only_active = True,
    vendor: options.vendor = None,
    compliance_framework: options.compliance_framework = None,
    storage_size: options.storage_size = None,
    storage_type: options.storage_type = None,
    gpu_min: options.gpu_min = None,
    gpu_memory_min: options.gpu_memory_min = None,
    gpu_memory_total: options.gpu_memory_total = None,
    limit: options.limit = 50,
    page: options.page = None,
    order_by: options.order_by = "vcpus",
    order_dir: options.order_dir = OrderDir.ASC,
    add_total_count_header: options.add_total_count_header = False,
    db: Session = Depends(get_db),
) -> List[ServerPKs]:
    max_scores = max_score_per_server()

    # compliance frameworks are defined at the vendor level,
    # let's filter for vendors instead of exploding the servers table
    if compliance_framework:
        if not vendor:
            vendor = db.exec(select(Vendor.vendor_id)).all()
        query = select(VendorComplianceLink.vendor_id).where(
            VendorComplianceLink.compliance_framework_id.in_(compliance_framework)
        )
        compliant_vendors = db.exec(query).all()
        vendor = list(set(vendor or []) & set(compliant_vendors))

    # keep track of filter conditions
    conditions = set()

    if partial_name_or_id:
        ilike = "%" + partial_name_or_id + "%"
        conditions.add(
            or_(
                Server.server_id.ilike(ilike),
                Server.name.ilike(ilike),
                Server.api_reference.ilike(ilike),
                Server.display_name.ilike(ilike),
            )
        )

    if vcpus_min:
        conditions.add(Server.vcpus >= vcpus_min)
    if architecture:
        conditions.add(Server.cpu_architecture.in_(architecture))
    if benchmark_score_stressng_cpu_min:
        conditions.add(max_scores.c.score > benchmark_score_stressng_cpu_min)
    if memory_min:
        conditions.add(Server.memory_amount >= memory_min * 1024)
    if storage_size:
        conditions.add(Server.storage_size >= storage_size)
    if gpu_min:
        conditions.add(Server.gpu_count >= gpu_min)
    if gpu_memory_min:
        conditions.add(Server.gpu_memory_min >= gpu_memory_min * 1024)
    if gpu_memory_total:
        conditions.add(Server.gpu_memory_total >= gpu_memory_total * 1024)
    if only_active:
        conditions.add(Server.status == Status.ACTIVE)
    if storage_type:
        conditions.add(Server.storage_type.in_(storage_type))
    if vendor:
        conditions.add(Server.vendor_id.in_(vendor))

    # count all records to be returned in header
    if add_total_count_header:
        query = select(func.count()).select_from(Server)
        if benchmark_score_stressng_cpu_min:
            query = query.join(
                max_scores,
                (Server.vendor_id == max_scores.c.vendor_id)
                & (Server.server_id == max_scores.c.server_id),
                isouter=True,
            )
        for condition in conditions:
            query = query.where(condition)
        response.headers["X-Total-Count"] = str(db.exec(query).one())

    # actual query
    query = select(Server, max_scores.c.score)
    query = query.join(Server.vendor)
    query = query.join(
        max_scores,
        (Server.vendor_id == max_scores.c.vendor_id)
        & (Server.server_id == max_scores.c.server_id),
        isouter=True,
    )
    query = query.options(contains_eager(Server.vendor))
    for condition in conditions:
        query = query.where(condition)

    # ordering
    if order_by:
        order_obj = [o for o in [Server, max_scores.c] if hasattr(o, order_by)]
        if len(order_obj) == 0:
            raise HTTPException(status_code=400, detail="Unknown order_by field.")
        if len(order_obj) > 1:
            raise HTTPException(status_code=400, detail="Unambiguous order_by field.")
        order_field = getattr(order_obj[0], order_by)
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

    # unpack score
    serverlist = []
    for server in servers:
        serveri = ServerPKs.from_orm(server[0])
        serveri.score = server[1]
        try:
            serveri.price = min_server_price(db, serveri.vendor_id, serveri.server_id)
            serveri.score_per_price = serveri.score / serveri.price
        except Exception:
            serveri.score_per_price = None
        serverlist.append(serveri)

    return serverlist


@app.get("/server_prices", tags=["Query Resources"])
def search_server_prices(
    response: Response,
    partial_name_or_id: options.partial_name_or_id = None,
    vcpus_min: options.vcpus_min = None,
    architecture: options.architecture = None,
    benchmark_score_stressng_cpu_min: options.benchmark_score_stressng_cpu_min = None,
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
    gpu_memory_total: options.gpu_memory_total = None,
    limit: options.limit250 = 50,
    page: options.page = None,
    order_by: options.order_by = "price",
    order_dir: options.order_dir = OrderDir.ASC,
    currency: options.currency = "USD",
    add_total_count_header: options.add_total_count_header = False,
    db: Session = Depends(get_db),
) -> List[ServerPriceWithPKs]:
    max_scores = max_score_per_server()

    # compliance frameworks are defined at the vendor level,
    # let's filter for vendors instead of exploding the prices table
    if compliance_framework:
        if not vendor:
            vendor = db.exec(select(Vendor.vendor_id)).all()
        query = select(VendorComplianceLink.vendor_id).where(
            VendorComplianceLink.compliance_framework_id.in_(compliance_framework)
        )
        compliant_vendors = db.exec(query).all()
        vendor = list(set(vendor or []) & set(compliant_vendors))

    # keep track of tables to be joins and filter conditions
    joins = set()
    conditions = set()

    if partial_name_or_id:
        ilike = "%" + partial_name_or_id + "%"
        joins.add(ServerPrice.server)
        conditions.add(
            or_(
                ServerPrice.server_id.ilike(ilike),
                Server.name.ilike(ilike),
                Server.api_reference.ilike(ilike),
                Server.display_name.ilike(ilike),
            )
        )

    if price_max:
        if currency != "USD":
            price_max = currency_converter.convert(price_max, currency, "USD")
        conditions.add(ServerPrice.price <= price_max)

    if vcpus_min:
        joins.add(ServerPrice.server)
        conditions.add(Server.vcpus >= vcpus_min)
    if architecture:
        joins.add(ServerPrice.server)
        conditions.add(Server.cpu_architecture.in_(architecture))
    if benchmark_score_stressng_cpu_min:
        conditions.add(max_scores.c.score > benchmark_score_stressng_cpu_min)
    if memory_min:
        joins.add(ServerPrice.server)
        conditions.add(Server.memory_amount >= memory_min * 1024)
    if storage_size:
        joins.add(ServerPrice.server)
        conditions.add(Server.storage_size >= storage_size)
    if gpu_min:
        joins.add(ServerPrice.server)
        conditions.add(Server.gpu_count >= gpu_min)
    if gpu_memory_min:
        joins.add(ServerPrice.server)
        conditions.add(Server.gpu_memory_min >= gpu_memory_min * 1024)
    if gpu_memory_total:
        joins.add(ServerPrice.server)
        conditions.add(Server.gpu_memory_total >= gpu_memory_total * 1024)
    if only_active:
        joins.add(ServerPrice.server)
        conditions.add(Server.status == Status.ACTIVE)
    if green_energy:
        joins.add(ServerPrice.region)
        conditions.add(Region.green_energy == green_energy)
    if allocation:
        conditions.add(ServerPrice.allocation == allocation)
    if storage_type:
        joins.add(ServerPrice.server)
        conditions.add(Server.storage_type.in_(storage_type))
    if vendor:
        conditions.add(ServerPrice.vendor_id.in_(vendor))
    if regions:
        conditions.add(ServerPrice.region_id.in_(regions))
    if countries:
        joins.add(ServerPrice.region)
        conditions.add(Region.country_id.in_(countries))

    # count all records to be returned in header
    if add_total_count_header:
        query = select(func.count()).select_from(ServerPrice)
        for j in joins:
            query = query.join(j)
        if benchmark_score_stressng_cpu_min:
            query = query.join(
                max_scores,
                (ServerPrice.vendor_id == max_scores.c.vendor_id)
                & (ServerPrice.server_id == max_scores.c.server_id),
                isouter=True,
            )
        for condition in conditions:
            query = query.where(condition)
        response.headers["X-Total-Count"] = str(db.exec(query).one())

    # actual query
    query = select(ServerPrice, max_scores.c.score)
    joins.update(
        [
            ServerPrice.vendor,
            ServerPrice.region,
            ServerPrice.zone,
            ServerPrice.server,
        ]
    )

    for j in joins:
        query = query.join(j)
    query = query.join(
        max_scores,
        (ServerPrice.vendor_id == max_scores.c.vendor_id)
        & (ServerPrice.server_id == max_scores.c.server_id),
        isouter=True,
    )
    region_alias = Region
    query = query.join(region_alias.country)
    # avoid n+1 queries
    query = query.options(contains_eager(ServerPrice.vendor))
    query = query.options(
        contains_eager(ServerPrice.region).contains_eager(region_alias.country)
    )
    query = query.options(contains_eager(ServerPrice.zone))
    query = query.options(contains_eager(ServerPrice.server))
    for condition in conditions:
        query = query.where(condition)

    # ordering
    if order_by:
        order_obj = [
            o
            for o in [ServerPrice, Server, Region, max_scores.c]
            if hasattr(o, order_by)
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

    # pagination
    if limit > 0:
        query = query.limit(limit)
    # only apply if limit is set
    if page and limit > 0:
        query = query.offset((page - 1) * limit)

    results = db.exec(query).all()

    # unpack score
    prices = []
    for result in results:
        price = ServerPriceWithPKs.from_orm(result[0])
        price.server.score = result[1]
        try:
            price.server.price = min_server_price(
                db, price.server.vendor_id, price.server.server_id
            )
        except KeyError:
            price.server.price = None
        price.server.score_per_price = (
            price.server.score / price.server.price
            if price.server.price and price.server.score
            else None
        )

        prices.append(price)

    # update prices to currency requested
    for price in prices:
        if currency:
            if hasattr(price, "price") and hasattr(price, "currency"):
                if price.currency != currency:
                    price.price = round(
                        currency_converter.convert(
                            price.price, price.currency, currency
                        ),
                        4,
                    )
                    price.currency = currency
    return prices


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
