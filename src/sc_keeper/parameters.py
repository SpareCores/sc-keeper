from typing import Annotated, List, Optional

from fastapi import Depends, Path, Query
from sc_crawler.table_fields import (
    Allocation,
    CpuAllocation,
    CpuArchitecture,
    DatabaseEngine,
    DatabaseHaLevel,
    DatabaseHaStrategy,
    DatabaseSecurityFeature,
    DatabaseWireProtocol,
    StorageType,
    TrafficDirection,
)

from .helpers import get_database_dict, get_server_dict
from .references import (
    BestDatabasePriceAllocation,
    BestPriceAllocation,
    CommonFilterCategory,
    ComplianceFrameworks,
    Countries,
    CpuFamilies,
    CpuFlags,
    CpuL1CacheSnapPoints,
    CpuL1CacheTotalSnapPoints,
    CpuL2CacheSnapPoints,
    CpuL2CacheTotalSnapPoints,
    CpuL3CacheSnapPoints,
    CpuL3CacheTotalSnapPoints,
    CpuManufacturers,
    CpuSpeedSnapPoints,
    DatabaseFilterCategory,
    GpuFamilies,
    GpuManufacturers,
    GpuModels,
    NetworkSpeedSnapPoints,
    NetworkStorageSpeedSnapPoints,
    OrderDir,
    Regions,
    ServerColumns,
    ServerFilterCategory,
    VendorRegions,
    Vendors,
)

# ##############################################################################
# Shared API query parameters

vendor = Annotated[
    Optional[List[Vendors]],
    Query(
        title="Vendor",
        description="Identifier of the cloud provider vendor.",
        json_schema_extra={
            "category_id": CommonFilterCategory.VENDOR,
            "enum": [m.value for m in Vendors],
        },
    ),
]

partial_name_or_id = Annotated[
    Optional[str],
    Query(
        title="Partial name or id",
        description="Freetext, case-insensitive search on the server_id, name, api_reference or display_name.",
        json_schema_extra={
            "category_id": CommonFilterCategory.BASIC,
        },
    ),
]

vcpus_min = Annotated[
    int,
    Query(
        title="Minimum vCPUs",
        description="Minimum number of virtual CPUs.",
        ge=1,
        le=256,
        json_schema_extra={
            "category_id": CommonFilterCategory.PROCESSOR,
            "unit": "vCPUs",
            "range_min": 1,
            "range_max": 256,
        },
    ),
]

vcpus_max = Annotated[
    Optional[int],
    Query(
        title="Maximum vCPUs",
        description="Maximum number of virtual CPUs.",
        ge=1,
        le=256,
        json_schema_extra={
            "category_id": CommonFilterCategory.PROCESSOR,
            "unit": "vCPUs",
            "range_min": 1,
            "range_max": 256,
            "null_value": 256,
        },
    ),
]

architecture = Annotated[
    Optional[List[CpuArchitecture]],
    Query(
        title="Processor architecture",
        description="Processor architecture.",
        json_schema_extra={
            "category_id": CommonFilterCategory.PROCESSOR,
            "enum": [e.value for e in CpuArchitecture],
        },
    ),
]

cpu_manufacturer = Annotated[
    Optional[List[CpuManufacturers]],
    Query(
        title="Processor manufacturer",
        json_schema_extra={
            "category_id": CommonFilterCategory.PROCESSOR,
            "enum": [e.value for e in CpuManufacturers],
        },
    ),
]

cpu_family = Annotated[
    Optional[List[CpuFamilies]],
    Query(
        title="Processor family",
        json_schema_extra={
            "category_id": CommonFilterCategory.PROCESSOR,
            "enum": [e.value for e in CpuFamilies],
        },
    ),
]

cpu_flags = Annotated[
    Optional[List[CpuFlags]],
    Query(
        title="CPU flags",
        description="Required CPU flags.",
        json_schema_extra={
            "category_id": CommonFilterCategory.PROCESSOR,
            "enum": [e.value for e in CpuFlags],
        },
    ),
]

