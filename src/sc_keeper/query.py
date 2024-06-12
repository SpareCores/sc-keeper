from sc_crawler.tables import BenchmarkScore, Server
from sqlalchemy.orm import aliased


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
