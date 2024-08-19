from time import time

from fastapi.testclient import TestClient

from sc_keeper.api import app

client = TestClient(app)


def test_healthcheck():
    response = client.get("/healthcheck")
    assert response.status_code == 200
    # updated recently
    assert response.json()["database_last_updated"] > time() - 60
    assert response.json()["database_last_updated"] < time()


def test_server_prices():
    for params in [
        {},
        {"partial_name_or_id": "cx"},
        {"vcpus_min": 32},
        {"architecture": "x86_64"},
        {"benchmark_score_stressng_cpu_min": 5e5},
        {"memory_min": 32000},
        {"price_max": 1},
        {"green_energy": True},
        {"allocation": "spot"},
        {"vendor": ["hcloud"]},
        {"vendor": ["hcloud", "gcp"]},
        {"regions": ["us-west-2"]},
        {"compliance_framework": ["hipaa"]},
        {"storage_size": 100},
        {"storage_type": "ssd"},
        {"countries": ["DE"]},
        {"gpu_min": 1},
        {"gpu_memory_min": 1},
        {"gpu_memory_total": 128},
    ]:
        response = client.get(
            "/server_prices", params=params | {"add_total_count_header": True}
        )
        print(params, int(response.headers["x-total-count"]))  # QQ
        # expect OK status within a reasonable time
        assert response.status_code == 200
        assert response.elapsed.total_seconds() < 5
        # filtered list should have fewer items than full search
        if not params:
            count = int(response.headers["x-total-count"])
        else:
            assert int(response.headers["x-total-count"]) < count
    # only active is set to True by default, so we should find more servers now
    params = {"only_active": False}
    response = client.get(
        "/server_prices", params=params | {"add_total_count_header": True}
    )
    assert int(response.headers["x-total-count"]) > count
