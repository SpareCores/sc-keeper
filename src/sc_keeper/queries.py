from typing import List, Optional

from sc_crawler.tables import (
    Allocation,
    BenchmarkScore,
    Region,
    ServerPrice,
    Status,
    Storage,
    StoragePrice,
    StorageType,
    TrafficDirection,
    TrafficPrice,
)
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
) -> Subquery:
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


def gen_traffic_price_query(
    countries: Optional[countries] = None,
    vendor_regions: Optional[vendor_regions] = None,
) -> Subquery:
    """Generate a subquery for the cheapest outbound traffic unit price per (vendor_id, region_id) in USD.

    Returns columns: vendor_id, region_id, min_traffic_price (per GB in USD).
    """
    query = (
        select(
            TrafficPrice.vendor_id,
            func.round(func.min(TrafficPrice.price * Currency.rate), 4).label(
                "min_traffic_price"
            ),
            TrafficPrice.price_upfront,
            TrafficPrice.price_tiered,
        )
        .where(TrafficPrice.status == Status.ACTIVE)
        .where(TrafficPrice.direction == TrafficDirection.OUT)
        .join(
            Currency,
            (TrafficPrice.currency == Currency.base) & (Currency.quote == "USD"),
        )
    )
    if countries:
        query = query.join(
            Region,
            (TrafficPrice.vendor_id == Region.vendor_id)
            & (TrafficPrice.region_id == Region.region_id),
        )
        query = query.where(Region.country_id.in_(countries))
    if vendor_regions:
        query = query.where(vendor_region_filter(vendor_regions, TrafficPrice))
    return query.group_by(TrafficPrice.vendor_id).subquery()


def gen_storage_price_query(
    extra_storage_size: int,
    extra_storage_type: Optional[List[StorageType]] = None,
    countries: Optional[countries] = None,
    vendor_regions: Optional[vendor_regions] = None,
) -> Optional[Subquery]:
    """Generate a subquery for the cheapest storage unit price per (vendor_id, region_id) in USD.

    Filters StoragePrice by Storage.min_size/max_size matching the requested size,
    and optionally by storage type.

    Returns columns: vendor_id, region_id, min_storage_price (per GB/month in USD).
    """
    query = (
        select(
            StoragePrice.vendor_id,
            func.round(func.min(StoragePrice.price * Currency.rate), 4).label(
                "min_storage_price"
            ),
            StoragePrice.price_upfront,
            StoragePrice.price_tiered,
        )
        .join(StoragePrice.storage)
        .where(StoragePrice.status == Status.ACTIVE)
        .where(Storage.min_size <= extra_storage_size)
        .where(Storage.max_size >= extra_storage_size)
        .join(
            Currency,
            (StoragePrice.currency == Currency.base) & (Currency.quote == "USD"),
        )
    )
    if extra_storage_type:
        query = query.where(Storage.storage_type.in_(extra_storage_type))
    if countries:
        query = query.join(
            Region,
            (StoragePrice.vendor_id == Region.vendor_id)
            & (StoragePrice.region_id == Region.region_id),
        )
        query = query.where(Region.country_id.in_(countries))
    if vendor_regions:
        query = query.where(vendor_region_filter(vendor_regions, StoragePrice))
    return query.group_by(StoragePrice.vendor_id).subquery()
