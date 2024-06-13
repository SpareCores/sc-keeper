from sc_crawler.tables import BenchmarkScore
from sqlalchemy.orm import aliased
from sqlmodel import func, select


def max_score_per_server():
    return aliased(
        select(
            BenchmarkScore.vendor_id,
            BenchmarkScore.server_id,
            func.max(BenchmarkScore.score).label("score"),
        )
        .where(BenchmarkScore.benchmark_id == "stress_ng:cpu_all")
        .group_by(BenchmarkScore.vendor_id, BenchmarkScore.server_id)
        .subquery(),
        name="max_score_per_server",
    )
