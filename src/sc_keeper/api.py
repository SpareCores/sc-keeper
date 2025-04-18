from contextlib import asynccontextmanager, suppress
from importlib.metadata import version
from json import loads as json_loads
from os import environ
from textwrap import dedent
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sc_crawler.table_fields import Status, TrafficDirection
from sc_crawler.tables import (
    Benchmark,
    BenchmarkScore,
    ComplianceFramework,
    Country,
    Region,
    Server,
    ServerPrice,
    Storage,
    StoragePrice,
    TrafficPrice,
    Vendor,
    VendorComplianceLink,
    Zone,
)
from sqlalchemy.orm import aliased, contains_eager
from sqlmodel import Session, String, case, func, or_, select

from . import parameters as options
from . import routers
from .cache import CacheHeaderMiddleware
from .currency import currency_converter
from .database import get_db
from .logger import LogMiddleware
from .queries import gen_benchmark_query
from .references import (
    BenchmarkConfig,
    OrderDir,
    RegionPKs,
    ServerPKs,
    ServerPriceWithPKs,
    StoragePriceWithPKs,
    TrafficPriceWithPKsWithMonthlyTraffic,
)
from .sentry import before_send as sentry_before_send
from .views import ServerExtra

if environ.get("SENTRY_DSN"):
    import sentry_sdk

    sentry_sdk.init(
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        before_send=sentry_before_send,
    )


db = next(get_db())


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
ServerPKs.model_config["json_schema_extra"]["examples"][0]["min_price"] = 7
ServerPKs.model_config["json_schema_extra"]["examples"][0]["min_price_spot"] = 7
ServerPKs.model_config["json_schema_extra"]["examples"][0]["min_price_ondemand"] = 10
ServerPKs.model_config["json_schema_extra"]["examples"][0]["score_per_price"] = 42 / 7

Storage.model_config["json_schema_extra"] = {
    "examples": [example_data["storage"].model_dump()]
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

# ##############################################################################
# Middlewares

# logging
app.add_middleware(LogMiddleware)

# CORS: allows all origins, without spec headers and without auth
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["sentry-trace", "baggage", "x-application-id"],
    expose_headers=["X-Total-Count"],
)

# aggressive compression
app.add_middleware(GZipMiddleware, minimum_size=100)

# set cache control header
app.add_middleware(CacheHeaderMiddleware)

# ##############################################################################
# API endpoints


app.include_router(routers.administrative.router, tags=["Administrative endpoints"])
app.include_router(routers.tables.router, prefix="/table", tags=["Table dumps"])
app.include_router(routers.table_metadata.router)
app.include_router(routers.server.router, tags=["Server Details"])
app.include_router(routers.ai.router, prefix="/ai", tags=["AI"])


@app.get("/regions", tags=["Query Resources"])
def search_regions(
    vendor: options.vendor = None,
    db: Session = Depends(get_db),
) -> List[RegionPKs]:
    query = select(Region)
    if vendor:
        query = query.where(Region.vendor_id.in_(vendor))
    return db.exec(query).all()


