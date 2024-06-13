from sc_crawler.tables import BenchmarkScore, Server
from sqlalchemy.orm import aliased
from sqlmodel import func, select


def where_benchmark_score_stressng_cpu_min(query, threshold: int):
    stress_ng_cpu_all = aliased(BenchmarkScore, name="stress_ng_cpu_all")
    query = query.join(
        stress_ng_cpu_all,
        (Server.vendor_id == stress_ng_cpu_all.vendor_id)
        & (Server.server_id == stress_ng_cpu_all.server_id)
        & (stress_ng_cpu_all.benchmark_id == "stress_ng:cpu_all"),
        isouter=True,
    )
    query = query.where(stress_ng_cpu_all.score > threshold)
    return query


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
