from time import time

import pytest
from fastapi.testclient import TestClient

from sc_keeper.api import app

client = TestClient(app)


def test_healthcheck():
    response = client.get("/healthcheck")
    assert response.status_code == 200
    # updated recently
    assert response.json()["database_last_updated"] > time() - 60
    assert response.json()["database_last_updated"] < time()


test_server_prices_params = [
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
]


@pytest.mark.parametrize("params", test_server_prices_params)
def test_server_prices_with_params(params):
    response = client.get(
        "/server_prices", params=params | {"add_total_count_header": True}
    )
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 5
    # if params is empty, this is the full count
    if params == {}:
        global count
        count = int(response.headers["x-total-count"])
    else:
        # filtered list should have fewer items than full search
        assert int(response.headers["x-total-count"]) < count


def test_server_prices_with_inactive():
    # only_active is set to True by default, so we should find more servers now
    params = {"only_active": False}
    response = client.get(
        "/server_prices", params=params | {"add_total_count_header": True}
    )
    assert int(response.headers["x-total-count"]) > count