@app.get("/servers", tags=["Query Resources"])
def search_servers(
    response: Response,
    partial_name_or_id: options.partial_name_or_id = None,
    vcpus_min: options.vcpus_min = 1,
    vcpus_max: options.vcpus_max = None,
    architecture: options.architecture = None,
    cpu_manufacturer: options.cpu_manufacturer = None,
    cpu_family: options.cpu_family = None,
    cpu_allocation: options.cpu_allocation = None,
    benchmark_score_stressng_cpu_min: options.benchmark_score_stressng_cpu_min = None,
    benchmark_score_per_price_stressng_cpu_min: options.benchmark_score_per_price_stressng_cpu_min = None,
    benchmark_id: options.benchmark_id = None,
    benchmark_config: options.benchmark_config = None,
    benchmark_score_min: options.benchmark_score_min = None,
    benchmark_score_per_price_min: options.benchmark_score_per_price_min = None,
    memory_min: options.memory_min = None,
    only_active: options.only_active = True,
    vendor: options.vendor = None,
    compliance_framework: options.compliance_framework = None,
    storage_size: options.storage_size = None,
    storage_type: options.storage_type = None,
    gpu_min: options.gpu_min = None,
    gpu_memory_min: options.gpu_memory_min = None,
    gpu_memory_total: options.gpu_memory_total = None,
    gpu_manufacturer: options.gpu_manufacturer = None,
    gpu_family: options.gpu_family = None,
    gpu_model: options.gpu_model = None,
    limit: options.limit = 25,
    page: options.page = None,
    order_by: options.order_by = "min_price",
    order_dir: options.order_dir = OrderDir.ASC,
    add_total_count_header: options.add_total_count_header = False,
    db: Session = Depends(get_db),
) -> List[ServerPKs]:
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

    # extra lookups
    benchmark_query = gen_benchmark_query(benchmark_id, benchmark_config)
    if (benchmark_score_min or benchmark_score_per_price_min) and not benchmark_id:
        raise HTTPException(
            status_code=400,
            detail="benchmark_id is required when filtering by benchmark_score or benchmark_score_per_price",
        )
    if (
        order_by in ["selected_benchmark_score", "selected_benchmark_score_per_price"]
    ) and not benchmark_id:
        raise HTTPException(
            status_code=400,
            detail="benchmark_id is required when ordering by benchmark_score or benchmark_score_per_price",
        )

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
    if vcpus_max:
        conditions.add(Server.vcpus <= vcpus_max)
    if architecture:
        conditions.add(Server.cpu_architecture.in_(architecture))
    if cpu_manufacturer:
        conditions.add(Server.cpu_manufacturer.in_(cpu_manufacturer))
    if cpu_family:
        conditions.add(Server.cpu_family.in_(cpu_family))
    if cpu_allocation:
        conditions.add(Server.cpu_allocation.in_(cpu_allocation))
    if benchmark_score_stressng_cpu_min:
        conditions.add(ServerExtra.score > benchmark_score_stressng_cpu_min)
    if benchmark_score_per_price_stressng_cpu_min:
        conditions.add(
            ServerExtra.score_per_price > benchmark_score_per_price_stressng_cpu_min
        )
    if benchmark_score_min:
        conditions.add(benchmark_query.c.benchmark_score >= benchmark_score_min)
    if benchmark_score_per_price_min:
        conditions.add(
            benchmark_query.c.benchmark_score / ServerExtra.min_price
            >= benchmark_score_per_price_min
        )
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
    if gpu_manufacturer:
        conditions.add(Server.gpu_manufacturer.in_(gpu_manufacturer))
    if gpu_family:
        conditions.add(Server.gpu_family.in_(gpu_family))
    if gpu_model:
        conditions.add(Server.gpu_model.in_(gpu_model))
    if only_active:
        conditions.add(Server.status == Status.ACTIVE)
    if storage_type:
        conditions.add(Server.storage_type.in_(storage_type))
    if vendor:
        conditions.add(Server.vendor_id.in_(vendor))

    # hide servers without value when filtering by the related column
    if order_by == "score_per_price":
        conditions.add(ServerExtra.score.isnot(None))
    if order_by == "score_per_price":
        conditions.add(ServerExtra.score_per_price.isnot(None))
    if order_by == "min_price":
        conditions.add(ServerExtra.min_price.isnot(None))
    if order_by == "min_price_ondemand":
        conditions.add(ServerExtra.min_price_ondemand.isnot(None))
    if order_by == "min_price_spot":
        conditions.add(ServerExtra.min_price_spot.isnot(None))
    if order_by == "selected_benchmark_score":
        conditions.add(benchmark_query.c.benchmark_score.isnot(None))
    if order_by == "selected_benchmark_score_per_price":
        conditions.add(benchmark_query.c.benchmark_score.isnot(None))
        conditions.add(ServerExtra.min_price.isnot(None))

    # count all records to be returned in header
    if add_total_count_header:
        query = select(func.count()).select_from(Server)
        if (
            only_active
            or benchmark_score_stressng_cpu_min
            or benchmark_score_per_price_stressng_cpu_min
            or benchmark_score_per_price_min
        ):
            query = query.join(
                ServerExtra,
                (Server.vendor_id == ServerExtra.vendor_id)
                & (Server.server_id == ServerExtra.server_id),
                isouter=True,
            )
        if (benchmark_score_min or benchmark_score_per_price_min) or (
            order_by
            in ["selected_benchmark_score", "selected_benchmark_score_per_price"]
        ):
            query = query.join(
                benchmark_query,
                (Server.vendor_id == benchmark_query.c.vendor_id)
                & (Server.server_id == benchmark_query.c.server_id),
                isouter=True,
            )
        for condition in conditions:
            query = query.where(condition)
        response.headers["X-Total-Count"] = str(db.exec(query).one())

    # actual query
    if benchmark_id:
        query = select(Server, ServerExtra, benchmark_query.c.benchmark_score)
    else:
        query = select(Server, ServerExtra)
    query = query.join(Server.vendor)
    query = query.join(
        ServerExtra,
        (Server.vendor_id == ServerExtra.vendor_id)
        & (Server.server_id == ServerExtra.server_id),
        isouter=True,
    )
    if benchmark_id:
        query = query.join(
            benchmark_query,
            (Server.vendor_id == benchmark_query.c.vendor_id)
            & (Server.server_id == benchmark_query.c.server_id),
            isouter=True,
        )
    query = query.options(contains_eager(Server.vendor))
    for condition in conditions:
        query = query.where(condition)

    # ordering
    if order_by:
        if order_by == "selected_benchmark_score":
            order_field = benchmark_query.c.benchmark_score
        elif order_by == "selected_benchmark_score_per_price":
            order_field = benchmark_query.c.benchmark_score / ServerExtra.min_price
        else:
            order_obj = [o for o in [Server, ServerExtra] if hasattr(o, order_by)]
            if len(order_obj) == 0:
                raise HTTPException(status_code=400, detail="Unknown order_by field.")
            if len(order_obj) > 1:
                raise HTTPException(status_code=400, detail="Ambiguous order_by field.")
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
    for server_items in servers:
        if benchmark_id:
            server_data, server_extra, benchmark_score = server_items
        else:
            server_data, server_extra = server_items
            benchmark_score = None
        server = ServerPKs.model_validate(server_data)
        with suppress(Exception):
            server.score = server_extra.score
            server.min_price = server_extra.min_price
            server.min_price_spot = server_extra.min_price_spot
            server.min_price_ondemand = server_extra.min_price_ondemand
            server.score_per_price = server_extra.score_per_price
            server.price = server.min_price  # legacy
            server.selected_benchmark_score = benchmark_score
            server.selected_benchmark_score_per_price = (
                benchmark_score / server.min_price
            )
        serverlist.append(server)

    return serverlist


