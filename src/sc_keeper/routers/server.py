from contextlib import suppress
from typing import Annotated, List, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Request,
)
from sc_crawler.table_bases import ServerBase
from sc_crawler.table_fields import Status
from sc_crawler.tables import (
    BenchmarkScore,
    Region,
    Server,
    ServerPrice,
)
from sqlalchemy.orm import contains_eager
from sqlmodel import Session, and_, case, func, not_, select

from sc_keeper.views import ServerExtra

from .. import parameters as options
from ..auth import check_filter_limits
from ..currency import currency_converter
from ..database import get_db
from ..helpers import (
    get_server_dict,
    get_server_pks,
    update_server_price_currency,
    vendor_region_filter,
)
from ..queries import gen_live_price_query
from ..references import ServerPKs, ServerPriceWithPKs

router = APIRouter()


@router.get("/v2/server/{vendor}/{server}")
def get_server_without_relations(server_args: options.server_args) -> ServerBase:
    """Query a single server by its vendor id and either the server id or its API reference."""
    vendor_id, server_id = server_args
    return get_server_dict(vendor_id, server_id)


@router.get("/server/{vendor}/{server}/similar_servers/{by}/{num}")
def get_similar_servers(
    request: Request,
    vendor: Annotated[str, Path(description="Vendor ID.")],
    server: Annotated[str, Path(description="Server ID or API reference.")],
    by: Annotated[
        Literal["family", "specs", "score", "score_per_price"],
        Path(description="Algorithm to look for similar servers."),
    ],
    num: Annotated[
        int,
        Path(description="Number of servers to get.", le=100),
    ],
    server_region: options.server_region = None,
    countries: options.countries = None,
    vendor_regions: options.vendor_regions = None,
    benchmark_id: options.benchmark_id = None,
    benchmark_config: options.benchmark_config = None,
    currency: options.currency = "USD",
    db: Session = Depends(get_db),
) -> List[ServerPKs]:
    """Search similar servers to the provided one.

    The "family" method returns all servers from the same family of
    the same vendor.

    The "specs" approach will prioritize the number of
    GPUs, then CPUs, lastly the amount of memory.

    The "score" method will find the servers with the closest
    performance using the multi-core SCore.

    The "score_per_price" method is similar to "score", but
    instead of using the multi-core SCore, it uses the SCore
    per price.
    """
    check_filter_limits(request, countries, vendor_regions=vendor_regions)

    serverobj = get_server_pks(vendor, server, db)

    live_price_query = gen_live_price_query(
        countries=countries, vendor_regions=vendor_regions
    )

    if live_price_query is not None:
        query = select(
            Server,
            ServerExtra,
            live_price_query.c.min_price,
            live_price_query.c.min_price_spot,
            live_price_query.c.min_price_ondemand,
            live_price_query.c.min_price_ondemand_monthly,
        )
    else:
        query = select(Server, ServerExtra)

    query = query.join(
        ServerExtra,
        (Server.vendor_id == ServerExtra.vendor_id)
        & (Server.server_id == ServerExtra.server_id),
        isouter=True,
    )
    if live_price_query is not None:
        query = query.join(
            live_price_query,
            (Server.vendor_id == live_price_query.c.vendor_id)
            & (Server.server_id == live_price_query.c.server_id),
        )

    query = query.where(
        not_(
            and_(
                Server.vendor_id == serverobj.vendor_id,
                Server.server_id == serverobj.server_id,
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
        target_score = db.exec(
            select(ServerExtra.score)
            .where(ServerExtra.vendor_id == serverobj.vendor_id)
            .where(ServerExtra.server_id == serverobj.server_id)
        ).first()
        if target_score is None:
            return []
        query = query.where(ServerExtra.score.isnot(None)).order_by(
            func.abs(ServerExtra.score - target_score)
        )

    if by == "score_per_price":
        if server_region is None:
            raise HTTPException(
                status_code=400,
                detail="The server_region parameter is required when sorting by score_per_price.",
            )
        target_live_price_query = gen_live_price_query(
            vendor_regions=[f"{serverobj.vendor_id}~{server_region}"]
        )
        target_score_per_price = db.exec(
            select(
                func.round(ServerExtra.score / target_live_price_query.c.min_price, 4)
            )
            .select_from(ServerExtra)
            .join(
                target_live_price_query,
                (ServerExtra.vendor_id == target_live_price_query.c.vendor_id)
                & (ServerExtra.server_id == target_live_price_query.c.server_id),
            )
            .where(ServerExtra.vendor_id == serverobj.vendor_id)
            .where(ServerExtra.server_id == serverobj.server_id)
        ).first()
        if target_score_per_price is None:
            return []
        if live_price_query is not None:
            query = query.where(ServerExtra.score.isnot(None)).order_by(
                func.abs(
                    case(
                        (
                            (live_price_query.c.min_price.is_(None))
                            | (live_price_query.c.min_price == 0),
                            None,
                        ),
                        else_=func.round(
                            ServerExtra.score / live_price_query.c.min_price, 4
                        ),
                    )
                    - target_score_per_price
                )
            )
        else:
            query = query.where(ServerExtra.score_per_price.isnot(None)).order_by(
                func.abs(ServerExtra.score_per_price - target_score_per_price)
            )

    servers = db.exec(query.limit(num)).all()

    serverlist = []
    for server in servers:
        serveri = ServerPKs.model_validate(server[0])
        with suppress(Exception):
            serveri.score = server[1].score
            serveri.price = serveri.min_price  # legacy
            if live_price_query is not None:
                serveri.min_price = server[2]
                serveri.min_price_spot = server[3]
                serveri.min_price_ondemand = server[4]
                serveri.min_price_ondemand_monthly = server[5]
                serveri.score_per_price = round(serveri.score / serveri.min_price, 4)
            else:
                serveri.min_price = server[1].min_price
                serveri.min_price_spot = server[1].min_price_spot
                serveri.min_price_ondemand = server[1].min_price_ondemand
                serveri.min_price_ondemand_monthly = server[
                    1
                ].min_price_ondemand_monthly
                serveri.score_per_price = server[1].score_per_price
        serverlist.append(serveri)
    return serverlist


@router.get("/server/{vendor}/{server}/prices")
def get_server_prices(
    request: Request,
    server_args: options.server_args,
    countries: options.countries = None,
    vendor_regions: options.vendor_regions = None,
    currency: options.currency = "USD",
    db: Session = Depends(get_db),
) -> List[ServerPriceWithPKs]:
    """Query the current prices of a single server by its vendor id and server id."""
    vendor_id, server_id = server_args

    if currency and currency not in currency_converter.converter.currencies:
        raise HTTPException(status_code=400, detail="Invalid currency code")

    check_filter_limits(request, countries, vendor_regions=vendor_regions)

    query = (
        select(ServerPrice)
        .join(ServerPrice.region)
        .join(Region.country)
        .where(ServerPrice.status == Status.ACTIVE)
        .where(ServerPrice.vendor_id == vendor_id)
        .where(ServerPrice.server_id == server_id)
    )
    if countries:
        query = query.where(Region.country_id.in_(countries))
    if vendor_regions:
        query = query.where(vendor_region_filter(vendor_regions, ServerPrice))

    query = query.options(
        contains_eager(ServerPrice.region).contains_eager(Region.country)
    )

    results = db.exec(query).all()

    prices = []
    for result in results:
        if not result:
            continue
        price = ServerPriceWithPKs.model_validate(result)
        price.price_monthly = result.price_monthly
        prices.append(price)

    return update_server_price_currency(prices, currency)


@router.get("/server/{vendor}/{server}/benchmarks")
def get_server_benchmarks(
    server_args: options.server_args,
    db: Session = Depends(get_db),
) -> List[BenchmarkScore]:
    """Query the current benchmark scores of a single server."""
    vendor_id, server_id = server_args
    return db.exec(
        select(BenchmarkScore)
        .where(BenchmarkScore.status == Status.ACTIVE)
        .where(BenchmarkScore.vendor_id == vendor_id)
        .where(BenchmarkScore.server_id == server_id)
    ).all()