cpu_allocation = Annotated[
    Optional[List[CpuAllocation]],
    Query(
        title="CPU allocation",
        description="Allocation of the CPU(s) to the server, e.g. shared, burstable or dedicated.",
        json_schema_extra={
            "category_id": CommonFilterCategory.PROCESSOR,
            "enum": [e.value for e in CpuAllocation],
        },
    ),
]

memory_min = Annotated[
    Optional[float],
    Query(
        title="Required memory",
        description="Required amount of memory in GBs.",
        json_schema_extra={
            "category_id": CommonFilterCategory.MEMORY,
            "unit": "GB",
            "step": 0.1,
        },
    ),
]

price_max = Annotated[
    Optional[float],
    Query(
        title="Maximum price",
        description="Maximum price (USD/hr).",
        json_schema_extra={
            "category_id": CommonFilterCategory.PRICE,
            "step": 0.0001,
        },
    ),
]


only_active = Annotated[
    Optional[bool],
    Query(
        title="Active only",
        description="Filter for active resources only.",
        json_schema_extra={"category_id": CommonFilterCategory.BASIC},
    ),
]

only_orderable = Annotated[
    Optional[bool],
    Query(
        title="Orderable only",
        description="Filter for orderable (active or planned for retirement) resources only.",
        json_schema_extra={"category_id": CommonFilterCategory.BASIC},
    ),
]

green_energy = Annotated[
    Optional[bool],
    Query(
        title="Green energy",
        description="Filter for regions that are 100% powered by renewable energy.",
        json_schema_extra={"category_id": CommonFilterCategory.REGION},
    ),
]

allocation = Annotated[
    Optional[Allocation],
    Query(
        title="Allocation",
        description="Server allocation method.",
        json_schema_extra={
            "enum": [m.value for m in Allocation],
        },
    ),
]


regions = Annotated[
    Optional[List[Regions]],
    Query(
        title="Region",
        description="Identifier of the region. Note that region ids are not vendor-specific, so when you select a region, you might get results from multiple vendors. For more precise filtering, use vendor_regions instead.",
        json_schema_extra={
            "category_id": CommonFilterCategory.REGION,
            "enum": [m.value for m in Regions],
        },
    ),
]

vendor_regions = Annotated[
    Optional[List[VendorRegions]],
    Query(
        title="Vendor and region",
        description="Identifier of the vendor and region, separated by a tilde.",
        json_schema_extra={
            "category_id": CommonFilterCategory.REGION,
            "enum": [m.value for m in VendorRegions],
        },
    ),
]

server_region = Annotated[
    Optional[str],
    Query(
        title="Server region",
        description="Region of the baseline server, used for score_per_price ordering to find servers with a similar score_per_price.",
        json_schema_extra={
            "category_id": CommonFilterCategory.REGION,
            "enum": [m.value for m in Regions],
        },
    ),
]

compliance_framework = Annotated[
    Optional[List[ComplianceFrameworks]],
    Query(
        title="Compliance framework",
        description="Compliance framework implemented at the vendor.",
        json_schema_extra={
            "category_id": CommonFilterCategory.VENDOR,
            "enum": [m.value for m in ComplianceFrameworks],
        },
    ),
]

network_speed_baseline_min = Annotated[
    Optional[float],
    Query(
        title="Required baseline network speed",
        description="Required baseline network speed in Gbps.",
        json_schema_extra={
            "category_id": CommonFilterCategory.TRAFFIC,
            "enum": [e.value for e in NetworkSpeedSnapPoints],
            "unit": "Gbps",
        },
    ),
]

network_speed_max_min = Annotated[
    Optional[float],
    Query(
        title="Required maximum network speed",
        description="Required maximum network speed in Gbps.",
        json_schema_extra={
            "category_id": CommonFilterCategory.TRAFFIC,
            "enum": [e.value for e in NetworkSpeedSnapPoints],
            "unit": "Gbps",
        },
    ),
]

cpu_speed_min = Annotated[
    Optional[float],
    Query(
        title="Required CPU speed",
        description="Required CPU speed in GHz.",
        json_schema_extra={
            "category_id": CommonFilterCategory.PROCESSOR,
            "enum": [e.value for e in CpuSpeedSnapPoints],
            "unit": "GHz",
        },
    ),
]

