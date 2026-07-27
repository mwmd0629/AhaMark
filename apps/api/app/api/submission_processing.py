import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.core.config import get_settings
from app.core.request_id import celery_request_headers
from app.db.session import get_db
from app.models import (
    Assignment,
    GradingResult,
    Question,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionProcessingJob,
    SubmissionQuestionAnchor,
    TeacherReview,
    now_utc,
)
from app.recognition.submission_processing import (
    PROCESSING_VERSION,
    SEGMENTATION_VERSION,
    run_submission_processing,
)
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["submission-processing"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


class ProcessingStart(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class RegionMutation(BaseModel):
    question_id: uuid.UUID
    submission_page_id: uuid.UUID
    x: Decimal = Field(ge=0, lt=1)
    y: Decimal = Field(ge=0, lt=1)
    width: Decimal = Field(gt=0, le=1)
    height: Decimal = Field(gt=0, le=1)
    status: Literal["candidate", "confirmed", "rejected", "manual_required"] = "confirmed"
    source: Literal["manual", "template", "ocr", "alignment"] = "manual"
    confidence: Decimal | None = Field(None, ge=0, le=1)
    reason: str | None = Field(None, max_length=255)


def _submission(db: Session, owner_id: uuid.UUID, submission_id: uuid.UUID) -> Submission:
    item = db.scalar(
        select(Submission).where(Submission.id == submission_id, Submission.owner_id == owner_id)
    )
    if item is None:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "答卷不存在")
    return item


def _editable(db: Session, owner_id: uuid.UUID, submission_id: uuid.UUID) -> Submission:
    item = _submission(db, owner_id, submission_id)
    if item.status == "finalized" or item.finalized_at is not None:
        raise ApiProblem(409, "FINALIZED_SUBMISSION_IMMUTABLE", "已完成答卷不可修改")
    return item


def _job_json(job: SubmissionProcessingJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "submission_id": str(job.submission_id),
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "provider": job.provider,
        "provider_version": job.provider_version,
        "config_version": job.config_version,
        "attempt": job.attempt,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }


def _dispatch(job_id: uuid.UUID, page_id: uuid.UUID | None = None) -> None:
    from workers.celery_app import celery_app

    celery_app.send_task(
        "ahamark.submission_processing.run",
        args=[str(job_id), str(page_id) if page_id else None],
        headers=celery_request_headers(),
    )


@router.post("/submissions/{submission_id}/processing-jobs", status_code=201)
def start_processing(
    submission_id: uuid.UUID,
    data: ProcessingStart,
    db: Db,
    actor: Actor,
    storage: Storage,
    run_now: bool = False,
) -> dict[str, Any]:
    submission = _editable(db, actor.id, submission_id)
    existing = db.scalar(
        select(SubmissionProcessingJob).where(
            SubmissionProcessingJob.owner_id == actor.id,
            SubmissionProcessingJob.idempotency_key == data.idempotency_key,
        )
    )
    if existing:
        if existing.submission_id != submission.id:
            raise ApiProblem(409, "IDEMPOTENCY_KEY_CONFLICT", "幂等键已用于其他答卷")
        return _job_json(existing)
    job = SubmissionProcessingJob(
        owner_id=actor.id,
        submission_id=submission.id,
        idempotency_key=data.idempotency_key,
        status="queued",
        stage="page_processing",
        config_version=PROCESSING_VERSION,
    )
    db.add(job)
    db.flush()
    audit(db, actor.id, "submission_processing.create", "submission_processing_job", job.id)
    db.commit()
    if run_now:
        run_submission_processing(db, storage, get_settings(), job.id)
    else:
        try:
            _dispatch(job.id)
        except Exception as exc:
            job.status, job.error_code, job.error_message = (
                "failed",
                "WORKER_UNAVAILABLE",
                type(exc).__name__,
            )
            db.commit()
    return _job_json(job)


@router.get("/submissions/{submission_id}/processing-jobs/{job_id}")
def get_processing_job(
    submission_id: uuid.UUID, job_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    _submission(db, actor.id, submission_id)
    job = db.scalar(
        select(SubmissionProcessingJob).where(
            SubmissionProcessingJob.id == job_id,
            SubmissionProcessingJob.submission_id == submission_id,
            SubmissionProcessingJob.owner_id == actor.id,
        )
    )
    if job is None:
        raise ApiProblem(404, "PROCESSING_JOB_NOT_FOUND", "页面处理任务不存在")
    return _job_json(job)


@router.get("/submissions/{submission_id}/processing-pages")
def list_processing_pages(
    submission_id: uuid.UUID, db: Db, actor: Actor, storage: Storage
) -> list[dict[str, Any]]:
    _submission(db, actor.id, submission_id)
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission_id)
        .order_by(SubmissionPage.page_number)
    ).all()
    return [
        {
            "id": str(page.id),
            "source_page_number": page.source_page_number,
            "page_number": page.page_number,
            "width": page.width,
            "height": page.height,
            "rotation": page.rotation,
            "processing_status": page.processing_status,
            "quality": {
                "blur_score": page.blur_score,
                "brightness": page.brightness,
                "contrast": page.contrast,
                "blank_probability": page.blank_probability,
                "duplicate_of_page_id": (
                    str(page.duplicate_of_page_id) if page.duplicate_of_page_id else None
                ),
                "orientation_confidence": page.orientation_confidence,
                "warnings": page.quality_warnings,
            },
            "preprocessing_version": page.preprocessing_version,
            "error_code": page.processing_error_code,
            "retryable": page.retryable,
            "alignment": {
                "paper_page_id": (
                    str(page.aligned_paper_page_id) if page.aligned_paper_page_id else None
                ),
                "transform": page.alignment_transform,
                "confidence": page.alignment_confidence,
                "failure_reason": page.alignment_failure_reason,
            },
            "original_url": (
                storage.presigned_get(page.rendered_storage_key)
                if page.rendered_storage_key
                else None
            ),
            "processed_url": (
                storage.presigned_get(page.processed_storage_key)
                if page.processed_storage_key
                else None
            ),
            "thumbnail_url": (
                storage.presigned_get(page.thumbnail_storage_key)
                if page.thumbnail_storage_key
                else None
            ),
        }
        for page in pages
    ]


