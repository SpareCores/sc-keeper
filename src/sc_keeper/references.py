from enum import Enum, StrEnum
from time import time
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from sc_crawler.table_bases import (
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
from sc_crawler.table_fields import Status
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
    VendorRegions = StrEnum(
        "VendorRegions",
        {
            m.vendor_id + "~" + m.region_id: m.vendor_id + "~" + m.region_id
            for m in db.exec(select(Region)).all()
        },
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
                .where(Server.status == Status.ACTIVE)
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
                .where(Server.status == Status.ACTIVE)
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
                .where(Server.status == Status.ACTIVE)
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
                .where(Server.status == Status.ACTIVE)
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
                .where(Server.status == Status.ACTIVE)
                # exclude Google TPUs for now
                .where(not_(Server.gpu_model.like("ct%")))
                .where(not_(Server.gpu_model.like("tpu%")))
                # and a few other low-frequency models
                .where(
                    Server.gpu_model.notin_(
                        [
                            "5090",
                            "5880",
                            "ALINPU 800",
                            "H20",
                            "Lovelace",
                            "MI-308X",
                            "NETINT T408",
                            "VG1000",
                        ]
                    )
                )
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
    min_price_ondemand_monthly: Optional[float] = None
    score_per_price: Optional[float] = None
    selected_benchmark_score: Optional[float] = None
    selected_benchmark_score_per_price: Optional[float] = None


class ServerPKs(ServerWithScore):
    vendor: VendorBase


class ServerPricePKs(ServerPriceBase):
    region: RegionBase
    zone: ZoneBase


class RegionPKs(RegionBase):
    vendor: VendorBase


class RegionBaseWithPKs(RegionBase):
    country: CountryBase


class ServerPriceWithPKs(ServerPriceBase):
    price_monthly: Optional[float] = None
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
    PERFORMANCE = "performance"
    PROCESSOR = "processor"
    MEMORY = "memory"
    REGION = "region"
    VENDOR = "vendor"
    STORAGE = "storage"
    GPU = "gpu"
    TRAFFIC = "traffic"


class BenchmarkConfig(BaseModel):
    benchmark_id: str
    config: str
    category: Optional[str] = None


class VendorDebugInfo(BaseModel):
    """Statistics about benchmark coverage for a specific vendor."""

    vendor_id: str = Field(description="Vendor identifier (e.g., 'aws', 'gcp')")
    all: int = Field(description="Total number of server types for this vendor")
    active: int = Field(description="Number of active server types")
    evaluated: int = Field(
        description="Number of servers with at least one benchmark score"
    )
    missing: int = Field(
        description="Number of active servers with prices but no benchmark data"
    )
    inactive: int = Field(
        description="Number of servers without prices or with inactive status"
    )


VendorDebugInfo.model_config["json_schema_extra"] = {
    "examples": [
        {
            "vendor_id": "aws",
            "all": 500,
            "active": 450,
            "evaluated": 350,
            "missing": 100,
            "inactive": 50,
        }
    ]
}


class ServerDebugInfo(BaseModel):
    """Debug information about a single server type and its benchmark coverage."""

    vendor_id: str = Field(description="Vendor identifier")
    server_id: str = Field(description="Server type identifier")
    api_reference: str = Field(description="API reference name for the server")
    status: str = Field(description="Server status (e.g., 'ACTIVE', 'INACTIVE')")
    has_hw_info: bool = Field(
        description="Whether hardware information (e.g. CPU flags) is available"
    )
    has_price: bool = Field(description="Whether any pricing data is available")
    has_benchmarks: bool = Field(description="Whether any benchmark data is available")
    benchmarks: Dict[str, bool] = Field(
        description="Map of benchmark family names to availability (true if at least one score exists)"
    )


ServerDebugInfo.model_config["json_schema_extra"] = {
    "examples": [
        {
            "vendor_id": "aws",
            "server_id": "m5.large",
            "api_reference": "m5.large",
            "status": "ACTIVE",
            "has_hw_info": True,
            "has_price": True,
            "has_benchmarks": True,
            "benchmarks": {
                "stress_ng": True,
                "geekbench": True,
                "passmark": False,
            },
        }
    ]
}


class DebugInfoResponse(BaseModel):
    """Complete debug information about server and benchmark data availability."""

    vendors: List[VendorDebugInfo] = Field(
        description="Per-vendor statistics about benchmark coverage"
    )
    servers: List[ServerDebugInfo] = Field(
        description="Detailed information about each server type"
    )
    benchmark_families: List[str] = Field(
        description="List of all available benchmark families (e.g., 'stress_ng', 'geekbench', 'passmark')"
    )


DebugInfoResponse.model_config["json_schema_extra"] = {
    "examples": [
        {
            "vendors": VendorDebugInfo.model_config["json_schema_extra"]["examples"],
            "servers": ServerDebugInfo.model_config["json_schema_extra"]["examples"],
            "benchmark_families": ["stress_ng", "geekbench", "passmark"],
        }
    ]
}