cpu_l1d_cache_min = Annotated[
    Optional[int],
    Query(
        title="Required L1 data cache size",
        description="Required L1 data cache size in KiBs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.CPU_CACHE,
            "enum": [e.value for e in CpuL1CacheSnapPoints],
            "unit": "KiB",
        },
    ),
]

cpu_l1d_cache_total_min = Annotated[
    Optional[int],
    Query(
        title="Required L1 data cache size across all cores",
        description="Required L1 data cache size across all cores in KiBs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.CPU_CACHE,
            "enum": [e.value for e in CpuL1CacheTotalSnapPoints],
            "unit": "KiB",
        },
    ),
]

cpu_l1i_cache_min = Annotated[
    Optional[int],
    Query(
        title="Required L1 instruction cache size",
        description="Required L1 instruction cache size in KiBs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.CPU_CACHE,
            "enum": [e.value for e in CpuL1CacheSnapPoints],
            "unit": "KiB",
        },
    ),
]

cpu_l1i_cache_total_min = Annotated[
    Optional[int],
    Query(
        title="Required L1 instruction cache size across all cores",
        description="Required L1 instruction cache size across all cores in KiBs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.CPU_CACHE,
            "enum": [e.value for e in CpuL1CacheTotalSnapPoints],
            "unit": "KiB",
        },
    ),
]

cpu_l2_cache_min = Annotated[
    Optional[int],
    Query(
        title="Required L2 cache size",
        description="Required L2 cache size in KiBs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.CPU_CACHE,
            "enum": [e.value for e in CpuL2CacheSnapPoints],
            "unit": "KiB",
        },
    ),
]

cpu_l2_cache_total_min = Annotated[
    Optional[int],
    Query(
        title="Required L2 cache size across all cores",
        description="Required L2 cache size across all cores in KiBs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.CPU_CACHE,
            "enum": [e.value for e in CpuL2CacheTotalSnapPoints],
            "unit": "KiB",
        },
    ),
]

cpu_l3_cache_min = Annotated[
    Optional[int],
    Query(
        title="Required L3 cache size",
        description="Required L3 cache size in MiBs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.CPU_CACHE,
            "enum": [e.value for e in CpuL3CacheSnapPoints],
            "unit": "MiB",
        },
    ),
]

cpu_l3_cache_total_min = Annotated[
    Optional[int],
    Query(
        title="Required L3 cache size across all cores",
        description="Required L3 cache size across all cores in MiBs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.CPU_CACHE,
            "enum": [e.value for e in CpuL3CacheTotalSnapPoints],
            "unit": "MiB",
        },
    ),
]

hw_virt = Annotated[
    Optional[bool],
    Query(
        title="Hardware virtualization",
        description="Filter for servers with hardware virtualization.",
        json_schema_extra={"category_id": CommonFilterCategory.PROCESSOR},
    ),
]

cpu_hyperthreading = Annotated[
    Optional[bool],
    Query(
        title="CPU hyperthreading",
        description=(
            'Filter by CPU hyperthreading. Uses the "ht" CPU flag when flags are known, '
            "otherwise falls back to comparing vCPUs and CPU cores "
            "(hyperthreaded: vCPUs > cores, non-hyperthreaded: vCPUs == cores)."
        ),
        json_schema_extra={"category_id": CommonFilterCategory.PROCESSOR},
    ),
]

storage_size = Annotated[
    Optional[float],
    Query(
        title="Required local storage size",
        description="Required amount of built-in local (SSD, HDD, NVMe) server storage in GBs.",
        json_schema_extra={
            "category_id": CommonFilterCategory.STORAGE,
            "step": 0.1,
            "unit": "GB",
        },
    ),
]


storage_type = Annotated[
    Optional[List[StorageType]],
    Query(
        title="Local storage type",
        description="Storage type of the server's built-in local storage (e.g. HDD, SSD, NVMe).",
        json_schema_extra={
            "category_id": CommonFilterCategory.STORAGE,
            "enum": [e.value for e in StorageType],
        },
    ),
]

