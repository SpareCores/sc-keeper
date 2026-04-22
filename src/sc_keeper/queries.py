from typing import List, Optional

from sc_crawler.tables import (
    Allocation,
    BenchmarkScore,
    Region,
    Server,
    ServerPrice,
    Status,
    Storage,
    StoragePrice,
    StorageType,
    TrafficDirection,
    TrafficPrice,
)
from sqlalchemy import Float, Subquery, cast, func, literal
from sqlmodel import String, case, select

from .helpers import vendor_region_filter
from .parameters import countries, regions, vendor_regions
from .views import Currency

# Treat null upper bounds as effectively unlimited in tiered pricing
_TIER_UPPER_FALLBACK = 1e18


def _tiered_total_subq(price_tiered_col, usage):
    """Correlated scalar subquery: sum of tiered costs for *usage* units in the original currency.

    Uses SQLite's json_each to iterate over the JSON price_tiered array.
    Returns NULL when price_tiered is NULL or empty (caller should COALESCE with a fallback).

    Args:
        price_tiered_col: SQLAlchemy column expression pointing at the JSON price_tiered field.
        usage: Numeric usage amount — either a Python float/int or a SQLAlchemy column
            expression (e.g. when the effective usage varies per row).
    """
    je = func.json_each(price_tiered_col).table_valued("value")
    upper = func.coalesce(
        cast(func.json_extract(je.c.value, "$.upper"), Float),
        literal(_TIER_UPPER_FALLBACK),
    )
    lower = cast(func.json_extract(je.c.value, "$.lower"), Float)
    tier_price = cast(func.json_extract(je.c.value, "$.price"), Float)
    usage_expr = literal(float(usage)) if isinstance(usage, (int, float)) else usage
    tier_cost = func.max(literal(0.0), func.min(usage_expr, upper) - lower) * tier_price
    return (
        select(func.sum(tier_cost))
        .select_from(je)
        .correlate_except(je)
        .scalar_subquery()
    )


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
    traffic_direction: TrafficDirection,
    usage: int,
    countries: Optional[countries] = None,
    regions: Optional[regions] = None,
    vendor_regions: Optional[vendor_regions] = None,
) -> Subquery:
    """Generate a subquery for the cheapest total traffic price per vendor in USD.

    Computes the full tiered cost for *usage* GB via SQLite's json_each (falling back to
    flat price × usage when price_tiered is absent), adds price_upfront, and returns the
    single cheapest row per vendor.

    Returns columns: vendor_id, total_traffic_price (monthly USD).
    """
    tiered_raw = _tiered_total_subq(TrafficPrice.price_tiered, usage)
    total_price_expr = func.round(
        (
            func.coalesce(tiered_raw, TrafficPrice.price * usage)
            + func.coalesce(TrafficPrice.price_upfront, 0.0)
        )
        * Currency.rate,
        4,
    )

    level1 = (
        select(
            TrafficPrice.vendor_id,
            total_price_expr.label("total_traffic_price"),
        )
        .where(TrafficPrice.status == Status.ACTIVE)
        .where(TrafficPrice.direction == traffic_direction)
        .join(
            Currency,
            (TrafficPrice.currency == Currency.base) & (Currency.quote == "USD"),
        )
    )
    if countries:
        level1 = level1.join(
            Region,
            (TrafficPrice.vendor_id == Region.vendor_id)
            & (TrafficPrice.region_id == Region.region_id),
        )
        level1 = level1.where(Region.country_id.in_(countries))
    if regions:
        level1 = level1.where(TrafficPrice.region_id.in_(regions))
    if vendor_regions:
        level1 = level1.where(vendor_region_filter(vendor_regions, TrafficPrice))
    level1 = level1.subquery()

    level2 = (
        select(
            level1.c.vendor_id,
            level1.c.total_traffic_price,
            func.row_number()
            .over(
                partition_by=level1.c.vendor_id,
                order_by=level1.c.total_traffic_price,
            )
            .label("rn"),
        )
    ).subquery()

    return (
        select(level2.c.vendor_id, level2.c.total_traffic_price)
        .where(level2.c.rn == 1)
        .subquery()
    )


