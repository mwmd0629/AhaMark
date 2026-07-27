import hashlib
import json
import uuid
from typing import Annotated, Any

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.core.request_id import celery_request_headers
from app.db.session import get_db
from app.math_validation.engine import ENGINE_VERSION
from app.math_validation.stale import stale_for_engine_versions
from app.models import (
    CriterionValidationResult,
    MathValidationJob,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    StudentAnswer,
    Submission,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["math-validation"])
Db = Annotated[Session, Depends(get_db)]
CONFIG_VERSION = "safe-math-limits-v2"


class ValidationInput(BaseModel):
    student_answer_id: uuid.UUID
    rubric_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)


def _job_json(db: Session, job: MathValidationJob) -> dict[str, Any]:
    results = db.scalars(
        select(CriterionValidationResult)
        .where(
            CriterionValidationResult.validation_job_id == job.id,
            CriterionValidationResult.generation == job.generation,
        )
        .order_by(CriterionValidationResult.created_at)
    ).all()
    return {
        "id": str(job.id),
        "submission_id": str(job.submission_id),
        "question_id": str(job.question_id),
        "student_answer_id": str(job.student_answer_id),
        "scoring_input_version": job.scoring_input_version,
        "rubric_version_id": str(job.rubric_version_id),
        "reference_answer_version_id": str(job.reference_answer_version_id),
        "engine": job.engine,
        "engine_version": job.engine_version,
        "config_version": job.config_version,
        "task_id": job.celery_task_id,
        "status": job.status,
        "generation": job.generation,
        "stale": job.stale_at is not None,
        "results": [
            {
                "id": str(result.id),
                "criterion_id": str(result.criterion_id),
                "result": result.result,
                "suggested_points": (
                    str(result.suggested_points) if result.suggested_points is not None else None
                ),
                "confidence": str(result.confidence) if result.confidence is not None else None,
                "comparison_method": result.comparison_method,
                "evidence": result.evidence,
                "diagnostics": result.diagnostics,
                "input_hash": result.input_hash,
                "output_hash": result.output_hash,
                "duration_ms": result.duration_ms,
                "stale": result.stale_at is not None,
            }
            for result in results
        ],
        "suggested_total": str(
            sum(
                (
                    result.suggested_points
                    for result in results
                    if result.suggested_points is not None
                    and result.stale_at is None
                    and job.stale_at is None
                ),
                start=0,
            )
        ),
    }


@router.post("/math-validation/jobs", status_code=202)
def create_validation_job(payload: ValidationInput, db: Db, actor: Actor) -> dict[str, Any]:
    stale_for_engine_versions(db, ENGINE_VERSION, CONFIG_VERSION)
    existing = db.scalar(
        select(MathValidationJob).where(
            MathValidationJob.owner_id == actor.id,
            MathValidationJob.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        return _job_json(db, existing)
    answer = db.get(StudentAnswer, payload.student_answer_id)
    rubric = db.get(StructuredRubricVersion, payload.rubric_version_id)
    if answer is None or rubric is None or rubric.question_id != answer.question_id:
        raise ApiProblem(404, "VALIDATION_INPUT_NOT_FOUND", "学生答案或 Rubric 不存在")
    submission = db.get(Submission, answer.submission_id)
    if submission is None or submission.owner_id != actor.id:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "提交不存在")
    if submission.finalized_at is not None:
        raise ApiProblem(409, "FINALIZED_READ_ONLY", "已定稿提交只读")
    if rubric.status != "confirmed":
        raise ApiProblem(422, "RUBRIC_NOT_CONFIRMED", "只能使用已确认 Rubric")
    evidence = db.scalar(
        select(QuestionRecognitionEvidence)
        .where(
            QuestionRecognitionEvidence.student_answer_id == answer.id,
            QuestionRecognitionEvidence.status == "confirmed",
            QuestionRecognitionEvidence.stale_at.is_(None),
        )
        .order_by(QuestionRecognitionEvidence.recognition_version.desc())
    )
    if evidence is None:
        raise ApiProblem(422, "RECOGNITION_NOT_CONFIRMED", "缺少当前已确认识别证据")
    reference = db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
    if reference is None or reference.status != "confirmed":
        raise ApiProblem(422, "REFERENCE_NOT_CONFIRMED", "标准答案未确认")
    scoring_version = (
        f"{evidence.input_hash}:{evidence.recognition_version}:{evidence.confirmed_revision or 0}"
    )
    input_hash = hashlib.sha256(
        json.dumps(
            {
                "evidence": evidence.input_hash,
                "rubric": rubric.content_hash,
                "reference": reference.content_hash,
                "engine": ENGINE_VERSION,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    job = MathValidationJob(
        owner_id=actor.id,
        submission_id=submission.id,
        question_id=answer.question_id,
        student_answer_id=answer.id,
        recognition_evidence_id=evidence.id,
        scoring_input_version=scoring_version,
        reference_answer_version_id=reference.id,
        rubric_version_id=rubric.id,
        engine_version=ENGINE_VERSION,
        config_version=CONFIG_VERSION,
        idempotency_key=payload.idempotency_key,
        input_hash=input_hash,
    )
    db.add(job)
    db.flush()
    audit(db, actor.id, "create", "math_validation_job", job.id)
    db.commit()
    from workers.tasks.math_validation import run_math_validation

    task = run_math_validation.apply_async(
        args=[str(job.id), job.generation],
        headers=celery_request_headers(),
    )
    job.celery_task_id = task.id
    db.commit()
    return _job_json(db, job)


@router.get("/math-validation/jobs/{job_id}")
def get_validation_job(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    job = db.scalar(
        select(MathValidationJob).where(
            MathValidationJob.id == job_id, MathValidationJob.owner_id == actor.id
        )
    )
    if job is None:
        raise ApiProblem(404, "VALIDATION_JOB_NOT_FOUND", "验证任务不存在")
    return _job_json(db, job)


@router.get("/student-answers/{answer_id}/math-validation/jobs")
def list_answer_validation_jobs(answer_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    jobs = db.scalars(
        select(MathValidationJob)
        .where(
            MathValidationJob.student_answer_id == answer_id,
            MathValidationJob.owner_id == actor.id,
        )
        .order_by(MathValidationJob.created_at.desc())
    ).all()
    return [_job_json(db, job) for job in jobs]


@router.post("/math-validation/jobs/{job_id}/criteria/{criterion_id}/retry", status_code=202)
def retry_criterion(
    job_id: uuid.UUID, criterion_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    job = db.scalar(
        select(MathValidationJob)
        .where(MathValidationJob.id == job_id, MathValidationJob.owner_id == actor.id)
        .with_for_update()
    )
    criterion = db.get(RubricCriterion, criterion_id)
    if job is None or criterion is None or criterion.rubric_version_id != job.rubric_version_id:
        raise ApiProblem(404, "VALIDATION_CRITERION_NOT_FOUND", "验证任务或评分项不存在")
    if job.stale_at is not None:
        raise ApiProblem(409, "VALIDATION_STALE", "过期验证不可重试")
    job.generation += 1
    job.status = "queued"
    db.commit()
    from workers.tasks.math_validation import run_math_validation

    task = run_math_validation.apply_async(
        args=[str(job.id), job.generation, str(criterion_id)],
        headers=celery_request_headers(),
    )
    job.celery_task_id = task.id
    db.commit()
    return _job_json(db, job)
