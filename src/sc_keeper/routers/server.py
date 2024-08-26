from typing import Annotated, List, Literal

from fastapi import (
    APIRouter,
    Depends,
    Path,
)
from sc_crawler.table_bases import ServerBase
from sc_crawler.table_fields import Status
from sc_crawler.tables import (
    BenchmarkScore,
    Server,
    ServerPrice,
)
from sqlalchemy.orm import contains_eager
from sqlmodel import Session, and_, func, not_, select

from .. import parameters as options
from ..database import get_db
from ..helpers import (
    currency_converter,
    get_server_base,
    get_server_dict,
    get_server_pks,
)
from ..lookups import min_server_price
from ..query import max_score_per_server
from ..references import ServerPKs, ServerPKsWithPrices

router = APIRouter()


@router.get("/server/{vendor}/{server}", deprecated=True)
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
    res = get_server_pks(vendor, server, db)
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
    res = ServerPKsWithPrices.model_validate(res)
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


@router.get("/v2/server/{vendor}/{server}")
def get_server_without_relations(server_args: options.server_args) -> ServerBase:
    """Query a single server by its vendor id and either the server id or its API reference."""
    vendor_id, server_id = server_args
    return get_server_dict(vendor_id, server_id)


@router.get("/server/{vendor}/{server}/similar_servers/{by}/{n}")
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
    """
    serverobj = get_server_pks(vendor, server, db)

    max_scores = max_score_per_server(benchmark_id, benchmark_config)
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
        serveri = ServerPKs.model_validate(server[0])
        serveri.score = server[1]
        try:
            serveri.price = min_server_price(db, serveri.vendor_id, serveri.server_id)
            serveri.score_per_price = serveri.score / serveri.price
        except Exception:
            serveri.score_per_price = None
        serverlist.append(serveri)

    return serverlist


@router.get("/server/{vendor}/{server}/prices")
def get_server_prices(
    server_args: options.server_args,
    db: Session = Depends(get_db),
) -> List[ServerPrice]:
    """Query the current prices of a single server by its vendor id and server id."""
    vendor_id, server_id = server_args
    prices = db.exec(
        select(ServerPrice)
        .where(ServerPrice.status == Status.ACTIVE)
        .where(ServerPrice.vendor_id == vendor_id)
        .where(ServerPrice.server_id == server_id)
    ).all()
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
