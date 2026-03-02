from typing import Optional

from sc_crawler.tables import Allocation, BenchmarkScore, Region, ServerPrice, Status
from sqlmodel import String, case, func, select

from .helpers import vendor_region_filter
from .parameters import countries, regions, vendor_regions
from .views import Currency


def gen_live_price_query(
    countries: Optional[countries] = None,
    regions: Optional[regions] = None,
    vendor_regions: Optional[vendor_regions] = None,
):
    if not (countries or regions or vendor_regions):
        return None
    lp = (
        select(
            ServerPrice.vendor_id,
            ServerPrice.server_id,
            func.round(func.min(ServerPrice.price * Currency.rate), 4).label(
                "min_price"
            ),
            func.min(
                case(
                    (
                        ServerPrice.allocation == Allocation.SPOT,
                        func.round(ServerPrice.price * Currency.rate, 4),
                    )
                )
            ).label("min_price_spot"),
            func.min(
                case(
                    (
                        ServerPrice.allocation == Allocation.ONDEMAND,
                        func.round(ServerPrice.price * Currency.rate, 4),
                    )
                )
            ).label("min_price_ondemand"),
            func.min(
                case(
                    (
                        ServerPrice.allocation == Allocation.ONDEMAND,
                        func.round(ServerPrice.price_monthly * Currency.rate, 2),
                    )
                )
            ).label("min_price_ondemand_monthly"),
        )
        .where(ServerPrice.status == Status.ACTIVE)
        .join(
            Currency,
            (ServerPrice.currency == Currency.base) & (Currency.quote == "USD"),
        )
    )
    if countries:
        lp = lp.join(
            Region,
            (ServerPrice.vendor_id == Region.vendor_id)
            & (ServerPrice.region_id == Region.region_id),
        )
        lp = lp.where(Region.country_id.in_(countries))
    if regions:
        lp = lp.where(ServerPrice.region_id.in_(regions))
    if vendor_regions:
        lp = lp.where(vendor_region_filter(vendor_regions, ServerPrice))
    return lp.group_by(ServerPrice.vendor_id, ServerPrice.server_id).subquery()


def gen_benchmark_query(benchmark_id: str, benchmark_config: Optional[str] = None):
    query = select(
        BenchmarkScore.server_id,
        BenchmarkScore.vendor_id,
        # make sure to return only one score per server
        func.max(BenchmarkScore.score).label("benchmark_score"),
    ).where(BenchmarkScore.benchmark_id == benchmark_id)
    if benchmark_config:
        query = query.where(BenchmarkScore.config.cast(String) == benchmark_config)
    query = query.group_by(BenchmarkScore.server_id, BenchmarkScore.vendor_id)
    return query.subquery()