def gen_storage_price_query(
    extra_storage_size: int,
    extra_storage_type: Optional[List[StorageType]] = None,
    countries: Optional[countries] = None,
    regions: Optional[regions] = None,
    vendor_regions: Optional[vendor_regions] = None,
) -> Subquery:
    """Generate a per-server subquery for the cheapest total external storage price in USD.

    Returns columns: vendor_id, server_id, total_storage_price (monthly USD).

    Step 1 finds the cheapest StoragePrice per vendor (by unit price, with type/region
    filters and max_size >= extra_storage_size). Step 2 joins that single product against
    every Server of the vendor and computes the per-server cost:
    - servers whose built-in storage already covers extra_storage_size → price = 0
    - otherwise: effective_usage = MAX(extra_storage_size - server.storage_size, product.min_size)
      and the tiered total (or flat price × effective_usage as fallback) is returned.
    """
    # Step 1: find the cheapest StoragePrice row per vendor by unit price.
    # Trade-off: selecting by flat unit price rather than by the full tiered total means that in rare
    # edge cases a slightly more expensive product could yield a lower total once tier boundaries and
    # the actual usage are taken into account. This compromise is intentional — evaluating every
    # product for every server would produce an O(products × servers) cross-join that is too slow.
    # The max_size filter uses the full extra_storage_size (not actual_extra = extra_storage_size -
    # Server.storage_size) because Server.storage_size is not available at this stage. This means
    # products that could cover a smaller actual_extra for servers with large built-in storage may
    # be incorrectly excluded — an acceptable residual inaccuracy given the performance constraint.
    inner = (
        select(
            StoragePrice.vendor_id,
            StoragePrice.price,
            StoragePrice.price_upfront,
            StoragePrice.price_tiered,
            Storage.min_size,
            Storage.max_size,
            Currency.rate.label("currency_rate"),
            func.row_number()
            .over(
                partition_by=StoragePrice.vendor_id,
                order_by=StoragePrice.price * Currency.rate,
            )
            .label("rn"),
        )
        .join(StoragePrice.storage)
        .where(StoragePrice.status == Status.ACTIVE)
        .where(Storage.max_size >= extra_storage_size)
        .join(
            Currency,
            (StoragePrice.currency == Currency.base) & (Currency.quote == "USD"),
        )
    )
    if extra_storage_type:
        inner = inner.where(Storage.storage_type.in_(extra_storage_type))
    if countries:
        inner = inner.join(
            Region,
            (StoragePrice.vendor_id == Region.vendor_id)
            & (StoragePrice.region_id == Region.region_id),
        )
        inner = inner.where(Region.country_id.in_(countries))
    if regions:
        inner = inner.where(StoragePrice.region_id.in_(regions))
    if vendor_regions:
        inner = inner.where(vendor_region_filter(vendor_regions, StoragePrice))
    inner = inner.subquery()

    cheapest = (
        select(
            inner.c.vendor_id,
            inner.c.price,
            inner.c.price_upfront,
            inner.c.price_tiered,
            inner.c.min_size,
            inner.c.max_size,
            inner.c.currency_rate,
        )
        .where(inner.c.rn == 1)
        .subquery()
    )

    # Step 2: join the cheapest product (one row per vendor) against all servers of that
    # vendor and compute the per-server total, applying the built-in storage deduction.
    actual_extra = literal(extra_storage_size) - Server.storage_size
    effective_usage = func.max(actual_extra, cheapest.c.min_size)

    tiered_raw = _tiered_total_subq(cheapest.c.price_tiered, effective_usage)
    total_price_raw = (
        func.coalesce(tiered_raw, cheapest.c.price * effective_usage)
        + func.coalesce(cheapest.c.price_upfront, 0.0)
    ) * cheapest.c.currency_rate

    total_price_expr = func.round(
        case(
            (Server.storage_size >= extra_storage_size, literal(0.0)),
            else_=total_price_raw,
        ),
        4,
    )

    return (
        select(
            Server.vendor_id,
            Server.server_id,
            total_price_expr.label("total_storage_price"),
        )
        .join(cheapest, Server.vendor_id == cheapest.c.vendor_id)
        .subquery()
    )
