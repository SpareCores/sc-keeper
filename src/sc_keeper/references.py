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
    TrafficPriceBase,
    VendorBase,
    ZoneBase,
)
from sc_crawler.tables import (
    ComplianceFramework,
    Country,
    Region,
    Server,
    Vendor,
)
from sqlmodel import distinct, not_, select, text

from .database import session

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
    CpuManufacturers = StrEnum(
        "CpuManufacturers",
        {
            m: m
            for m in db.exec(
                select(distinct(Server.cpu_manufacturer))
                .where(Server.cpu_manufacturer.isnot(None))
                .order_by(text("1"))
            ).all()
        },
    )
    CpuFamilies = StrEnum(
        "CpuFamilies",
        {
            m: m
            for m in db.exec(
                select(distinct(Server.cpu_family))
                .where(Server.cpu_family.isnot(None))
                .order_by(text("1"))
            ).all()
        },
    )
    GpuManufacturers = StrEnum(
        "GpuManufacturers",
        {
            m: m
            for m in db.exec(
                select(distinct(Server.gpu_manufacturer))
                .where(Server.gpu_manufacturer.isnot(None))
                .order_by(text("1"))
            ).all()
        },
    )
    GpuFamilies = StrEnum(
        "GpuFamilies",
        {
            f: f
            for f in db.exec(
                select(distinct(Server.gpu_family))
                .where(Server.gpu_family.isnot(None))
                .order_by(text("1"))
            ).all()
        },
    )
    GpuModels = StrEnum(
        "GpuModels",
        {
            m: m
            for m in db.exec(
                select(distinct(Server.gpu_model))
                .where(Server.gpu_model.isnot(None))
                # exclude Google TPUs for now
                .where(not_(Server.gpu_model.like("ct%")))
                .order_by(text("1"))
            ).all()
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
    price: Optional[float] = None  # legacy
    min_price: Optional[float] = None
    min_price_spot: Optional[float] = None
    min_price_ondemand: Optional[float] = None
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


class TrafficPriceWithPKs(TrafficPriceBase):
    region: RegionBaseWithPKs
    vendor: VendorBase


class TrafficPriceWithPKsWithMonthlyTraffic(TrafficPriceWithPKs):
    price_monthly_traffic: Optional[float] = None


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
    TRAFFIC = "traffic"
