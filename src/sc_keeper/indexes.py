from sc_crawler.tables import BenchmarkScore, Region, Server, ServerPrice
from sqlmodel import Index

index_defs = [
    ["server_price_idx_vendor_price", ServerPrice.vendor_id, ServerPrice.price],
    ["server_price_idx_vendor_server", ServerPrice.vendor_id, ServerPrice.server_id],
    [
        "server_price_idx_vendor_server_price",
        ServerPrice.vendor_id,
        ServerPrice.server_id,
        ServerPrice.price,
    ],
    [
        "server_price_idx_server_vendor_region",
        ServerPrice.server_id,
        ServerPrice.vendor_id,
        ServerPrice.region_id,
    ],
    [
        "server_price_idx_allocation_vendor_server",
        ServerPrice.allocation,
        ServerPrice.vendor_id,
        ServerPrice.server_id,
    ],
    ["server_idx_status_vcpus", Server.status, Server.vcpus],
    [
        "server_idx_status_server_vendor",
        Server.status,
        Server.server_id,
        Server.vendor_id,
    ],
    [
        "server_idx_status_vendor_server",
        Server.status,
        Server.vendor_id,
        Server.server_id,
    ],
    [
        "benchmark_score_idx_benchmark_vendor_server",
        BenchmarkScore.benchmark_id,
        BenchmarkScore.vendor_id,
        BenchmarkScore.server_id,
    ],
    [
        "server_price_idx_vendor_region_status",
        ServerPrice.vendor_id,
        ServerPrice.region_id,
        ServerPrice.status,
        ServerPrice.server_id,
        ServerPrice.price,
    ],
    [
        "server_price_idx_region_status",
        ServerPrice.region_id,
        ServerPrice.status,
        ServerPrice.vendor_id,
        ServerPrice.server_id,
        ServerPrice.price,
    ],
    [
        "region_idx_country",
        Region.country_id,
        Region.vendor_id,
        Region.region_id,
    ],
    [
        "server_idx_vendor_server_status",
        Server.vendor_id,
        Server.server_id,
        Server.status,
    ],
]

indexes = [Index(*index) for index in index_defs]
