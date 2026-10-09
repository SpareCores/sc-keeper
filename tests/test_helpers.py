"""Tests for helper functions."""

from sc_keeper.helpers import get_sort_key_for_benchmark_configs


def _sorted(items):
    items = [item | {"original_order": i} for i, item in enumerate(items)]
    return sorted(items, key=get_sort_key_for_benchmark_configs)


def test_benchmark_configs_category_order():
    """Listed categories first, then unlisted ones alphabetically, "Other" last."""
    items = [
        {
            "benchmark_id": "llm_speed:text_generation",
            "category": "LLM inference speed",
        },
        {"benchmark_id": "unknown", "category": None},
        {"benchmark_id": "foo", "category": "Unlisted B"},
        {"benchmark_id": "redis:rps", "category": "Database"},
        {"benchmark_id": "bar", "category": "Unlisted A"},
        {"benchmark_id": "compression_text:ratio", "category": "Compression algos"},
        {"benchmark_id": "openssl", "category": "Cryptography"},
    ]
    assert [item["category"] for item in _sorted(items)] == [
        "Cryptography",
        "Compression algos",
        "Database",
        "LLM inference speed",
        "Unlisted A",
        "Unlisted B",
        None,
    ]


def test_benchmark_configs_subcategory_and_benchmark_order():
    """Subcategories group benchmarks, main scores come first within them."""
    items = [
        {"benchmark_id": "passmark:memory_read_cached", "subcategory": "Memory"},
        {"benchmark_id": "passmark:cpu_prime_numbers_test", "subcategory": "CPU"},
        {"benchmark_id": "passmark:memory_mark", "subcategory": "Memory"},
        {"benchmark_id": "passmark:cpu_mark", "subcategory": "CPU"},
        {"benchmark_id": "passmark:cpu_compression_test", "subcategory": "CPU"},
    ]
    items = [item | {"category": "Passmark"} for item in items]
    assert [item["benchmark_id"] for item in _sorted(items)] == [
        "passmark:cpu_mark",
        "passmark:cpu_compression_test",
        "passmark:cpu_prime_numbers_test",
        "passmark:memory_mark",
        "passmark:memory_read_cached",
    ]


def test_benchmark_configs_config_order_within_benchmark():
    """Configs of the same benchmark keep the config based ordering."""
    items = [
        {"benchmark_id": "pgbench:heavy_read_only", "config": {"concurrency": "peak"}},
        {"benchmark_id": "openssl", "config": {"algo": "sha256", "block_size": 16}},
        {"benchmark_id": "openssl", "config": {"algo": "md5", "block_size": 1024}},
        {"benchmark_id": "openssl", "config": {"algo": "md5", "block_size": 16}},
        {
            "benchmark_id": "pgbench:heavy_read_only",
            "config": {"concurrency": "single"},
        },
    ]
    assert [(item["benchmark_id"], item["config"]) for item in _sorted(items)] == [
        ("openssl", {"algo": "md5", "block_size": 16}),
        ("openssl", {"algo": "md5", "block_size": 1024}),
        ("openssl", {"algo": "sha256", "block_size": 16}),
        ("pgbench:heavy_read_only", {"concurrency": "single"}),
        ("pgbench:heavy_read_only", {"concurrency": "peak"}),
    ]
