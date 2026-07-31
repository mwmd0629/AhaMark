from __future__ import annotations

import uuid
from typing import Annotated, Any

from app.api.domain import ApiProblem
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.processing.codex_local import (
    CodexLocalProblem,
    apply_work_item,
    claim_work_items,
    submit_work_item,
    verify_internal_token,
)
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/internal/codex-local", tags=["codex-local-internal"])
Db = Annotated[Session, Depends(get_db)]


def _internal_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Settings:
    if not settings.codex_local_enabled:
        raise ApiProblem(404, "CODEX_LOCAL_DISABLED", "Codex local worker is disabled")
    token = settings.codex_local_internal_token
    if token is None:
        raise ApiProblem(404, "CODEX_LOCAL_DISABLED", "Codex local worker is disabled")
    try:
        verify_internal_token(authorization, token.get_secret_value())
    except CodexLocalProblem as exc:
        raise ApiProblem(exc.status, exc.code, exc.message, exc.details) from exc
    return settings


InternalSettings = Annotated[Settings, Depends(_internal_auth)]


class ClaimInput(BaseModel):
    worker_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    limit: int = Field(default=1, ge=1, le=100)


class SubmitInput(BaseModel):
    worker_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    lease_token: str = Field(min_length=32, max_length=256)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response: dict[str, Any]


class ApplyInput(BaseModel):
    worker_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/work-items/claim")
def claim(data: ClaimInput, db: Db, settings: InternalSettings) -> dict[str, Any]:
    try:
        items = claim_work_items(
            db,
            worker_id=data.worker_id,
            limit=min(data.limit, settings.codex_local_max_claim),
            lease_seconds=settings.codex_local_lease_seconds,
        )
    except CodexLocalProblem as exc:
        db.rollback()
        raise ApiProblem(exc.status, exc.code, exc.message, exc.details) from exc
    return {"items": items, "count": len(items)}


@router.post("/work-items/{item_id}/submit")
def submit(
    item_id: uuid.UUID,
    data: SubmitInput,
    db: Db,
    _settings: InternalSettings,
) -> dict[str, Any]:
    try:
        item = submit_work_item(
            db,
            item_id=item_id,
            worker_id=data.worker_id,
            lease_token=data.lease_token,
            request_hash=data.request_hash,
            response=data.response,
        )
    except CodexLocalProblem as exc:
        if exc.code != "CODEX_RESPONSE_INVALID":
            db.rollback()
        raise ApiProblem(exc.status, exc.code, exc.message, exc.details) from exc
    return {
        "work_item_id": str(item.id),
        "status": item.status,
        "request_hash": item.request_hash,
        "response_hash": item.response_hash,
        "submitted_at": item.submitted_at.isoformat() if item.submitted_at else None,
        "suggestion_only": True,
    }


@router.post("/work-items/{item_id}/apply")
def apply(
    item_id: uuid.UUID,
    data: ApplyInput,
    db: Db,
    _settings: InternalSettings,
) -> dict[str, Any]:
    try:
        item = apply_work_item(
            db,
            item_id=item_id,
            worker_id=data.worker_id,
            request_hash=data.request_hash,
            response_hash=data.response_hash,
        )
    except CodexLocalProblem as exc:
        if exc.code != "CODEX_WORK_INPUT_STALE":
            db.rollback()
        raise ApiProblem(exc.status, exc.code, exc.message, exc.details) from exc
    return {
        "work_item_id": str(item.id),
        "status": item.status,
        "request_hash": item.request_hash,
        "response_hash": item.response_hash,
        "grading_job_id": str(item.grading_job_id) if item.grading_job_id else None,
        "grading_result_id": str(item.grading_result_id) if item.grading_result_id else None,
        "applied_at": item.applied_at.isoformat() if item.applied_at else None,
        "provider": "codex_local",
        "provider_label": "Codex-assisted",
        "suggestion_only": True,
    }
