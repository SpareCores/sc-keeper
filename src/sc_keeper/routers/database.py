from typing import List

from fastapi import APIRouter, Depends, Request
from sc_crawler.table_bases import DatabaseBase
from sc_crawler.table_fields import ResourceType, Status
from sc_crawler.tables import BenchmarkScore, DatabasePrice, Region
from sqlmodel import Session, select

from .. import parameters as options
from ..database import get_db
from ..helpers import (
    get_database_dict,
    get_sort_key_for_benchmark_configs,
    update_database_price_currency,
    vendor_region_filter,
)
from ..validators import check_currency, check_filter_limits

router = APIRouter()


@router.get("/database/{vendor}/{database}")
def get_database_without_relations(
    database_args: options.database_args,
) -> DatabaseBase:
    """Query a single database by its vendor id and either the database id or its API reference."""
    vendor_id, database_id = database_args
    return get_database_dict(vendor_id, database_id)


@router.get("/database/{vendor}/{database}/prices")
def get_database_prices(
    request: Request,
    database_args: options.database_args,
    countries: options.countries = None,
    vendor_regions: options.vendor_regions = None,
    currency: options.currency = "USD",
    db: Session = Depends(get_db),
) -> List[DatabasePrice]:
    """Query the current prices of a single database by its vendor id and database id."""
    vendor_id, database_id = database_args

    check_currency(currency)

    check_filter_limits(request, countries, vendor_regions=vendor_regions)

    query = select(DatabasePrice)
    if countries:
        query = query.join(DatabasePrice.region)
    query = (
        query.where(DatabasePrice.status == Status.ACTIVE)
        .where(DatabasePrice.vendor_id == vendor_id)
        .where(DatabasePrice.database_id == database_id)
    )
    if countries:
        query = query.where(Region.country_id.in_(countries))
    if vendor_regions:
        query = query.where(vendor_region_filter(vendor_regions, DatabasePrice))
    results = db.exec(query).all()

    prices = []
    for price in results:
        price = DatabasePrice.model_validate(price)
        prices.append(update_database_price_currency(price, currency))

    return prices


@router.get("/database/{vendor}/{database}/benchmarks")
def get_database_benchmarks(
    database_args: options.database_args,
    db: Session = Depends(get_db),
) -> List[BenchmarkScore]:
    """Query the current benchmark scores of a single database."""
    vendor_id, database_id = database_args

    results = db.exec(
        select(BenchmarkScore)
        .where(BenchmarkScore.resource_type == ResourceType.DATABASE)
        .where(BenchmarkScore.status == Status.ACTIVE)
        .where(BenchmarkScore.vendor_id == vendor_id)
        .where(BenchmarkScore.database_id == database_id)
    ).all()

    benchmarks = []
    for i, result in enumerate(results):
        benchmark = result.model_dump()
        benchmark["original_order"] = i
        benchmarks.append(benchmark)

    return sorted(benchmarks, key=get_sort_key_for_benchmark_configs)
