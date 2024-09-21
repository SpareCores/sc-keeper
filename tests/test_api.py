from collections import ChainMap
from json import dumps
from statistics import stdev
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


test_general_params = [
    {},
    {"vendor": ["hcloud"]},
    {"vendor": ["hcloud", "gcp"]},
]

test_region_params = [
    {"green_energy": True},
    {"regions": ["us-west-2"]},
    {"countries": ["DE"]},
]

test_servers_params = [
    *test_general_params,
    {"partial_name_or_id": "cx"},
    {"vcpus_min": 32},
    {"architecture": "x86_64"},
    {"benchmark_score_stressng_cpu_min": 5e5},
    {"memory_min": 32000},
    {"compliance_framework": ["hipaa"]},
    {"storage_size": 100},
    {"storage_type": "ssd"},
    {"gpu_min": 1},
    {"gpu_memory_min": 1},
    {"gpu_memory_total": 128},
    {"gpu_manufacturer": ["NVIDIA"]},
    {"gpu_manufacturer": ["NVIDIA", "AMD"]},
    {"gpu_family": ["Turing"]},
    {"gpu_model": ["A100"]},
]

test_server_prices_params = test_servers_params + [
    {"price_max": 1},
    {"allocation": "spot"},
    *test_region_params,
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

test_storage_prices_params = [
    *test_general_params,
    *test_region_params,
    {"storage_type": ["ssd"]},
    {"storage_min": 100},
]

test_traffic_prices_params = [
    {"direction": ["inbound", "outbound"]},
    *test_general_params,
    *test_region_params,
    {"direction": ["inbound"]},
]


@pytest.mark.parametrize(
    "totals", [False, True], ids=lambda t: params_id_func(bool_total_header(t))
)
@pytest.mark.parametrize("params", test_servers_params, ids=params_id_func)
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


@pytest.mark.parametrize(
    "totals", [False, True], ids=lambda t: params_id_func(bool_total_header(t))
)
@pytest.mark.parametrize("params", test_server_prices_params, ids=params_id_func)
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


@pytest.mark.parametrize("currency", [None, "USD", "EUR"])
def test_server_prices(currency):
    response = client.get(
        "/server/aws/t3.nano/prices", params={"currency": currency} if currency else {}
    )
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    # make sure expected fields are (not) returned
    assert len(response.json()) > 10
    assert response.json()[1]["zone_id"]
    with pytest.raises(KeyError):
        assert response.json()[1]["zone"]
    if currency:
        assert response.json()[1]["currency"] == currency


def test_server_benchmarks():
    response = client.get("/server/aws/t3.nano/benchmarks")
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    # make sure expected fields are (not) returned
    assert len(response.json()) > 10
    assert response.json()[1]["benchmark_id"]


def test_server_similar_family():
    response = client.get("/server/aws/t3.nano/similar_servers/family/3")
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    # make sure expected fields are (not) returned
    data = response.json()
    assert len(data) == 3
    for i in range(2):
        assert data[i]["server_id"]
        assert data[i]["vendor_id"] == data[2]["vendor_id"]
        assert data[i]["family"] == data[2]["family"]
        assert data[i]["server_id"] != data[2]["server_id"]


def test_server_similar_specs():
    response = client.get("/server/aws/t3.nano/similar_servers/specs/5")
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    # make sure expected fields are (not) returned
    data = response.json()
    assert len(data) == 5
    for i in range(5):
        assert data[i]["server_id"]


def test_server_similar_score():
    response = client.get("/server/aws/t3.nano/similar_servers/score/10")
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 1
    # make sure expected fields are (not) returned
    data = response.json()
    assert len(data) == 10
    for i in range(10):
        assert data[i]["server_id"]
        assert data[i]["score"]
    assert stdev([data[i]["score"] for i in range(10)]) < 100


@pytest.mark.parametrize("params", test_storage_prices_params)
def test_storage_prices_with_params(params):
    response = client.get("/storage_prices", params=params | {"limit": -1})
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 5
    # if params is empty, this is the full count
    if params == {}:
        global count
        count = len(response.json())
    else:
        # filtered list should have fewer items than full search
        assert len(response.json()) < count


@pytest.mark.parametrize("params", test_traffic_prices_params)
def test_traffic_prices_with_params(params):
    response = client.get("/traffic_prices", params=params | {"limit": -1})
    # expect OK status within a reasonable time
    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 5
    # if params is empty, this is the full count
    if params == {"direction": ["inbound", "outbound"]}:
        global count
        count = len(response.json())
    else:
        # filtered list should have fewer items than full search
        assert len(response.json()) < count
