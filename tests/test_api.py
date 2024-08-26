from collections import ChainMap
from json import dumps
from time import time

import pytest
from fastapi.testclient import TestClient

from sc_keeper.api import app

client = TestClient(app)


def params_id_func(param):
    """Used to convert param1, param2 etc nodeids to human-readable ids."""
    return dumps(param, sort_keys=True, separators=(",", ":"))


def bool_total_header(b):
    return {"add_total_count_header": b}


def test_healthcheck():
    response = client.get("/healthcheck")
    assert response.status_code == 200
    # updated recently
    assert response.json()["database_last_updated"] > time() - 60
    assert response.json()["database_last_updated"] < time()


test_servers_params = [
    {},
    {"partial_name_or_id": "cx"},
    {"vcpus_min": 32},
    {"architecture": "x86_64"},
    {"benchmark_score_stressng_cpu_min": 5e5},
    {"memory_min": 32000},
    {"vendor": ["hcloud"]},
    {"vendor": ["hcloud", "gcp"]},
    {"compliance_framework": ["hipaa"]},
    {"storage_size": 100},
    {"storage_type": "ssd"},
    {"gpu_min": 1},
    {"gpu_memory_min": 1},
    {"gpu_memory_total": 128},
]

test_server_prices_params = test_servers_params + [
    {"price_max": 1},
    {"green_energy": True},
    {"allocation": "spot"},
    {"regions": ["us-west-2"]},
    {"countries": ["DE"]},
]
# merge some params together for more complex queries
for mix in [
    [2, 5, 6],
    [3, 4, 7, 10, 17],
    [2, 4, 6, 8, 10, 12, 14],
    list(range(1, len(test_server_prices_params))),
]:
    test_server_prices_params += [
        dict(ChainMap(*[test_server_prices_params[m] for m in mix]))
    ]


@pytest.mark.parametrize("params", test_servers_params, ids=params_id_func)
@pytest.mark.parametrize(
    "totals", [False, True], ids=lambda t: params_id_func(bool_total_header(t))
)
def test_servers_with_params(params, totals):
    response = client.get("/servers", params=params | bool_total_header(totals))
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    if totals:
        # if params is empty, this is the full count
        if params == {}:
            global count
            count = int(response.headers["x-total-count"])
        else:
            # filtered list should have fewer items than full search
            assert int(response.headers["x-total-count"]) < count


@pytest.mark.parametrize("params", test_server_prices_params, ids=params_id_func)
@pytest.mark.parametrize(
    "totals", [False, True], ids=lambda t: params_id_func(bool_total_header(t))
)
def test_server_prices_with_params(params, totals):
    response = client.get("/server_prices", params=params | bool_total_header(totals))
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 2
    if totals:
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


def test_server_v1():
    response = client.get("/server/aws/t3.nano")
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 3  # slow with duckdb
    # make sure expected fields are (not) returned
    assert response.json()["vendor_id"]
    assert response.json()["vendor"]


def test_server_v2():
    response = client.get("/v2/server/aws/t3.nano")
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    # make sure expected fields are (not) returned
    assert response.json()["vendor_id"]
    with pytest.raises(KeyError):
        assert response.json()["vendor"]


def test_server_prices():
    response = client.get("/server/aws/t3.nano/prices")
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    # make sure expected fields are (not) returned
    assert len(response.json()) > 10
    assert response.json()[1]["zone_id"]
    with pytest.raises(KeyError):
        assert response.json()[1]["zone"]


def test_server_benchmarks():
    response = client.get("/server/aws/t3.nano/benchmarks")
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    # make sure expected fields are (not) returned
    assert len(response.json()) > 10
    assert response.json()[1]["benchmark_id"]
