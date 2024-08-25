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
    Storage,
    Vendor,
    Zone,
)
from sqlmodel import Session, select

from ..database import get_db

router = APIRouter()


@router.get("/table/benchmark", tags=["Table dumps"])
def table_benchmark(db: Session = Depends(get_db)) -> List[Benchmark]:
    """Return the Benchmark table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Benchmark)).all()


@router.get("/table/country", tags=["Table dumps"])
def table_country(db: Session = Depends(get_db)) -> List[Country]:
    """Return the Country table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Country)).all()


@router.get("/table/compliance_framework", tags=["Table dumps"])
def table_compliance_frameworks(
    db: Session = Depends(get_db),
) -> List[ComplianceFramework]:
    """Return the ComplianceFramework table as-is, without filtering options or relationships resolved."""
    return db.exec(select(ComplianceFramework)).all()


@router.get("/table/vendor", tags=["Table dumps"])
def table_vendor(db: Session = Depends(get_db)) -> List[Vendor]:
    """Return the Vendor table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Vendor)).all()


@router.get("/table/region", tags=["Table dumps"])
def table_region(db: Session = Depends(get_db)) -> List[Region]:
    """Return the Region table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Region)).all()


@router.get("/table/zone", tags=["Table dumps"])
def table_zone(db: Session = Depends(get_db)) -> List[Zone]:
    """Return the Zone table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Zone)).all()


@router.get("/table/server", tags=["Table dumps"])
def table_server(db: Session = Depends(get_db)) -> List[Server]:
    """Return the Server table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Server)).all()


@router.get("/table/storage", tags=["Table dumps"])
def table_storage(db: Session = Depends(get_db)) -> List[Storage]:
    """Return the Storage table as-is, without filtering options or relationships resolved."""
    return db.exec(select(Storage)).all()
