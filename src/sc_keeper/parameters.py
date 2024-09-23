from typing import Annotated, List, Optional

from fastapi import Depends, Path, Query
from sc_crawler.table_fields import (
    Allocation,
    CpuArchitecture,
    StorageType,
    TrafficDirection,
)

from .helpers import get_server_dict
from .references import (
    ComplianceFrameworks,
    Countries,
    CpuFamilies,
    CpuManufacturers,
    FilterCategories,
    GpuFamilies,
    GpuManufacturers,
    GpuModels,
    OrderDir,
    Regions,
    Vendors,
)

# ##############################################################################
# Shared API query parameters

vendor = Annotated[
    Optional[List[Vendors]],
    Query(
        title="Vendor id",
        description="Identifier of the cloud provider vendor.",
        json_schema_extra={
            "category_id": FilterCategories.VENDOR,
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
            "category_id": FilterCategories.BASIC,
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
            "category_id": FilterCategories.PROCESSOR,
            "unit": "vCPUs",
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
            "category_id": FilterCategories.PROCESSOR,
            "unit": "vCPUs",
        },
    ),
]

architecture = Annotated[
    Optional[List[CpuArchitecture]],
    Query(
        title="Processor architecture",
        description="Processor architecture.",
        json_schema_extra={
            "category_id": FilterCategories.PROCESSOR,
            "enum": [e.value for e in CpuArchitecture],
        },
    ),
]

cpu_manufacturer = Annotated[
    Optional[List[CpuManufacturers]],
    Query(
        title="Processor manufacturer",
        json_schema_extra={
            "category_id": FilterCategories.PROCESSOR,
            "enum": [e.value for e in CpuManufacturers],
        },
    ),
]

cpu_family = Annotated[
    Optional[List[CpuFamilies]],
    Query(
        title="Processor family",
        json_schema_extra={
            "category_id": FilterCategories.PROCESSOR,
            "enum": [e.value for e in CpuFamilies],
        },
    ),
]

memory_min = Annotated[
    Optional[float],
    Query(
        title="Minimum memory",
        description="Minimum amount of memory in GBs.",
        json_schema_extra={
            "category_id": FilterCategories.MEMORY,
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
            "category_id": FilterCategories.PRICE,
            "step": 0.0001,
        },
    ),
]


only_active = Annotated[
    Optional[bool],
    Query(
        title="Active only",
        description="Filter for active servers only.",
        json_schema_extra={"category_id": FilterCategories.BASIC},
    ),
]

green_energy = Annotated[
    Optional[bool],
    Query(
        title="Green energy",
        description="Filter for regions with kow CO2 emission only.",
        json_schema_extra={"category_id": FilterCategories.REGION},
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
        title="Region id",
        description="Identifier of the region.",
        json_schema_extra={
            "category_id": FilterCategories.REGION,
            "enum": [m.value for m in Regions],
        },
    ),
]

compliance_framework = Annotated[
    Optional[List[ComplianceFrameworks]],
    Query(
        title="Compliance Framework id",
        description="Compliance framework implemented at the vendor.",
        json_schema_extra={
            "category_id": FilterCategories.VENDOR,
            "enum": [m.value for m in ComplianceFrameworks],
        },
    ),
]

storage_size = Annotated[
    Optional[float],
    Query(
        title="Storage Size",
        description="Minimum amount of storage (GBs).",
        json_schema_extra={
            "category_id": FilterCategories.STORAGE,
            "step": 0.1,
            "unit": "GB",
        },
    ),
]


storage_type = Annotated[
    Optional[List[StorageType]],
    Query(
        title="Storage Type",
        description="Type of the storage attached to the server.",
        json_schema_extra={
            "category_id": FilterCategories.STORAGE,
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
            "category_id": FilterCategories.TRAFFIC,
            "enum": [e.value for e in TrafficDirection],
        },
    ),
]

monthly_traffic = Annotated[
    Optional[float],
    Query(
        title="Monthly Overall Traffic",
        description="Overall amount of monthly traffic (GBs).",
        json_schema_extra={
            "category_id": FilterCategories.TRAFFIC,
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
            "category_id": FilterCategories.REGION,
            "enum": [e.value for e in Countries],
        },
    ),
]


gpu_min = Annotated[
    Optional[int],
    Query(
        title="GPU count",
        description="Minimum number of GPUs.",
        json_schema_extra={
            "category_id": FilterCategories.GPU,
            "unit": "GPUs",
        },
    ),
]

gpu_memory_min = Annotated[
    Optional[float],
    Query(
        title="Minimum GPU memory",
        description="Minimum amount of GPU memory (GB) in each GPU.",
        json_schema_extra={
            "category_id": FilterCategories.GPU,
            "unit": "GB",
            "step": 0.1,
        },
    ),
]


gpu_memory_total = Annotated[
    Optional[float],
    Query(
        title="Total GPU memory",
        description="Minimum amount of total GPU memory (GBs) in all GPUs.",
        json_schema_extra={
            "category_id": FilterCategories.GPU,
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
            "category_id": FilterCategories.GPU,
            "enum": [m.value for m in GpuManufacturers],
        },
    ),
]


gpu_family = Annotated[
    Optional[List[GpuFamilies]],
    Query(
        title="GPU family",
        json_schema_extra={
            "category_id": FilterCategories.GPU,
            "enum": [m.value for m in GpuFamilies],
        },
    ),
]


gpu_model = Annotated[
    Optional[List[GpuModels]],
    Query(
        title="GPU model",
        json_schema_extra={
            "category_id": FilterCategories.GPU,
            "enum": [m.value for m in GpuModels],
        },
    ),
]


benchmark_score_stressng_cpu_min = Annotated[
    Optional[float],
    Query(
        title="SCore",
        description="Minimum stress-ng CPU workload score.",
        json_schema_extra={
            "category_id": FilterCategories.PROCESSOR,
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
