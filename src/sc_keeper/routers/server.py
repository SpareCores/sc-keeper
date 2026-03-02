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
    Server,
    ServerPrice,
)
from sqlmodel import Session, and_, case, func, not_, select

from sc_keeper.views import ServerExtra

from .. import parameters as options
from ..currency import currency_converter
from ..database import get_db
from ..helpers import get_server_dict, get_server_pks
from ..queries import gen_live_price_query
from ..references import ServerPKs

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
    countries: options.countries = None,
    regions: options.regions = None,
    benchmark_id: options.benchmark_id = "stress_ng:cpu_all",
    benchmark_config: options.benchmark_id = "",
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
    serverobj = get_server_pks(vendor, server, db)

    user = getattr(request.state, "user", None)
    if not user:
        if len(regions or []) > 3:
            raise HTTPException(
                status_code=400,
                detail="Max 3 regions can be queried at a time without authentication.",
            )
        if len(countries or []) > 1:
            raise HTTPException(
                status_code=400,
                detail="Max 1 country can be queried at a time without authentication.",
            )

    live_price_query = gen_live_price_query(countries, regions)

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
        if live_price_query is None:
            target_score_per_price = db.exec(
                select(ServerExtra.score_per_price)
                .where(ServerExtra.vendor_id == serverobj.vendor_id)
                .where(ServerExtra.server_id == serverobj.server_id)
            ).first()
        else:
            target_score_per_price = db.exec(
                select(
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
                )
                .where(ServerExtra.vendor_id == serverobj.vendor_id)
                .where(ServerExtra.server_id == serverobj.server_id)
            ).first()
        if target_score_per_price is None:
            return []
        query = query.where(ServerExtra.score_per_price.isnot(None)).order_by(
            func.abs(ServerExtra.score_per_price - target_score_per_price)
        )

    servers = db.exec(query.limit(num)).all()

    serverlist = []
    for server in servers:
        serveri = ServerPKs.model_validate(server[0])
        with suppress(Exception):
            serveri.score = server[1].score
            serveri.score_per_price = server[1].score_per_price
        with suppress(Exception):
            if live_price_query is not None:
                serveri.min_price = server[2]
                serveri.min_price_spot = server[3]
                serveri.min_price_ondemand = server[4]
                serveri.min_price_ondemand_monthly = server[5]
            else:
                serveri.min_price = server[1].min_price
                serveri.min_price_spot = server[1].min_price_spot
                serveri.min_price_ondemand = server[1].min_price_ondemand
            serveri.price = serveri.min_price  # legacy
        serverlist.append(serveri)

    return serverlist


@router.get("/server/{vendor}/{server}/prices")
def get_server_prices(
    server_args: options.server_args,
    db: Session = Depends(get_db),
    currency: options.currency = None,
) -> List[ServerPrice]:
    """Query the current prices of a single server by its vendor id and server id."""
    vendor_id, server_id = server_args
    prices = db.exec(
        select(ServerPrice)
        .where(ServerPrice.status == Status.ACTIVE)
        .where(ServerPrice.vendor_id == vendor_id)
        .where(ServerPrice.server_id == server_id)
    ).all()
    if currency:
        for price in prices:
            if hasattr(price, "price") and hasattr(price, "currency"):
                if price.currency != currency:
                    db.expunge(price)
                    try:
                        price.price = round(
                            currency_converter.convert(
                                price.price, price.currency, currency
                            ),
                            4,
                        )
                    except ValueError as e:
                        raise HTTPException(
                            status_code=400, detail="Invalid currency code"
                        ) from e
                    price.currency = currency
    return prices


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
