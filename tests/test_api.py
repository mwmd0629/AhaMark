from app.main import app
from fastapi.testclient import TestClient


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


def test_ready_with_healthy_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.dependency_readiness",
        lambda *_args: {"ready": True, "status": "available", "components": {}},
    )
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_error_shape() -> None:
    response = TestClient(app).post(
        "/files", files={"file": ("bad.exe", b"x", "application/octet-stream")}
    )
    body = response.json()
    assert response.status_code == 415
    assert set(body) == {"code", "message", "details", "request_id"}
    assert body["request_id"] == response.headers["x-request-id"]
