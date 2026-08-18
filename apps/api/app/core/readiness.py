"""Bounded dependency readiness checks; health remains process-liveness only."""

from __future__ import annotations

from typing import Any
from urllib.request import Request, urlopen

from app.assignment_generation.providers import select_provider as select_assignment_provider
from app.core.config import Settings
from app.core.provider_endpoints import ProviderEndpointError, safe_provider_base_url
from app.recognition.formula import HttpFormulaProvider, formula_provider_from_settings
from app.recognition.pipeline import provider_from_settings, safe_provider_readiness
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


def _local_model_available(
    *,
    base_url: str | None,
    api_key: str | None,
    allow_local: bool,
    allowed_hosts: list[str],
) -> bool:
    if not base_url or not api_key or not allow_local or not allowed_hosts:
        return False
    try:
        endpoint = safe_provider_base_url(
            base_url,
            allow_external_https=False,
            allow_local_http=True,
            allowed_local_hosts=allowed_hosts,
        )
        request = Request(
            endpoint.removesuffix("/v1") + "/health",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urlopen(request, timeout=1.0) as response:
            return int(response.status) == 200
    except (ProviderEndpointError, OSError, TimeoutError):
        return False


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
    assignment_available = assignment_provider.available
    if assignment_provider.name == "local_openai_compatible":
        assignment_available = _local_model_available(
            base_url=settings.assignment_generation_base_url,
            api_key=settings.assignment_generation_api_key,
            allow_local=settings.assignment_generation_allow_local_provider_requests,
            allowed_hosts=settings.assignment_generation_allowed_local_hosts,
        )
    components["assignment_generation_provider"] = {
        "status": "available" if assignment_available else "unavailable",
        "provider": assignment_provider.name,
        "hard_dependency": False,
        "suggestion_only": True,
    }
    grading_available = False
    if settings.grading_provider.lower() == "local_openai_compatible":
        grading_available = _local_model_available(
            base_url=settings.grading_base_url,
            api_key=settings.grading_api_key,
            allow_local=settings.grading_allow_local_provider_requests,
            allowed_hosts=settings.grading_allowed_local_hosts,
        )
    components["subjective_grading_provider"] = {
        "status": "available" if grading_available else "unavailable",
        "provider": settings.grading_provider.lower(),
        "hard_dependency": False,
        "suggestion_only": True,
        "human_confirmation_required": True,
    }
    ai_grading_available = False
    if settings.ai_grading_provider.lower() == "local_openai_compatible":
        ai_grading_available = _local_model_available(
            base_url=settings.ai_grading_base_url,
            api_key=settings.ai_grading_api_key,
            allow_local=settings.ai_grading_allow_local_provider_requests,
            allowed_hosts=settings.ai_grading_allowed_local_hosts,
        )
    components["ai_grading_provider"] = {
        "status": "available" if ai_grading_available else "unavailable",
        "provider": settings.ai_grading_provider.lower(),
        "hard_dependency": False,
        "suggestion_only": True,
        "human_confirmation_required": True,
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
