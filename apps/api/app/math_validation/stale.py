from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AIScoringJob,
    CriterionValidationResult,
    MathValidationJob,
    now_utc,
)


def mark_ai_jobs_stale(
    db: Session,
    jobs: Iterable[AIScoringJob],
    reason: str,
) -> int:
    timestamp = now_utc()
    changed = 0
    for job in jobs:
        if job.stale_at is not None:
            continue
        job.stale_at = timestamp
        job.status = "stale"
        job.error_code = reason
        changed += 1
    return changed


def mark_validation_jobs_stale(
    db: Session,
    jobs: Iterable[MathValidationJob],
    reason: str,
) -> int:
    timestamp = now_utc()
    changed = 0
    for job in jobs:
        if job.stale_at is not None:
            continue
        job.stale_at = timestamp
        job.status = "stale"
        job.error_code = reason
        job.generation += 1
        results = db.scalars(
            select(CriterionValidationResult).where(
                CriterionValidationResult.validation_job_id == job.id,
                CriterionValidationResult.stale_at.is_(None),
            )
        ).all()
        for result in results:
            result.stale_at = timestamp
            result.diagnostics = {
                **result.diagnostics,
                "stale_reason": reason,
            }
        changed += 1
    return changed


def stale_for_answer(db: Session, answer_id: uuid.UUID, reason: str) -> int:
    validation_count = mark_validation_jobs_stale(
        db,
        db.scalars(
            select(MathValidationJob).where(
                MathValidationJob.student_answer_id == answer_id,
                MathValidationJob.stale_at.is_(None),
            )
        ).all(),
        reason,
    )
    ai_count = mark_ai_jobs_stale(
        db,
        db.scalars(
            select(AIScoringJob).where(
                AIScoringJob.student_answer_id == answer_id,
                AIScoringJob.stale_at.is_(None),
            )
        ).all(),
        reason,
    )
    return validation_count + ai_count


def stale_for_question(db: Session, question_id: uuid.UUID, reason: str) -> int:
    validation_count = mark_validation_jobs_stale(
        db,
        db.scalars(
            select(MathValidationJob).where(
                MathValidationJob.question_id == question_id,
                MathValidationJob.stale_at.is_(None),
            )
        ).all(),
        reason,
    )
    ai_count = mark_ai_jobs_stale(
        db,
        db.scalars(
            select(AIScoringJob).where(
                AIScoringJob.question_id == question_id,
                AIScoringJob.stale_at.is_(None),
            )
        ).all(),
        reason,
    )
    return validation_count + ai_count


def stale_for_reference(db: Session, reference_answer_version_id: uuid.UUID, reason: str) -> int:
    validation_count = mark_validation_jobs_stale(
        db,
        db.scalars(
            select(MathValidationJob).where(
                MathValidationJob.reference_answer_version_id == reference_answer_version_id,
                MathValidationJob.stale_at.is_(None),
            )
        ).all(),
        reason,
    )
    ai_count = mark_ai_jobs_stale(
        db,
        db.scalars(
            select(AIScoringJob).where(
                AIScoringJob.reference_answer_version_id == reference_answer_version_id,
                AIScoringJob.stale_at.is_(None),
            )
        ).all(),
        reason,
    )
    return validation_count + ai_count


def stale_for_rubric(db: Session, rubric_version_id: uuid.UUID, reason: str) -> int:
    validation_count = mark_validation_jobs_stale(
        db,
        db.scalars(
            select(MathValidationJob).where(
                MathValidationJob.rubric_version_id == rubric_version_id,
                MathValidationJob.stale_at.is_(None),
            )
        ).all(),
        reason,
    )
    ai_count = mark_ai_jobs_stale(
        db,
        db.scalars(
            select(AIScoringJob).where(
                AIScoringJob.rubric_version_id == rubric_version_id,
                AIScoringJob.stale_at.is_(None),
            )
        ).all(),
        reason,
    )
    return validation_count + ai_count


def stale_for_engine_versions(
    db: Session,
    engine_version: str,
    config_version: str,
) -> int:
    return mark_validation_jobs_stale(
        db,
        db.scalars(
            select(MathValidationJob).where(
                (MathValidationJob.engine_version != engine_version)
                | (MathValidationJob.config_version != config_version),
                MathValidationJob.stale_at.is_(None),
            )
        ).all(),
        "VALIDATION_ENGINE_OR_CONFIG_CHANGED",
    )


def stale_for_ai_versions(
    db: Session,
    provider: str,
    model: str | None,
    prompt_version: str,
    schema_version: str,
    config_version: str,
) -> int:
    return mark_ai_jobs_stale(
        db,
        db.scalars(
            select(AIScoringJob).where(
                (
                    (AIScoringJob.provider != provider)
                    | (AIScoringJob.model != model)
                    | (AIScoringJob.prompt_version != prompt_version)
                    | (AIScoringJob.schema_version != schema_version)
                    | (AIScoringJob.grading_config_version != config_version)
                ),
                AIScoringJob.stale_at.is_(None),
            )
        ).all(),
        "AI_PROVIDER_PROMPT_SCHEMA_OR_CONFIG_CHANGED",
    )
