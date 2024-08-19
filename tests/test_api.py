from time import time
from fastapi.testclient import TestClient

from sc_keeper.api import app

client = TestClient(app)


def test_healthcheck():
    response = client.get("/healthcheck")
    assert response.status_code == 200
    # updated recently
    assert response.json()["database_last_updated"] > time() - 60
    assert response.json()["database_last_updated"] < time() - 60