network_storage_speed_baseline_min = Annotated[
    Optional[float],
    Query(
        title="Required baseline network storage speed",
        description="Required baseline network storage speed in Gbps.",
        json_schema_extra={
            "category_id": CommonFilterCategory.TRAFFIC,
            "enum": [e.value for e in NetworkStorageSpeedSnapPoints],
            "unit": "Gbps",
        },
    ),
]

network_storage_speed_max_min = Annotated[
    Optional[float],
    Query(
        title="Required maximum network storage speed",
        description="Required maximum network storage speed in Gbps.",
        json_schema_extra={
            "category_id": CommonFilterCategory.TRAFFIC,
            "enum": [e.value for e in NetworkStorageSpeedSnapPoints],
            "unit": "Gbps",
        },
    ),
]

monthly_inbound_traffic = Annotated[
    Optional[int],
    Query(
        title="Monthly inbound traffic",
        description=(
            "Monthly inbound traffic in GBs to add to the total price. "
            "The cheapest available inbound traffic price for the vendor is used."
        ),
        json_schema_extra={
            "category_id": CommonFilterCategory.TRAFFIC,
            "unit": "GB",
            "step": 1,
        },
    ),
]

monthly_outbound_traffic = Annotated[
    Optional[int],
    Query(
        title="Monthly outbound traffic",
        description=(
            "Monthly outbound traffic in GBs to add to the total price. "
            "The cheapest available outbound traffic price for the vendor is used."
        ),
        json_schema_extra={
            "category_id": CommonFilterCategory.TRAFFIC,
            "unit": "GB",
            "step": 1,
        },
    ),
]

extra_storage_size = Annotated[
    Optional[int],
    Query(
        title="Required storage size",
        description=(
            "Total storage needed in GBs, combining local (where applicable) and ondemand network storage. "
            "The server's built-in storage is subtracted from this amount, "
            "and only the difference is priced as additional external storage. "
            "Servers whose built-in storage already meets or exceeds this value incur no extra storage cost."
        ),
        json_schema_extra={
            "category_id": CommonFilterCategory.STORAGE,
            "step": 1,
            "unit": "GB",
        },
    ),
]

extra_storage_type = Annotated[
    Optional[List[StorageType]],
    Query(
        title="Required storage type",
        description=(
            "Storage product type (e.g. HDD, SSD, NVMe) for the required storage price lookup. "
            "When omitted, the cheapest available type (usually HDD over network) is used."
        ),
        json_schema_extra={
            "category_id": CommonFilterCategory.STORAGE,
            "enum": [e.value for e in StorageType],
        },
    ),
]

direction = Annotated[
    Optional[List[TrafficDirection]],
    Query(
        title="Direction",
        description="Direction of the Internet traffic.",
        json_schema_extra={
            "category_id": CommonFilterCategory.TRAFFIC,
            "enum": [e.value for e in TrafficDirection],
        },
    ),
]

monthly_traffic = Annotated[
    Optional[float],
    Query(
        title="Monthly overall traffic",
        description="Overall amount of monthly traffic (GBs).",
        json_schema_extra={
            "category_id": CommonFilterCategory.TRAFFIC,
            "unit": "GB",
            "step": 1,
        },
    ),
]

countries = Annotated[
    Optional[List[Countries]],
    Query(
        title="Countries",
        description="Filter for regions in the provided list of countries.",
        json_schema_extra={
            "category_id": CommonFilterCategory.REGION,
            "enum": [e.value for e in Countries],
        },
    ),
]


gpu_min = Annotated[
    Optional[float],
    Query(
        title="GPU count",
        description="Required number of GPUs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.GPU,
            "unit": "GPUs",
        },
    ),
]

gpu_memory_min = Annotated[
    Optional[float],
    Query(
        title="Required GPU memory",
        description="Required amount of GPU memory (GB) in each GPU.",
        json_schema_extra={
            "category_id": ServerFilterCategory.GPU,
            "unit": "GB",
            "step": 0.1,
        },
    ),
]


