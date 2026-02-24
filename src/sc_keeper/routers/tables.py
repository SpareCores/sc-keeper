from typing import List

from fastapi import APIRouter, Depends, HTTPException, Security
from sc_crawler.table_fields import Status
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
from sqlmodel import Session, and_, or_, select

from .. import parameters as options
from ..auth import User, current_user
from ..currency import currency_converter
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
    vendor_regions: options.vendor_regions = None,
    allocation: options.allocation = None,
    only_active: options.only_active = True,
    currency: options.currency = None,
    user: User = Security(current_user),
    db: Session = Depends(get_db),
) -> List[ServerPrice]:
    """Query ServerPrices records without relationships resolved."""
    query = select(ServerPrice)
    if vendor:
        query = query.where(ServerPrice.vendor_id.in_(vendor))
    if region:
        query = query.where(ServerPrice.region_id.in_(region))
    if vendor_regions:
        vendor_region_clauses = []
        for vendor_region in vendor_regions:
            v, r = vendor_region.split("~")
            vendor_region_clauses.append(
                and_(ServerPrice.vendor_id == v, ServerPrice.region_id == r)
            )
        if vendor_region_clauses:
            query = query.where(or_(*vendor_region_clauses))
    if allocation:
        query = query.where(ServerPrice.allocation == allocation)
    if only_active:
        query = query.where(ServerPrice.status == Status.ACTIVE)
    prices = db.exec(query).all()
    if currency:
        for price in prices:
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


@router.get("/storage")
def table_storage(db: Session = Depends(get_db)) -> List[Storage]:
    """Return the Storage table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Storage)).all()