@app.get("/server_prices", tags=["Query Resources"])
def search_server_prices(
    response: Response,
    partial_name_or_id: options.partial_name_or_id = None,
    # although it's relatively expensive to set a dummy filter,
    # but this is needed not to mess on the frontend (slider without value)
    vcpus_min: options.vcpus_min = 1,
    vcpus_max: options.vcpus_max = None,
    architecture: options.architecture = None,
    cpu_manufacturer: options.cpu_manufacturer = None,
    cpu_family: options.cpu_family = None,
    cpu_allocation: options.cpu_allocation = None,
    benchmark_score_stressng_cpu_min: options.benchmark_score_stressng_cpu_min = None,
    benchmark_score_per_price_stressng_cpu_min: options.benchmark_score_per_price_stressng_cpu_min = None,
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
    gpu_manufacturer: options.gpu_manufacturer = None,
    gpu_family: options.gpu_family = None,
    gpu_model: options.gpu_model = None,
    limit: options.limit250 = 25,
    page: options.page = None,
    order_by: options.order_by = "price",
    order_dir: options.order_dir = OrderDir.ASC,
    currency: options.currency = "USD",
    add_total_count_header: options.add_total_count_header = False,
    db: Session = Depends(get_db),
) -> List[ServerPriceWithPKs]:
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
    if vcpus_max:
        joins.add(ServerPrice.server)
        conditions.add(Server.vcpus <= vcpus_max)
    if architecture:
        joins.add(ServerPrice.server)
        conditions.add(Server.cpu_architecture.in_(architecture))
    if cpu_manufacturer:
        joins.add(ServerPrice.server)
        conditions.add(Server.cpu_manufacturer.in_(cpu_manufacturer))
    if cpu_family:
        joins.add(ServerPrice.server)
        conditions.add(Server.cpu_family.in_(cpu_family))
    if cpu_allocation:
        joins.add(ServerPrice.server)
        conditions.add(Server.cpu_allocation.in_(cpu_allocation))
    if benchmark_score_stressng_cpu_min:
        conditions.add(ServerExtra.score > benchmark_score_stressng_cpu_min)
    if benchmark_score_per_price_stressng_cpu_min:
        # needs special handling in filtering
        pass
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
    if gpu_manufacturer:
        conditions.add(Server.gpu_manufacturer.in_(gpu_manufacturer))
    if gpu_family:
        conditions.add(Server.gpu_family.in_(gpu_family))
    if gpu_model:
        conditions.add(Server.gpu_model.in_(gpu_model))
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
        if (
            benchmark_score_stressng_cpu_min
            or benchmark_score_per_price_stressng_cpu_min
        ):
            query = query.join(
                ServerExtra,
                (Server.vendor_id == ServerExtra.vendor_id)
                & (Server.server_id == ServerExtra.server_id),
                isouter=True,
            )
        for condition in conditions:
            query = query.where(condition)
        if benchmark_score_per_price_stressng_cpu_min:
            query = query.where(ServerExtra.score.isnot(None)).where(
                ServerExtra.score / ServerPrice.price
                > benchmark_score_per_price_stressng_cpu_min
            )
        response.headers["X-Total-Count"] = str(db.exec(query).one())

    # actual query
    query = select(ServerPrice, ServerExtra)

    for j in joins:
        query = query.join(j)
    query = query.join(
        ServerExtra,
        (Server.vendor_id == ServerExtra.vendor_id)
        & (Server.server_id == ServerExtra.server_id),
        isouter=True,
    )

    for condition in conditions:
        query = query.where(condition)
    if benchmark_score_per_price_stressng_cpu_min:
        query = query.where(ServerExtra.score.isnot(None)).where(
            ServerExtra.score / ServerPrice.price
            > benchmark_score_per_price_stressng_cpu_min
        )

    # ordering
    if order_by:
        # special handling for price_per_score as not being a table column
        if order_by == "score_per_price":
            if OrderDir(order_dir) == OrderDir.ASC:
                query = query.order_by(ServerExtra.score / ServerPrice.price)
            else:
                query = query.order_by(ServerExtra.score / ServerPrice.price * -1)
        else:
            order_obj = [
                o
                for o in [ServerPrice, Server, Region, ServerExtra]
                if hasattr(o, order_by)
            ]
            if len(order_obj) == 0:
                raise HTTPException(status_code=400, detail="Unknown order_by field.")
            if len(order_obj) > 1:
                raise HTTPException(status_code=400, detail="Ambiguous order_by field.")
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

    # load extra objects/columns _after_ the subquery filtering
    subquery = query.subquery()
    subquery_aliased = aliased(ServerPrice, subquery.alias("filtered_server_price"))
    query = select(
        subquery_aliased,
        ServerExtra,
        case(
            (
                subquery_aliased.price.isnot(None),
                ServerExtra.score / subquery_aliased.price,
            ),
            else_=None,
        ).label("price_per_score"),
    )
    joins = [
        subquery_aliased.vendor,
        subquery_aliased.region,
        subquery_aliased.zone,
        subquery_aliased.server,
    ]
    for j in joins:
        query = query.join(j, isouter=True)
    query = query.join(
        ServerExtra,
        (subquery_aliased.vendor_id == ServerExtra.vendor_id)
        & (subquery_aliased.server_id == ServerExtra.server_id),
        isouter=True,
    )
    region_alias = Region
    query = query.join(region_alias.country)
    # avoid n+1 queries
    query = query.options(contains_eager(subquery_aliased.vendor))
    query = query.options(
        contains_eager(subquery_aliased.region).contains_eager(region_alias.country)
    )
    query = query.options(contains_eager(subquery_aliased.zone))
    query = query.options(contains_eager(subquery_aliased.server))
    # reorder again after the above joins
    if order_by:
        if order_by == "score_per_price":
            if OrderDir(order_dir) == OrderDir.ASC:
                query = query.order_by(ServerExtra.score / subquery_aliased.price)
            else:
                query = query.order_by(ServerExtra.score / subquery_aliased.price * -1)
        else:
            order_obj = [
                o
                for o in [subquery_aliased, Server, Region, ServerExtra]
                if hasattr(o, order_by)
            ]
            order_field = getattr(order_obj[0], order_by)
            if OrderDir(order_dir) == OrderDir.ASC:
                query = query.order_by(order_field)
            else:
                query = query.order_by(order_field.desc())

    results = db.exec(query).all()

    # unpack score
    prices = []
    for result in results:
        price = ServerPriceWithPKs.model_validate(result[0])
        with suppress(Exception):
            price.server.score = result[1].score
            price.server.min_price = result[1].min_price
            price.server.min_price_spot = result[1].min_price_spot
            price.server.min_price_ondemand = result[1].min_price_ondemand
            # note this is not the server's but the server price's score_per_price
            price.server.score_per_price = round(result[2], 6)
            price.server.price = price.server.min_price  # legacy
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


