from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_endpoint_returns_foundations_placeholder() -> None:
    response = client.get("/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "foundations"
    assert payload["ready"] is False
