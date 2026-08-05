import hashlib
import json
import time
import uuid
from decimal import Decimal
from typing import Any

from app.db.session import SessionLocal
from app.math_validation.engine import ENGINE_VERSION, Limits, validate
from app.models import (
    Assignment,
    CriterionValidationResult,
    MathValidationJob,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    Submission,
    now_utc,
)
from app.structured_rubric_authority import (
    StructuredRubricAuthorityError,
    require_active_structured_rubric,
    require_job_authority,
)
from sqlalchemy import select

from workers.celery_app import celery_app


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@celery_app.task(
    name="ahamark.math_validation.run",
    bind=True,
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=60,
    time_limit=75,
)
def run_math_validation(
    self: Any, job_id: str, generation: int, criterion_id: str | None = None
) -> dict[str, Any]:
    with SessionLocal() as db:
        job = db.scalar(
            select(MathValidationJob)
            .where(MathValidationJob.id == uuid.UUID(job_id))
            .with_for_update()
        )
        if job is None or job.generation != generation or job.stale_at is not None:
            return {"status": "discarded_late"}
        assignment = db.scalar(
            select(Assignment)
            .join_from(Assignment, Submission, Submission.assignment_id == Assignment.id)
            .where(Submission.id == job.submission_id)
        )
        try:
            if assignment is None:
                raise StructuredRubricAuthorityError(
                    "STRUCTURED_SET_STALE", "Assignment is unavailable"
                )
            authority = require_active_structured_rubric(
                db,
                assignment=assignment,
                question_id=job.question_id,
                owner_id=job.owner_id,
                lock=True,
            )
            require_job_authority(
                authority,
                structured_rubric_set_id=job.structured_rubric_set_id,
                rubric_version_id=job.rubric_version_id,
                reference_answer_version_id=job.reference_answer_version_id,
            )
        except StructuredRubricAuthorityError as exc:
            job.status, job.stale_at, job.error_code = "stale", now_utc(), exc.code
            db.commit()
            return {"status": "stale"}
        evidence = db.get(QuestionRecognitionEvidence, job.recognition_evidence_id)
        rubric = db.get(StructuredRubricVersion, job.rubric_version_id)
        reference = db.get(ReferenceAnswerVersion, job.reference_answer_version_id)
        current_input_hash = (
            _hash(
                {
                    "evidence": evidence.input_hash,
                    "rubric": rubric.content_hash,
                    "reference": reference.content_hash,
                    "engine": ENGINE_VERSION,
                }
            )
            if evidence is not None and rubric is not None and reference is not None
            else None
        )
        if (
            evidence is None
            or rubric is None
            or reference is None
            or evidence.stale_at is not None
            or evidence.input_hash not in job.scoring_input_version
            or current_input_hash != job.input_hash
            or job.engine_version != ENGINE_VERSION
        ):
            job.status, job.stale_at = "stale", now_utc()
            db.commit()
            return {"status": "stale"}
        job.status, job.started_at = "running", now_utc()
        db.commit()
        criteria_query = select(RubricCriterion).where(
            RubricCriterion.rubric_version_id == rubric.id
        )
        if criterion_id:
            criteria_query = criteria_query.where(RubricCriterion.id == uuid.UUID(criterion_id))
        criteria = db.scalars(criteria_query.order_by(RubricCriterion.display_order)).all()
        student_values = (
            evidence.block_sources[0].get("structured_values", {}) if evidence.block_sources else {}
        )
        expected_values = reference.structured_content.get("criteria", {})
        for criterion in criteria:
            started = time.monotonic()
            if criterion.validation_mode == "manual_only" or evidence.requires_review:
                outcome = validate({"answer_type": "manual_only", "domain": "rational"}, None, None)
            else:
                config = criterion.validation_rule
                raw_limits = config.get("limits", {})
                allowed = {
                    key: value
                    for key, value in raw_limits.items()
                    if key in Limits.__dataclass_fields__
                }
                outcome = validate(
                    config,
                    student_values.get(criterion.stable_key),
                    expected_values.get(criterion.stable_key),
                    Limits(**allowed),
                )
            suggested = (
                Decimal(criterion.max_points)
                if outcome.result == "verified_pass"
                else (Decimal("0") if outcome.result == "verified_fail" else None)
            )
            data = {
                "result": outcome.result,
                "reason": outcome.reason,
                "method": outcome.comparison_method,
                "evidence": outcome.evidence,
            }
            db.add(
                CriterionValidationResult(
                    validation_job_id=job.id,
                    criterion_id=criterion.id,
                    generation=generation,
                    result=outcome.result,
                    suggested_points=suggested,
                    confidence=Decimal("1") if outcome.result.startswith("verified_") else None,
                    normalized_student_input={"value": student_values.get(criterion.stable_key)},
                    normalized_expected_input={"value": expected_values.get(criterion.stable_key)},
                    assumptions=criterion.validation_rule.get("assumptions", {}),
                    comparison_method=outcome.comparison_method,
                    evidence=outcome.evidence,
                    diagnostics={"reason": outcome.reason, **outcome.diagnostics},
                    input_hash=_hash(
                        [
                            student_values.get(criterion.stable_key),
                            expected_values.get(criterion.stable_key),
                            criterion.validation_rule,
                        ]
                    ),
                    output_hash=_hash(data),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    engine_version=ENGINE_VERSION,
                )
            )
        current = db.get(MathValidationJob, job.id)
        if current is None or current.generation != generation or current.stale_at is not None:
            db.rollback()
            return {"status": "discarded_late"}
        try:
            require_job_authority(
                require_active_structured_rubric(
                    db,
                    assignment=assignment,
                    question_id=current.question_id,
                    owner_id=current.owner_id,
                    lock=True,
                ),
                structured_rubric_set_id=current.structured_rubric_set_id,
                rubric_version_id=current.rubric_version_id,
                reference_answer_version_id=current.reference_answer_version_id,
            )
        except StructuredRubricAuthorityError as exc:
            db.rollback()
            current = db.get(MathValidationJob, job.id)
            if current is not None:
                current.status, current.stale_at, current.error_code = "stale", now_utc(), exc.code
                db.commit()
            return {"status": "stale"}
        current.status, current.completed_at = "completed", now_utc()
        db.commit()
        return {"status": "completed", "job_id": job_id, "generation": generation}
