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
    ]:
        response = client.get("/server_prices", params=params)
        assert response.status_code == 200
        assert response.elapsed.total_seconds() < 5
