from app.main import app
from fastapi.testclient import TestClient


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


def test_ready_with_sqlite() -> None:
    assert TestClient(app).get("/ready").status_code == 200


def test_error_shape() -> None:
    response = TestClient(app).post(
        "/files", files={"file": ("bad.exe", b"x", "application/octet-stream")}
    )
    body = response.json()
    assert response.status_code == 415
    assert set(body) == {"code", "message", "details", "request_id"}
    assert body["request_id"] == response.headers["x-request-id"]
