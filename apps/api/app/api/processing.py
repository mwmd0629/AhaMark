from __future__ import annotations

import uuid
from typing import Annotated, Any

from app.api.actor import Actor
from app.api.domain import ApiProblem
from app.db.session import get_db
from app.processing.orchestrator import (
    OrchestratorProblem,
    continue_processing,
    get_latest_processing_run,
    get_processing_run,
    processing_run_json,
    reconcile_processing,
    retry_processing,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/grading-batches", tags=["processing"])
Db = Annotated[Session, Depends(get_db)]


class CommandInput(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def normalized_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency_key must not be blank")
        return normalized


class RetryInput(CommandInput):
    expected_generation: int = Field(gt=0)
    step_ids: list[uuid.UUID] = Field(min_length=1)

    @field_validator("step_ids")
    @classmethod
    def unique_steps(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("step_ids must be unique")
        return sorted(value, key=str)


class ReconcileInput(CommandInput):
    expected_generation: int = Field(gt=0)


def _translate(exc: OrchestratorProblem) -> ApiProblem:
    return ApiProblem(exc.status, exc.code, exc.message, exc.details)


@router.post("/{batch_id}/processing-runs", status_code=201)
def continue_batch(batch_id: uuid.UUID, data: CommandInput, db: Db, actor: Actor) -> dict[str, Any]:
    try:
        run = continue_processing(
            db,
            owner_id=actor.id,
            batch_id=batch_id,
            idempotency_key=data.idempotency_key,
        )
        return processing_run_json(db, run)
    except OrchestratorProblem as exc:
        db.rollback()
        raise _translate(exc) from exc


@router.get("/{batch_id}/processing-runs/latest")
def get_latest_run(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any] | None:
    try:
        run = get_latest_processing_run(db, owner_id=actor.id, batch_id=batch_id)
        return processing_run_json(db, run) if run is not None else None
    except OrchestratorProblem as exc:
        raise _translate(exc) from exc


@router.get("/{batch_id}/processing-runs/{run_id}")
def get_run(batch_id: uuid.UUID, run_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    try:
        return processing_run_json(
            db,
            get_processing_run(db, owner_id=actor.id, batch_id=batch_id, run_id=run_id),
        )
    except OrchestratorProblem as exc:
        raise _translate(exc) from exc


@router.post("/{batch_id}/processing-runs/{run_id}/retry", status_code=201)
def retry_run(
    batch_id: uuid.UUID,
    run_id: uuid.UUID,
    data: RetryInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    try:
        run = retry_processing(
            db,
            owner_id=actor.id,
            batch_id=batch_id,
            source_run_id=run_id,
            idempotency_key=data.idempotency_key,
            expected_generation=data.expected_generation,
            step_ids=data.step_ids,
        )
        return processing_run_json(db, run)
    except OrchestratorProblem as exc:
        db.rollback()
        raise _translate(exc) from exc


@router.post("/{batch_id}/processing-runs/{run_id}/reconcile")
def reconcile_run(
    batch_id: uuid.UUID,
    run_id: uuid.UUID,
    data: ReconcileInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    try:
        run = reconcile_processing(
            db,
            owner_id=actor.id,
            batch_id=batch_id,
            run_id=run_id,
            idempotency_key=data.idempotency_key,
            expected_generation=data.expected_generation,
        )
        return processing_run_json(db, run)
    except OrchestratorProblem as exc:
        db.rollback()
        raise _translate(exc) from exc
