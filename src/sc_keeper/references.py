from enum import Enum, StrEnum
from time import time
from typing import List, Optional

from pydantic import BaseModel
from sc_crawler.table_bases import (
    BenchmarkScoreBase,
    CountryBase,
    RegionBase,
    ServerBase,
    ServerPriceBase,
    StorageBase,
    StoragePriceBase,
    VendorBase,
    ZoneBase,
)
from sc_crawler.tables import (
    ComplianceFramework,
    Country,
    Region,
    Vendor,
)
from sqlmodel import select

from .database import session

# make sure we have a fresh database
session.updated.wait()

# create enums from DB values for filtering options
with session.sessionmaker as db:
    Countries = StrEnum(
        "Countries",
        {m.country_id: m.country_id for m in db.exec(select(Country)).all()},
    )
    Vendors = StrEnum(
        "Vendors", {m.vendor_id: m.vendor_id for m in db.exec(select(Vendor)).all()}
    )
    Regions = StrEnum(
        "Regions",
        {m.region_id: m.region_id for m in db.exec(select(Region)).all()},
    )
    ComplianceFrameworks = StrEnum(
        "ComplianceFrameworks",
        {
            m.compliance_framework_id: m.compliance_framework_id
            for m in db.exec(select(ComplianceFramework)).all()
        },
    )


class HealthcheckResponse(BaseModel):
    packages: dict
    database_last_updated: float
    database_hash: str
    database_alembic_version: str


HealthcheckResponse.model_config["json_schema_extra"] = {
    "examples": [
        {
            "packages": {"sparecores-crawler": "1.0.0"},
            "database_last_updated": time(),
            "database_hash": "foo",
            "database_alembic_version": "bar",
        }
    ]
}


class NameAndDescription(BaseModel):
    name: str
    description: str


class IdNameAndDescription(NameAndDescription):
    id: str


class TableMetaData(BaseModel):
    table: NameAndDescription
    fields: List[IdNameAndDescription]


class IdNameAndDescriptionAndCategory(IdNameAndDescription):
    category: str
    unit: Optional[str]


class ServerTableMetaData(TableMetaData):
    fields: List[IdNameAndDescriptionAndCategory]


class ServerWithScore(ServerBase):
    score: Optional[float] = None
    price: Optional[float] = None
    score_per_price: Optional[float] = None


class ServerPKs(ServerWithScore):
    vendor: VendorBase


class ServerPricePKs(ServerPriceBase):
    region: RegionBase
    zone: ZoneBase


class ServerPKsWithPrices(ServerPKs):
    prices: List[ServerPricePKs]
    benchmark_scores: List[BenchmarkScoreBase]


class RegionPKs(RegionBase):
    vendor: VendorBase


class RegionBaseWithPKs(RegionBase):
    country: CountryBase


class ServerPriceWithPKs(ServerPriceBase):
    vendor: VendorBase
    region: RegionBaseWithPKs
    zone: ZoneBase
    server: ServerWithScore


class StoragePriceWithPKs(StoragePriceBase):
    region: RegionBaseWithPKs
    vendor: VendorBase
    storage: StorageBase


class OrderDir(Enum):
    ASC = "asc"
    DESC = "desc"


class FilterCategories(Enum):
    BASIC = "basic"
    PRICE = "price"
    PROCESSOR = "processor"
    MEMORY = "memory"
    REGION = "region"
    VENDOR = "vendor"
    STORAGE = "storage"
    GPU = "gpu"