gpu_memory_total = Annotated[
    Optional[float],
    Query(
        title="Total GPU memory",
        description="Required amount of total GPU memory (GBs) in all GPUs.",
        json_schema_extra={
            "category_id": ServerFilterCategory.GPU,
            "unit": "GB",
            "step": 0.1,
        },
    ),
]


gpu_manufacturer = Annotated[
    Optional[List[GpuManufacturers]],
    Query(
        title="GPU manufacturer",
        json_schema_extra={
            "category_id": ServerFilterCategory.GPU,
            "enum": [m.value for m in GpuManufacturers],
        },
    ),
]


gpu_family = Annotated[
    Optional[List[GpuFamilies]],
    Query(
        title="GPU family",
        json_schema_extra={
            "category_id": ServerFilterCategory.GPU,
            "enum": [m.value for m in GpuFamilies],
        },
    ),
]


gpu_model = Annotated[
    Optional[List[GpuModels]],
    Query(
        title="GPU model",
        json_schema_extra={
            "category_id": ServerFilterCategory.GPU,
            "enum": [m.value for m in GpuModels],
        },
    ),
]


benchmark_score_stressng_cpu_min = Annotated[
    Optional[float],
    Query(
        title="Required SCore",
        description="Required stress-ng div16 CPU workload score.",
        json_schema_extra={
            "category_id": CommonFilterCategory.PERFORMANCE,
        },
    ),
]


benchmark_score_per_price_stressng_cpu_min = Annotated[
    Optional[float],
    Query(
        title="Required $Core",
        description="Required stress-ng div16 CPU workload score per USD/hr (using the best ondemand or spot price of all zones).",
        json_schema_extra={
            "category_id": CommonFilterCategory.PERFORMANCE,
            "unit": "/USD",
        },
    ),
]


benchmark_id = Annotated[
    Optional[str],
    Query(
        title="Benchmark ID",
        description="Selected benchmark ID for the min filters and ordering.",
    ),
]


benchmark_config = Annotated[
    Optional[str],
    Query(
        title="Benchmark config",
        description="Selected benchmark config for the min filters and ordering.",
    ),
]

benchmark_score_min = Annotated[
    Optional[float],
    Query(
        title="Required benchmark score",
        description="Required value of the selected benchmark score.",
        json_schema_extra={
            "category_id": CommonFilterCategory.PERFORMANCE,
        },
    ),
]


benchmark_score_per_price_min = Annotated[
    Optional[float],
    Query(
        title="Required benchmark score/price",
        description="Required value of the selected benchmark score per USD/hr (using the best ondemand or spot price of all zones).",
        json_schema_extra={
            "category_id": CommonFilterCategory.PERFORMANCE,
            "unit": "/USD",
        },
    ),
]


limit = Annotated[
    int, Query(description="Maximum number of results. Set to -1 for unlimited.")
]

limit250 = Annotated[int, Query(description="Maximum number of results.", le=250)]

page = Annotated[Optional[int], Query(description="Page number.")]

order_by = Annotated[str, Query(description="Order by column.")]

order_dir = Annotated[OrderDir, Query(description="Order direction.")]

currency = Annotated[Optional[str], Query(description="Currency used for prices.")]

best_price_allocation = Annotated[
    Optional[BestPriceAllocation],
    Query(
        title="Best price allocation strategy",
        description='Controls how the server\'s "best price" is computed: use only spot prices, only on-demand prices, or the lowest available price from any allocation type.',
        json_schema_extra={"enum": [e.value for e in BestPriceAllocation]},
    ),
]

best_database_price_allocation = Annotated[
    Optional[BestDatabasePriceAllocation],
    Query(
        title="Best price allocation strategy",
        description=(
            'Controls how the database\'s "best price" is computed: on-demand hourly, '
            "monthly, or the lowest available on-demand price."
        ),
        json_schema_extra={"enum": [e.value for e in BestDatabasePriceAllocation]},
    ),
]

add_total_count_header = Annotated[
    bool,
    Query(
        description="Add the X-Total-Count header to the response with the overall number of items (without paging). Note that it might reduce response times."
    ),
]

