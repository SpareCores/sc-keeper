from typing import Optional

from sc_crawler.tables import BenchmarkScore
from sqlmodel import String, func, select


def gen_benchmark_query(benchmark_id: str, benchmark_config: Optional[str] = None):
    query = select(
        BenchmarkScore.server_id,
        BenchmarkScore.vendor_id,
        # make sure to return only one score per server
        func.max(BenchmarkScore.score).label("benchmark_score"),
    ).where(BenchmarkScore.benchmark_id == benchmark_id)
    if benchmark_config:
        query = query.where(BenchmarkScore.config.cast(String) == benchmark_config)
    query = query.group_by(BenchmarkScore.server_id, BenchmarkScore.vendor_id)
    return query.subquery()
