"""Bounded dependency readiness checks; health remains process-liveness only."""

from __future__ import annotations

from typing import Any
from urllib.request import Request, urlopen

from app.assignment_generation.providers import select_provider as select_assignment_provider
from app.core.config import Settings
from app.recognition.formula import HttpFormulaProvider, formula_provider_from_settings
from app.recognition.pipeline import provider_from_settings, safe_provider_readiness
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


def dependency_readiness(db: Session, settings: Settings) -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = {}
    try:
        if settings.app_env == "test":
            db.execute(text("SELECT 1"))
        else:
            probe_engine = create_engine(
                settings.database_url,
                connect_args={"connect_timeout": 1},
                poolclass=NullPool,
            )
            try:
                with probe_engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
            finally:
                probe_engine.dispose()
        components["postgresql"] = {"status": "available", "hard_dependency": True}
    except Exception:
        components["postgresql"] = {"status": "unavailable", "hard_dependency": True}
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=0.35, socket_timeout=0.35).ping()
        components["redis"] = {"status": "available", "hard_dependency": True}
    except Exception:
        components["redis"] = {"status": "unavailable", "hard_dependency": True}
    scheme = "https" if settings.minio_secure else "http"
    try:
        with urlopen(
            f"{scheme}://{settings.minio_endpoint}/minio/health/ready", timeout=0.5
        ) as response:
            available = response.status == 200
        components["minio"] = {
            "status": "available" if available else "unavailable",
            "hard_dependency": True,
        }
    except Exception:
        components["minio"] = {"status": "unavailable", "hard_dependency": True}
    try:
        if components["redis"]["status"] != "available":
            raise RuntimeError("worker broker dependency unavailable")
        from workers.celery_app import celery_app

        replies = celery_app.control.inspect(timeout=0.5).ping()
        components["celery_worker"] = {
            "status": "available" if replies else "degraded",
            "workers": len(replies or {}),
            "hard_dependency": False,
        }
    except Exception:
        components["celery_worker"] = {"status": "degraded", "hard_dependency": False}
    assignment_provider = select_assignment_provider(settings)
    components["assignment_generation_provider"] = {
        "status": "available" if assignment_provider.available else "unavailable",
        "provider": assignment_provider.name,
        "hard_dependency": False,
        "suggestion_only": True,
    }
    recognition_provider = provider_from_settings(settings)
    recognition_available, _ = safe_provider_readiness(recognition_provider)
    ocr_status = (
        "degraded"
        if recognition_available and recognition_provider.is_demo
        else ("available" if recognition_available else "unavailable")
    )
    components["text_ocr"] = {
        "status": ocr_status,
        "hard_dependency": False,
    }
    formula_provider = formula_provider_from_settings(settings)
    formula_available, _ = formula_provider.available()
    formula_status = "unavailable"
    if formula_available and isinstance(formula_provider, HttpFormulaProvider):
        token = settings.formula_recognition_api_key
        base_url = settings.formula_recognition_base_url
        assert token is not None and base_url is not None
        request = Request(
            base_url.rstrip("/") + "/ready",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        )
        try:
            with urlopen(
                request,
                timeout=min(settings.formula_recognition_timeout_seconds, 1.0),
            ) as response:
                formula_status = "available" if response.status == 200 else "unavailable"
        except Exception:
            formula_status = "unavailable"
    elif formula_available:
        formula_status = "degraded"
    components["formula_ocr"] = {
        "status": formula_status,
        "provider": formula_provider.name,
        "hard_dependency": False,
        "human_confirmation_required": True,
    }
    hard_ready = all(
        component["status"] == "available"
        for component in components.values()
        if component["hard_dependency"]
    )
    return {
        "ready": hard_ready,
        "status": "available" if hard_ready else "unavailable",
        "components": components,
    }
