"""Tests for helper functions."""

from sc_keeper.helpers import get_sort_key_for_benchmark_configs


def test_benchmark_configs_category_order():
    """Benchmark categories follow the order of sc-crawler 0.10.0 categories."""
    items = [
        {
            "benchmark_id": "llm_speed:text_generation",
            "category": "LLM inference speed",
        },
        {"benchmark_id": "redis:rps", "category": "Database"},
        {"benchmark_id": "compression_text:ratio", "category": "Compression algos"},
        {"benchmark_id": "openssl", "category": "Cryptography"},
        {"benchmark_id": "unknown", "category": None},
        {"benchmark_id": "nvbandwidth:all:host_to_device", "category": "GPU bandwidth"},
    ]
    items = [item | {"original_order": i} for i, item in enumerate(items)]
    ordered = sorted(items, key=get_sort_key_for_benchmark_configs)
    assert [item["category"] for item in ordered] == [
        "Cryptography",
        "Compression algos",
        "Database",
        "LLM inference speed",
        "GPU bandwidth",
        None,
    ]