@app.get("/storage_prices", tags=["Query Resources"])
def search_storage_prices(
    response: Response,
    vendor: options.vendor = None,
    green_energy: options.green_energy = None,
    storage_min: options.storage_size = None,
    storage_type: options.storage_type = None,
    compliance_framework: options.compliance_framework = None,
    regions: options.regions = None,
    countries: options.countries = None,
    limit: options.limit = 10,
    page: options.page = None,
    order_by: options.order_by = "price",
    order_dir: options.order_dir = OrderDir.ASC,
    currency: options.currency = "USD",
    add_total_count_header: options.add_total_count_header = False,
    db: Session = Depends(get_db),
) -> List[StoragePriceWithPKs]:
    # compliance frameworks are defined at the vendor level,
    # let's filter for vendors instead of exploding the storages table
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

    # always filter for ACTIVE prices
    conditions.add(StoragePrice.status == Status.ACTIVE)

    if vendor:
        conditions.add(StoragePrice.vendor_id.in_(vendor))

    if storage_type:
        joins.add(StoragePrice.storage)
        conditions.add(Storage.storage_type.in_(storage_type))

    if storage_min:
        joins.add(StoragePrice.storage)
        conditions.add(Storage.min_size <= storage_min)
        conditions.add(Storage.max_size >= storage_min)

    if regions:
        conditions.add(StoragePrice.region_id.in_(regions))

    if countries:
        joins.add(StoragePrice.region)
        conditions.add(Region.country_id.in_(countries))

    if green_energy:
        joins.add(StoragePrice.region)
        conditions.add(Region.green_energy == green_energy)

    # count all records to be returned in header
    if add_total_count_header:
        query = select(func.count()).select_from(StoragePrice)
        for j in joins:
            query = query.join(j)
        for condition in conditions:
            query = query.where(condition)
        response.headers["X-Total-Count"] = str(db.exec(query).one())

    region_alias = Region
    query = (
        select(StoragePrice)
        .join(StoragePrice.vendor)
        .options(contains_eager(StoragePrice.vendor))
        .join(StoragePrice.region)
        .join(region_alias.country)
        .options(
            contains_eager(StoragePrice.region).contains_eager(region_alias.country)
        )
        .join(StoragePrice.storage)
        .options(contains_eager(StoragePrice.storage))
    )
    for condition in conditions:
        query = query.where(condition)

    # ordering
    if order_by:
        order_obj = [o for o in [StoragePrice, Region, Storage] if hasattr(o, order_by)]
        if len(order_obj) == 0:
            raise HTTPException(status_code=400, detail="Unknown order_by field.")
        if len(order_obj) > 1:
            raise HTTPException(status_code=400, detail="Ambiguous order_by field.")
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

    prices = db.exec(query).all()

    # update prices to currency requested
    for price in prices:
        if currency:
            if hasattr(price, "price") and hasattr(price, "currency"):
                if price.currency != currency:
                    price.price = round(
                        currency_converter.convert(
                            price.price, price.currency, currency
                        ),
                        6,
                    )
                    price.currency = currency

    return prices


