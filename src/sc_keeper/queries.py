from typing import Optional

from sc_crawler.tables import Allocation, BenchmarkScore, Region, ServerPrice, Status
from sqlalchemy import Subquery
from sqlmodel import String, case, func, select

from .helpers import vendor_region_filter
from .parameters import countries, regions, vendor_regions
from .views import Currency


def gen_live_price_query(
    countries: Optional[countries] = None,
    regions: Optional[regions] = None,
    vendor_regions: Optional[vendor_regions] = None,
) -> Optional[Subquery]:
    """Generate a subquery for the live prices of servers in USD.

    Join with this live lookup when the best global prices from ServerExtra are not enough,
    and you need country/region/vendor-specific best ondemand/spot/monthly etc prices.

    Args:
        countries: Optional[countries]: The list of country IDs to filter the server prices by.
        regions: Optional[regions]: The list of region IDs to filter the server prices by.
        vendor_regions: Optional[vendor_regions]: The list of vendor and region ID pairs separated by a tilde (~) to filter the server prices by.

    Returns:
        A subquery or None if no filters are provided.
    """
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


def gen_benchmark_query(
    benchmark_id: str, benchmark_config: Optional[str] = None
) -> Optional[Subquery]:
    """Generate a subquery for the filtered view of max benchmark scores of servers.

    Use this subquery when you need to custom benchmark scores e.g. for ordering instead of the global ServerExtra.score.

    Args:
        benchmark_id: The ID of the benchmark to filter the benchmark scores by.
        benchmark_config: Optional[str]: The configuration of the benchmark to filter the benchmark scores by.
    """
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
