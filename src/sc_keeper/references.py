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
            for m in db.exec(
                select(Region).order_by(Region.vendor_id, Region.region_id)
            ).all()
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


ServerColumns = StrEnum(
    "ServerColumns",
    {col: col for col in Server.get_columns()["all"]},
)


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


class PriceBreakdown(BaseModel):
    compute_monthly: Optional[float] = None
    traffic_monthly: Optional[float] = None
    extra_storage_monthly: Optional[float] = None
    total_monthly: Optional[float] = None


class ServerPKs(ServerWithScore):
    vendor: VendorBase


class ServerWithPriceBreakdown(ServerWithScore):
    vendor: VendorBase
    price_breakdown: Optional[PriceBreakdown] = None


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
    CPU_CACHE = "cpu_cache"
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


class BenchmarkHistogram(BaseModel):
    """Histogram data for a benchmark score distribution."""

    breakpoints: List[float] = Field(
        description="NUM_BINS + 1 boundary values defining the edges of each bucket"
    )
    counts: List[int] = Field(
        description="Number of scores falling in each bucket (length == len(breakpoints) - 1)"
    )


class BenchmarkScoreStatsItem(BaseModel):
    """Aggregate statistics and score distribution for a single benchmark."""

    benchmark_id: str = Field(description="Unique identifier of the benchmark")
    name: str = Field(description="Human-friendly name of the benchmark")
    description: Optional[str] = Field(
        default=None, description="Short description of the benchmark"
    )
    framework: str = Field(description="The benchmark framework/software/tool used")
    measurement: Optional[str] = Field(
        default=None, description="Name of the measurement recorded"
    )
    unit: Optional[str] = Field(
        default=None, description="Optional unit of measurement for the score"
    )
    higher_is_better: bool = Field(
        description="Whether a higher score indicates better performance"
    )
    status: str = Field(description="Benchmark status (e.g., 'ACTIVE', 'INACTIVE')")
    configs: dict = Field(
        default_factory=dict,
        description=(
            "Benchmark config fields enriched with example values. "
            "Keys come from Benchmark.config_fields; each value includes the original "
            "description plus an 'examples' list of unique observed config values."
        ),
    )
    count: int = Field(
        description="Total number of active, non-null benchmark score records"
    )
    count_servers: int = Field(
        description="Number of distinct (vendor_id, server_id) pairs with scores"
    )
    histogram: Optional[BenchmarkHistogram] = Field(
        default=None,
        description="Score distribution histogram; None when no scores are available",
    )


BenchmarkScoreStatsItem.model_config["json_schema_extra"] = {
    "examples": [
        {
            "benchmark_id": "stress_ng:cpu_all",
            "name": "stress-ng CPU (all methods)",
            "description": "Stress CPU using all available stress methods",
            "framework": "stress_ng",
            "measurement": "bogo_ops_per_second",
            "unit": "bogo ops/s",
            "higher_is_better": True,
            "status": "ACTIVE",
            "configs": {
                "cores": {
                    "description": "Stressing a single core or all cores.",
                    "examples": ["All Cores", "Single-Core Performance"],
                }
            },
            "count": 1234,
            "count_servers": 456,
            "histogram": {
                "breakpoints": [100.0, 200.0, 300.0],
                "counts": [50, 100],
            },
        }
    ]
}


class BestPriceAllocation(StrEnum):
    """Controls how the server's "best price" is computed: use only spot prices, only on-demand prices, or the lowest available price from any allocation type."""

    ANY = "ANY"
    SPOT_ONLY = "SPOT_ONLY"
    ONDEMAND_ONLY = "ONDEMAND_ONLY"
    MONTHLY = "MONTHLY"


class NetworkSpeedSnapPoints(Enum):
    """Predefined snap points for network speed filtering, based on common values in the dataset (in Gbps)."""

    MBPS_10 = 0.01
    MBPS_50 = 0.05
    MBPS_100 = 0.1
    MBPS_500 = 0.5
    GBPS_1 = 1
    GBPS_5 = 5
    GBPS_10 = 10
    GBPS_25 = 25
    GBPS_50 = 50
    GBPS_100 = 100
    GBPS_500 = 500
    GBPS_1000 = 1000
    GBPS_10000 = 10000
    GBPS_25000 = 25000


class CpuSpeedSnapPoints(Enum):
    """Predefined snap points for CPU speed filtering, based on common values in the dataset (in GHz)."""

    GHZ_1_0 = 1.0
    GHZ_1_5 = 1.5
    GHZ_2_0 = 2.0
    GHZ_2_5 = 2.5
    GHZ_3_0 = 3.0
    GHZ_3_5 = 3.5
    GHZ_4_0 = 4.0
    GHZ_4_5 = 4.5
    GHZ_5_0 = 5.0


class CpuL1CacheSnapPoints(Enum):
    """Predefined snap points for CPU L1 cache filtering, based on common values in the dataset (in KiB)."""

    KIB_32 = 32
    KIB_48 = 48
    KIB_64 = 64
    KIB_128 = 128


class CpuL1CacheTotalSnapPoints(Enum):
    """Predefined snap points for total CPU L1 cache filtering, based on common values in the dataset (in KiB)."""

    KIB_32 = 32
    KIB_64 = 64
    KIB_128 = 128
    KIB_192 = 192
    KIB_256 = 256
    KIB_384 = 384
    KIB_512 = 512
    KIB_768 = 768
    MIB_1 = 1024
    MIB_1_5 = 1536
    MIB_2 = 2048
    MIB_3 = 3072
    MIB_4 = 4096
    MIB_6 = 6144
    MIB_12 = 12288


class CpuL2CacheSnapPoints(Enum):
    """Predefined snap points for CPU L2 cache filtering, based on common values in the dataset (in KiB)."""

    KIB_256 = 256
    KIB_512 = 512
    MIB_1 = 1024
    MIB_2 = 2048
    MIB_4 = 4096


class CpuL2CacheTotalSnapPoints(Enum):
    """Predefined snap points for total CPU L2 cache filtering, based on common values in the dataset (in KiB)."""

    KIB_256 = 256
    KIB_512 = 512
    MIB_1 = 1024
    MIB_2 = 2048
    MIB_4 = 4096
    MIB_8 = 8192
    MIB_16 = 16384
    MIB_24 = 24576
    MIB_32 = 32768
    MIB_48 = 49152
    MIB_64 = 65536
    MIB_96 = 98304
    MIB_128 = 131072
    MIB_192 = 196608
    MIB_384 = 393216


class CpuL3CacheSnapPoints(Enum):
    """Predefined snap points for CPU L3 cache filtering, based on common values in the dataset (in MiB)."""

    MIB_4 = 4
    MIB_8 = 8
    MIB_16 = 16
    MIB_32 = 32
    MIB_48 = 48
    MIB_64 = 64
    MIB_128 = 128
    MIB_256 = 256
    MIB_480 = 480


class CpuL3CacheTotalSnapPoints(Enum):
    """Predefined snap points for total CPU L3 cache filtering, based on common values in the dataset (in MiB)."""

    MIB_8 = 8
    MIB_16 = 16
    MIB_32 = 32
    MIB_48 = 48
    MIB_64 = 64
    MIB_96 = 96
    MIB_128 = 128
    MIB_192 = 192
    MIB_256 = 256
    MIB_480 = 480
    GIB_1 = 1024
    GIB_2 = 2048
    GIB_4 = 4096