benchmark_id = Annotated[
    str,
    Query(description="Benchmark id to use as the main score for the server."),
]
benchmark_config = Annotated[
    Optional[str],
    Query(
        description="Optional benchmark config dict JSON to filter results of a benchmark_id."
    ),
]


def server_args_tuple(
    vendor: Annotated[str, Path(description="A Vendor's ID.")],
    server: Annotated[str, Path(description="A Server's ID or API reference.")],
):
    return vendor, get_server_dict(vendor, server)["server_id"]


server_args = Annotated[tuple[str, str], Depends(server_args_tuple)]


def database_args_tuple(
    vendor: Annotated[str, Path(description="A Vendor's ID.")],
    database: Annotated[str, Path(description="A Database's ID or API reference.")],
):
    return vendor, get_database_dict(vendor, database)["database_id"]


database_args = Annotated[tuple[str, str], Depends(database_args_tuple)]

server_columns = Annotated[
    Optional[List[ServerColumns]],
    Query(
        title="Server columns",
        description="Selected server columns.",
        json_schema_extra={"enum": [e.value for e in ServerColumns]},
    ),
]

database_partial_name_or_id = Annotated[
    Optional[str],
    Query(
        title="Partial name or id",
        description="Freetext, case-insensitive search on the database_id, name, api_reference or display_name.",
        json_schema_extra={
            "category_id": CommonFilterCategory.BASIC,
        },
    ),
]

database_engine = Annotated[
    Optional[DatabaseEngine],
    Query(
        title="Database engine",
        description="Managed database engine.",
        json_schema_extra={
            "category_id": DatabaseFilterCategory.ENGINE,
            "enum": [e.value for e in DatabaseEngine],
        },
    ),
]

database_engine_version = Annotated[
    Optional[str],
    Query(
        title="Engine version",
        description="Required major engine version.",
        json_schema_extra={
            "category_id": DatabaseFilterCategory.ENGINE,
        },
    ),
]

database_storage_size = Annotated[
    Optional[float],
    Query(
        title="Required bundled storage size",
        description=(
            "Required amount of storage (GB) bundled with the database instance."
        ),
        json_schema_extra={
            "category_id": CommonFilterCategory.STORAGE,
            "step": 0.1,
            "unit": "GB",
        },
    ),
]

database_extra_storage_size = Annotated[
    Optional[int],
    Query(
        title="Required storage size",
        description=(
            "Total storage needed in GBs, combining bundled (where applicable) and "
            "on-demand database storage. Bundled storage is subtracted; any remainder "
            "is billed as extra storage (at least the instance's storage_extra_min). "
            "Instances where bundled + storage_extra_max is below this value are excluded."
        ),
        json_schema_extra={
            "category_id": CommonFilterCategory.STORAGE,
            "step": 1,
            "unit": "GB",
        },
    ),
]

database_wire_protocol = Annotated[
    Optional[List[DatabaseWireProtocol]],
    Query(
        title="Wire protocol",
        description="Network protocol used for client connections.",
        json_schema_extra={
            "category_id": DatabaseFilterCategory.ENGINE,
            "enum": [e.value for e in DatabaseWireProtocol],
        },
    ),
]

database_auto_upgrade_versions = Annotated[
    Optional[bool],
    Query(
        title="Auto-upgrade versions",
        description=(
            "Filter for database instances that support auto-upgrade between minor "
            "engine versions."
        ),
        json_schema_extra={"category_id": DatabaseFilterCategory.ENGINE},
    ),
]

database_ha = Annotated[
    Optional[List[DatabaseHaLevel]],
    Query(
        title="High availability levels",
        description=(
            "Required HA levels; all must appear in the database instance's supported list."
        ),
        json_schema_extra={
            "category_id": DatabaseFilterCategory.FEATURES,
            "enum": [e.value for e in DatabaseHaLevel],
        },
    ),
]

database_ha_strategy = Annotated[
    Optional[List[DatabaseHaStrategy]],
    Query(
        title="High availability strategies",
        description=(
            "Required HA strategies; all must appear in the database instance's supported list."
        ),
        json_schema_extra={
            "category_id": DatabaseFilterCategory.FEATURES,
            "enum": [e.value for e in DatabaseHaStrategy],
        },
    ),
]

