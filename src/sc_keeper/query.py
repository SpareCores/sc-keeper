import json

from sc_crawler.tables import BenchmarkScore
from sqlalchemy.orm import aliased
from sqlmodel import func, select


def max_score_per_server(
    benchmark_id: str = "stress_ng:cpu_all", benchmark_config: str = ""
):
    query = select(
        BenchmarkScore.vendor_id,
        BenchmarkScore.server_id,
        func.max(BenchmarkScore.score).label("score"),
    ).where(BenchmarkScore.benchmark_id == benchmark_id)
    if benchmark_config:
        query = query.where(BenchmarkScore.config == json.loads(benchmark_config))
    return aliased(
        query.group_by(BenchmarkScore.vendor_id, BenchmarkScore.server_id).subquery(),
        name="max_score_per_server",
    )