def _region_json(region: StudentAnswerRegion, answer: StudentAnswer) -> dict[str, Any]:
    return {
        "id": str(region.id),
        "question_id": str(answer.question_id),
        "student_answer_id": str(answer.id),
        "submission_page_id": str(region.submission_page_id),
        "x": region.x,
        "y": region.y,
        "width": region.width,
        "height": region.height,
        "source": region.source,
        "confidence": region.confidence,
        "status": region.status,
        "reason": region.reason,
        "segmentation_version": region.segmentation_version,
    }


@router.get("/submissions/{submission_id}/region-candidates")
def list_regions(submission_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    _submission(db, actor.id, submission_id)
    rows = db.execute(
        select(StudentAnswerRegion, StudentAnswer)
        .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
        .where(StudentAnswer.submission_id == submission_id)
        .order_by(StudentAnswerRegion.submission_page_id, StudentAnswerRegion.y)
    ).all()
    return [_region_json(region, answer) for region, answer in rows]


def _validate_region(
    db: Session, submission: Submission, data: RegionMutation
) -> tuple[SubmissionPage, StudentAnswer]:
    if data.x + data.width > 1 or data.y + data.height > 1:
        raise ApiProblem(422, "ANSWER_REGION_INVALID", "区域必须位于页面 0–1 坐标内")
    page = db.scalar(
        select(SubmissionPage).where(
            SubmissionPage.id == data.submission_page_id,
            SubmissionPage.submission_id == submission.id,
        )
    )
    assignment = db.get(Assignment, submission.assignment_id)
    answer = (
        db.scalar(
            select(StudentAnswer)
            .join(Question, Question.id == StudentAnswer.question_id)
            .where(
                StudentAnswer.submission_id == submission.id,
                StudentAnswer.question_id == data.question_id,
                Question.paper_version_id == assignment.active_paper_version_id,
            )
        )
        if assignment and assignment.active_paper_version_id
        else None
    )
    if page is None or answer is None:
        raise ApiProblem(422, "REGION_RESOURCE_MISMATCH", "页面或题目不属于该答卷作业版本")
    return page, answer


def _invalidate(db: Session, answer: StudentAnswer) -> None:
    from app.recognition.answer_evidence import mark_answer_recognition_stale

    answer.status, answer.requires_review = "stale", True
    mark_answer_recognition_stale(db, answer.id)
    for result in db.scalars(
        select(GradingResult).where(GradingResult.student_answer_id == answer.id)
    ):
        if result.status not in {"superseded", "rejected"}:
            result.status = "stale"
    for review in db.scalars(
        select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
    ):
        review.confirmed_at = None


def _reject_high_overlap(
    db: Session,
    submission_id: uuid.UUID,
    data: RegionMutation,
    exclude_id: uuid.UUID | None = None,
) -> None:
    rows = db.scalars(
        select(StudentAnswerRegion)
        .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
        .where(
            StudentAnswer.submission_id == submission_id,
            StudentAnswerRegion.submission_page_id == data.submission_page_id,
            StudentAnswerRegion.status != "rejected",
        )
    ).all()
    area = data.width * data.height
    for region in rows:
        if region.id == exclude_id:
            continue
        left, top = max(data.x, region.x), max(data.y, region.y)
        right = min(data.x + data.width, region.x + region.width)
        bottom = min(data.y + data.height, region.y + region.height)
        intersection = max(Decimal(0), right - left) * max(Decimal(0), bottom - top)
        if intersection and intersection / min(area, region.width * region.height) >= Decimal(
            "0.65"
        ):
            raise ApiProblem(
                409,
                "REGION_OVERLAP_CONFLICT",
                "区域与现有题目区域高度重叠，需要教师调整或明确拒绝旧候选",
                {"conflicting_region_id": str(region.id)},
            )


@router.post("/submissions/{submission_id}/region-candidates", status_code=201)
def add_region(
    submission_id: uuid.UUID, data: RegionMutation, db: Db, actor: Actor
) -> dict[str, Any]:
    submission = _editable(db, actor.id, submission_id)
    page, answer = _validate_region(db, submission, data)
    _reject_high_overlap(db, submission.id, data)
    region = StudentAnswerRegion(
        student_answer_id=answer.id,
        submission_page_id=page.id,
        x=data.x,
        y=data.y,
        width=data.width,
        height=data.height,
        source=data.source,
        confidence=data.confidence,
        status=data.status,
        reason=data.reason,
        segmentation_version=SEGMENTATION_VERSION,
        confirmed_by=actor.id if data.status == "confirmed" else None,
        confirmed_at=now_utc() if data.status == "confirmed" else None,
    )
    db.add(region)
    db.flush()
    _invalidate(db, answer)
    audit(db, actor.id, "submission_region.create", "student_answer_region", region.id)
    db.commit()
    return _region_json(region, answer)


@router.put("/submissions/{submission_id}/region-candidates/{region_id}")
def update_region(
    submission_id: uuid.UUID,
    region_id: uuid.UUID,
    data: RegionMutation,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    submission = _editable(db, actor.id, submission_id)
    page, answer = _validate_region(db, submission, data)
    _reject_high_overlap(db, submission.id, data, region_id)
    region = db.scalar(
        select(StudentAnswerRegion)
        .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
        .where(
            StudentAnswerRegion.id == region_id,
            StudentAnswer.submission_id == submission.id,
        )
    )
    if region is None:
        raise ApiProblem(404, "ANSWER_REGION_NOT_FOUND", "区域不存在")
    old_answer = db.get(StudentAnswer, region.student_answer_id)
    region.student_answer_id = answer.id
    region.submission_page_id = page.id
    region.x, region.y, region.width, region.height = data.x, data.y, data.width, data.height
    region.source, region.confidence = data.source, data.confidence
    region.status, region.reason = data.status, data.reason
    region.region_version += 1
    region.confirmed_by = actor.id if data.status == "confirmed" else None
    region.confirmed_at = now_utc() if data.status == "confirmed" else None
    _invalidate(db, answer)
    if old_answer and old_answer.id != answer.id:
        _invalidate(db, old_answer)
    audit(db, actor.id, "submission_region.update", "student_answer_region", region.id)
    db.commit()
    return _region_json(region, answer)


@router.delete("/submissions/{submission_id}/region-candidates/{region_id}", status_code=204)
def remove_region(submission_id: uuid.UUID, region_id: uuid.UUID, db: Db, actor: Actor) -> None:
    submission = _editable(db, actor.id, submission_id)
    row = db.execute(
        select(StudentAnswerRegion, StudentAnswer)
        .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
        .where(
            StudentAnswerRegion.id == region_id,
            StudentAnswer.submission_id == submission.id,
        )
    ).one_or_none()
    if row is None:
        raise ApiProblem(404, "ANSWER_REGION_NOT_FOUND", "区域不存在")
    region, answer = row
    db.delete(region)
    _invalidate(db, answer)
    audit(db, actor.id, "submission_region.delete", "student_answer_region", region.id)
    db.commit()


@router.post("/submissions/{submission_id}/region-candidates/confirm-high-confidence")
def confirm_high_confidence(submission_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    submission = _editable(db, actor.id, submission_id)
    rows = db.execute(
        select(StudentAnswerRegion, StudentAnswer)
        .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
        .where(
            StudentAnswer.submission_id == submission.id,
            StudentAnswerRegion.status == "candidate",
            StudentAnswerRegion.confidence >= Decimal("0.85"),
        )
    ).all()
    for region, answer in rows:
        region.status, region.confirmed_by, region.confirmed_at = (
            "confirmed",
            actor.id,
            now_utc(),
        )
        _invalidate(db, answer)
    audit(
        db,
        actor.id,
        "submission_region.confirm_high_confidence",
        "submission",
        submission.id,
        {"count": len(rows)},
    )
    db.commit()
    return {"confirmed_count": len(rows)}


@router.get("/submissions/{submission_id}/segmentation-incomplete")
def segmentation_incomplete(submission_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    submission = _submission(db, actor.id, submission_id)
    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
    ).all()
    incomplete: list[str] = []
    for answer in answers:
        confirmed = db.scalar(
            select(StudentAnswerRegion.id).where(
                StudentAnswerRegion.student_answer_id == answer.id,
                StudentAnswerRegion.status == "confirmed",
            )
        )
        if confirmed is None:
            incomplete.append(str(answer.question_id))
    return {"complete": not incomplete, "question_ids": incomplete}


@router.post("/submissions/{submission_id}/processing-jobs/{job_id}/pages/{page_id}/retry")
def retry_page(
    submission_id: uuid.UUID,
    job_id: uuid.UUID,
    page_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    run_now: bool = False,
) -> dict[str, Any]:
    _editable(db, actor.id, submission_id)
    job = db.scalar(
        select(SubmissionProcessingJob).where(
            SubmissionProcessingJob.id == job_id,
            SubmissionProcessingJob.submission_id == submission_id,
            SubmissionProcessingJob.owner_id == actor.id,
        )
    )
    page = db.scalar(
        select(SubmissionPage).where(
            SubmissionPage.id == page_id, SubmissionPage.submission_id == submission_id
        )
    )
    if job is None or page is None:
        raise ApiProblem(404, "PROCESSING_PAGE_NOT_FOUND", "处理页面不存在")
    page.processing_status, job.status, job.stage = "pending", "queued", "page_processing"
    db.commit()
    if run_now:
        run_submission_processing(db, storage, get_settings(), job.id, page.id)
    else:
        _dispatch(job.id, page.id)
    return _job_json(job)


@router.get("/submissions/{submission_id}/question-anchors")
def list_anchors(submission_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    _submission(db, actor.id, submission_id)
    anchors = db.scalars(
        select(SubmissionQuestionAnchor)
        .join(
            SubmissionProcessingJob,
            SubmissionProcessingJob.id == SubmissionQuestionAnchor.submission_processing_job_id,
        )
        .where(SubmissionProcessingJob.submission_id == submission_id)
        .order_by(SubmissionQuestionAnchor.submission_page_id, SubmissionQuestionAnchor.y)
    ).all()
    return [
        {
            "id": str(anchor.id),
            "submission_page_id": str(anchor.submission_page_id),
            "text": anchor.text,
            "normalized_number": anchor.normalized_number,
            "question_id": (
                str(anchor.candidate_question_id) if anchor.candidate_question_id else None
            ),
            "confidence": anchor.confidence,
            "x": anchor.x,
            "y": anchor.y,
            "width": anchor.width,
            "height": anchor.height,
            "rejection_reason": anchor.rejection_reason,
        }
        for anchor in anchors
    ]