database_max_read_replicas_min = Annotated[
    Optional[int],
    Query(
        title="Minimum max read replicas",
        description=(
            "Minimum number of read-only replica nodes the database instance must support."
        ),
        json_schema_extra={
            "category_id": DatabaseFilterCategory.FEATURES,
            "step": 1,
        },
    ),
]

database_storage_extra_autosize = Annotated[
    Optional[bool],
    Query(
        title="Storage autosize",
        description=(
            "Filter for database instances that can automatically expand storage as "
            "disk usage grows."
        ),
        json_schema_extra={"category_id": CommonFilterCategory.STORAGE},
    ),
]

database_disk_encryption = Annotated[
    Optional[bool],
    Query(
        title="Disk encryption",
        description="Filter for database instances with storage encrypted at rest.",
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_scheduled_backups = Annotated[
    Optional[bool],
    Query(
        title="Scheduled backups",
        description="Filter for database instances that support scheduled/automated backups.",
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_continuous_backups_min = Annotated[
    Optional[int],
    Query(
        title="Minimum continuous backup retention",
        description="Minimum point-in-time recovery retention in days.",
        json_schema_extra={
            "category_id": DatabaseFilterCategory.FEATURES,
            "unit": "days",
            "step": 1,
        },
    ),
]

database_connection_pool = Annotated[
    Optional[bool],
    Query(
        title="Connection pool",
        description="Filter for database instances with managed connection proxy support.",
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_system_monitoring = Annotated[
    Optional[bool],
    Query(
        title="System monitoring",
        description=(
            "Filter for database instances with host-level CPU, RAM, and disk metrics."
        ),
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_monitoring = Annotated[
    Optional[bool],
    Query(
        title="Database monitoring",
        description=(
            "Filter for database instances with engine performance insights "
            "(slow queries, locks, execution plans)."
        ),
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_autotuning_advice = Annotated[
    Optional[bool],
    Query(
        title="Autotuning advice",
        description=(
            "Filter for database instances that analyze workload and generate "
            "performance tuning advice."
        ),
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_autotuning_apply = Annotated[
    Optional[bool],
    Query(
        title="Autotuning apply",
        description=(
            "Filter for database instances that automatically apply performance fixes "
            "without operator intervention."
        ),
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_custom_config = Annotated[
    Optional[bool],
    Query(
        title="Custom configuration",
        description="Filter for database instances that support custom configuration.",
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_custom_extensions = Annotated[
    Optional[bool],
    Query(
        title="Custom extensions",
        description="Filter for database instances that support custom extensions.",
        json_schema_extra={"category_id": DatabaseFilterCategory.FEATURES},
    ),
]

database_security_features = Annotated[
    Optional[List[DatabaseSecurityFeature]],
    Query(
        title="Security features",
        description=(
            "Required security features; all must be supported by the database instance."
        ),
        json_schema_extra={
            "category_id": DatabaseFilterCategory.FEATURES,
            "enum": [e.value for e in DatabaseSecurityFeature],
        },
    ),
]

database_sla_min = Annotated[
    Optional[float],
    Query(
        title="Minimum SLA",
        description="Minimum service level agreement as a percentage, e.g. 99.95.",
        json_schema_extra={
            "category_id": DatabaseFilterCategory.FEATURES,
            "step": 0.01,
        },
    ),
]

database_benchmark_score_min = Annotated[
    Optional[float],
    Query(
        title="Required benchmark score",
        description=("Required value of the selected benchmark score."),
        json_schema_extra={
            "category_id": CommonFilterCategory.PERFORMANCE,
        },
    ),
]

database_benchmark_score_per_price_min = Annotated[
    Optional[float],
    Query(
        title="Required benchmark score/price",
        description=(
            "Required value of the selected benchmark score per USD/hr (using the best on-demand price)."
        ),
        json_schema_extra={
            "category_id": CommonFilterCategory.PERFORMANCE,
            "unit": "/USD",
        },
    ),
]