@app.get("/traffic_prices", tags=["Query Resources"])
def search_traffic_prices(
    response: Response,
    vendor: options.vendor = None,
    green_energy: options.green_energy = None,
    compliance_framework: options.compliance_framework = None,
    regions: options.regions = None,
    countries: options.countries = None,
    direction: options.direction = [TrafficDirection.OUT],
    monthly_traffic: options.monthly_traffic = 1,
    limit: options.limit = 10,
    page: options.page = None,
    order_by: options.order_by = "price",
    order_dir: options.order_dir = OrderDir.ASC,
    currency: options.currency = "USD",
    add_total_count_header: options.add_total_count_header = False,
    db: Session = Depends(get_db),
) -> List[TrafficPriceWithPKsWithMonthlyTraffic]:
    # compliance frameworks are defined at the vendor level,
    # let's filter for vendors instead of exploding the storages table
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

    # always filter for ACTIVE prices
    conditions.add(TrafficPrice.status == Status.ACTIVE)

    if vendor:
        conditions.add(TrafficPrice.vendor_id.in_(vendor))

    if regions:
        conditions.add(TrafficPrice.region_id.in_(regions))

    if countries:
        joins.add(TrafficPrice.region)
        conditions.add(Region.country_id.in_(countries))

    if green_energy:
        joins.add(TrafficPrice.region)
        conditions.add(Region.green_energy == green_energy)

    if direction:
        conditions.add(TrafficPrice.direction.in_(direction))

    # count all records to be returned in header
    if add_total_count_header:
        query = select(func.count()).select_from(TrafficPrice)
        for j in joins:
            query = query.join(j)
        for condition in conditions:
            query = query.where(condition)
        response.headers["X-Total-Count"] = str(db.exec(query).one())

    region_alias = Region
    query = (
        select(TrafficPrice)
        .join(TrafficPrice.vendor)
        .options(contains_eager(TrafficPrice.vendor))
        .join(TrafficPrice.region)
        .join(region_alias.country)
        .options(
            contains_eager(TrafficPrice.region).contains_eager(region_alias.country)
        )
    )
    for condition in conditions:
        query = query.where(condition)

    # ordering
    if order_by:
        order_obj = [o for o in [TrafficPrice, Region] if hasattr(o, order_by)]
        if len(order_obj) == 0:
            raise HTTPException(status_code=400, detail="Unknown order_by field.")
        if len(order_obj) > 1:
            raise HTTPException(status_code=400, detail="Ambiguous order_by field.")
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

    prices = db.exec(query).all()

    # update model to include the monthly traffic price field
    for i, p in enumerate(prices):
        prices[i] = TrafficPriceWithPKsWithMonthlyTraffic.model_validate(p)

    # update prices per tiers and to currency requested
    for price in prices:

        def rounder(p):
            return round(p, 6)

        def local_price(p):
            return rounder(currency_converter.convert(p, price.currency, currency))

        if currency:
            if hasattr(price, "price") and hasattr(price, "currency"):
                if price.currency != currency:
                    price.price = local_price(price.price)
                    for i, tier in enumerate(price.price_tiered):
                        price.price_tiered[i].price = local_price(tier.price)
                    price.currency = currency

        if price.price_tiered:
            price.price_monthly_traffic = traffic_paid = 0
            for i, tier in enumerate(price.price_tiered):
                traffic_tier = min(
                    max(monthly_traffic - traffic_paid, 0),
                    (float(tier.upper) - float(tier.lower)),
                )
                price.price_monthly_traffic += rounder(tier.price * traffic_tier)
                traffic_paid += traffic_tier
        else:
            price.price_monthly_traffic = rounder(price.price * monthly_traffic)

    return prices


