from typing import List

from fastapi import (
    APIRouter,
    Depends,
)
from sc_crawler.tables import (
    Benchmark,
    ComplianceFramework,
    Country,
    Region,
    Server,
    ServerPrice,
    Storage,
    Vendor,
    Zone,
)
from sqlmodel import Session, select

from sc_keeper.currency import currency_converter

from .. import parameters as options
from ..database import get_db

router = APIRouter()


@router.get("/benchmark")
def table_benchmark(db: Session = Depends(get_db)) -> List[Benchmark]:
    """Return the Benchmark table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Benchmark)).all()


@router.get("/country")
def table_country(db: Session = Depends(get_db)) -> List[Country]:
    """Return the Country table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Country)).all()


@router.get("/compliance_framework")
def table_compliance_frameworks(
    db: Session = Depends(get_db),
) -> List[ComplianceFramework]:
    """Return the ComplianceFramework table as-is, without filtering options or relationships resolved."""
    return db.exec(select(ComplianceFramework)).all()


@router.get("/vendor")
def table_vendor(db: Session = Depends(get_db)) -> List[Vendor]:
    """Return the Vendor table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Vendor)).all()


@router.get("/region")
def table_region(db: Session = Depends(get_db)) -> List[Region]:
    """Return the Region table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Region)).all()


@router.get("/zone")
def table_zone(db: Session = Depends(get_db)) -> List[Zone]:
    """Return the Zone table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Zone)).all()


@router.get("/server")
def table_server(db: Session = Depends(get_db)) -> List[Server]:
    """Return the Server table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Server)).all()


@router.get("/server_prices")
def table_server_prices(
    vendor: options.vendor = None,
    region: options.regions = None,
    allocation: options.allocation = None,
    currency: options.currency = None,
    db: Session = Depends(get_db),
) -> List[ServerPrice]:
    """Query ServerPrices records without relationships resolved."""
    query = select(ServerPrice)
    if vendor:
        query = query.where(ServerPrice.vendor_id.in_(vendor))
    if region:
        query = query.where(ServerPrice.region_id.in_(region))
    if allocation:
        query = query.where(ServerPrice.allocation == allocation)
    prices = db.exec(query).all()
    if currency:
        for price in prices:
            if price.currency != currency:
                db.expunge(price)
                price.price = round(
                    currency_converter.convert(price.price, price.currency, currency),
                    4,
                )
                price.currency = currency
    return prices


@router.get("/storage")
def table_storage(db: Session = Depends(get_db)) -> List[Storage]:
    """Return the Storage table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Storage)).all()
