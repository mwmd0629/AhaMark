from types import SimpleNamespace

import pytest
from app.core.config import Settings
from app.core.readiness import dependency_readiness


class FakeDb:
    def __init__(self, available: bool = True):
        self.available = available

    def execute(self, _query: object) -> None:
        if not self.available:
            raise RuntimeError("secret database detail")


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeInspect:
    def ping(self) -> dict[str, object]:
        return {"worker@test": {"ok": "pong"}}


def configure(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(
        "app.core.readiness.Redis.from_url",
        lambda *_args, **_kwargs: SimpleNamespace(ping=lambda: True),
    )
    monkeypatch.setattr("app.core.readiness.urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(
        "workers.celery_app.celery_app.control.inspect", lambda **_kwargs: FakeInspect()
    )
    return Settings(
        app_env="test",
        recognition_provider="fake",
        assignment_generation_provider="unavailable",
    )


def test_all_hard_dependencies_healthy_and_provider_unavailable_is_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = dependency_readiness(FakeDb(), configure(monkeypatch))
    assert result["ready"] is True
    assert result["components"]["assignment_generation_provider"] == {
        "status": "unavailable",
        "provider": "unavailable",
        "hard_dependency": False,
        "suggestion_only": True,
    }


@pytest.mark.parametrize("failed", ["postgresql", "redis", "minio"])
def test_each_hard_dependency_failure_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    settings = configure(monkeypatch)
    db = FakeDb(available=failed != "postgresql")
    if failed == "redis":
        monkeypatch.setattr(
            "app.core.readiness.Redis.from_url",
            lambda *_args, **_kwargs: SimpleNamespace(
                ping=lambda: (_ for _ in ()).throw(TimeoutError())
            ),
        )
    if failed == "minio":
        monkeypatch.setattr(
            "app.core.readiness.urlopen",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
        )
    result = dependency_readiness(db, settings)
    assert result["ready"] is False
    assert result["components"][failed]["status"] == "unavailable"
    assert "secret" not in str(result).lower()


def test_worker_is_soft_degraded_component(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = configure(monkeypatch)
    monkeypatch.setattr(
        "workers.celery_app.celery_app.control.inspect",
        lambda **_kwargs: SimpleNamespace(ping=lambda: None),
    )
    result = dependency_readiness(FakeDb(), settings)
    assert result["ready"] is True
    assert result["components"]["celery_worker"]["status"] == "degraded"


def test_compose_uses_ready_and_nginx_retries_503() -> None:
    from pathlib import Path

    compose = (Path(__file__).parents[1] / "docker-compose.preproduction.yml").read_text(
        encoding="utf-8"
    )
    nginx = (Path(__file__).parents[1] / "deploy/nginx/preproduction.conf").read_text(
        encoding="utf-8"
    )
    assert "localhost:8000/ready" in compose
    assert "proxy_next_upstream error timeout http_502 http_503 http_504" in nginx