@app.get("/benchmark_configs", tags=["Query Resources"])
def search_benchmark_configs(
    db: Session = Depends(get_db),
) -> List[BenchmarkConfig]:
    query = (
        select(BenchmarkScore.benchmark_id, func.cast(BenchmarkScore.config, String))
        .distinct()
        .where(BenchmarkScore.status == Status.ACTIVE)
        .order_by(
            BenchmarkScore.benchmark_id,
            func.json_extract(BenchmarkScore.config, "$.key1"),
        )
    )
    results = db.exec(query).all()
    for i, result in enumerate(results):
        result = result._asdict()
        # store parsed config
        result["config_parsed"] = json_loads(result["config"])

        if result["benchmark_id"] == "bogomips":
            result["category"] = "Other"
        if result["benchmark_id"] == "bw_mem":
            result["category"] = "Memory bandwidth"
        if result["benchmark_id"] == "openssl":
            result["category"] = "OpenSSL"
        if result["benchmark_id"].startswith("compression_text"):
            result["category"] = "Compression algos"
        if result["benchmark_id"].startswith("geekbench"):
            result["category"] = "Geekbench"
        if result["benchmark_id"].startswith("passmark"):
            result["category"] = "Passmark"
        if result["benchmark_id"].startswith("static_web"):
            result["category"] = "Static web server"
        if result["benchmark_id"].startswith("redis"):
            result["category"] = "Redis"
        if result["benchmark_id"].startswith("stress_ng:best"):
            result["category"] = "stress-ng"
        if result["benchmark_id"].startswith("llm_speed"):
            result["category"] = "LLM inference speed"
        # keep original order
        result["original_order"] = i
        results[i] = result
    results = [result for result in results if result.get("category")]

    category_order = [
        "stress-ng",
        "Geekbench",
        "Passmark",
        "Memory bandwidth",
        "OpenSSL",
        "Compression algos",
        "Static web server",
        "Redis",
        "LLM inference speed",
        "Other",
    ]
    sub_category_order = [
        "geekbench:score",
        "passmark:cpu_mark",
        "passmark:memory_mark",
        "llm_speed:prompt_processing",
        "llm_speed:text_generation",
    ]
    model_order = [
        "SmolLM-135M.Q4_K_M.gguf",
        "qwen1_5-0_5b-chat-q4_k_m.gguf",
        "gemma-2b.Q4_K_M.gguf",
        "llama-7b.Q4_K_M.gguf",
        "phi-4-q4.gguf",
        "Llama-3.3-70B-Instruct-Q4_K_M.gguf",
    ]

    def get_sort_key(item):
        """Helper function to determine the sort order for benchmark configs"""
        config = item["config_parsed"]

        # primary sort by category
        category_idx = category_order.index(item["category"])

        # secondary sort by benchmark_id
        if item["benchmark_id"] in sub_category_order:
            subcategory_idx = sub_category_order.index(item["benchmark_id"])
        else:
            subcategory_idx = len(sub_category_order)

        # then sort by cores (single-core first)
        cores_idx = 0 if config.get("cores", "") == "Single-Core Performance" else 1

        # then sort by LLM model (if present)
        model_idx = len(model_order)
        if "model" in config and config["model"] in model_order:
            model_idx = model_order.index(config["model"])

        # then sort by tokens (if present)
        tokens = 0
        if "tokens" in config:
            try:
                tokens = int(config["tokens"])
            except (ValueError, TypeError):
                pass

        # finally, sort by original order
        return (
            category_idx,
            subcategory_idx,
            cores_idx,
            model_idx,
            tokens,
            item["original_order"],
        )

    return sorted(results, key=get_sort_key)
