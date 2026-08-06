import uuid
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.core.request_id import celery_request_headers
from app.db.session import get_db
from app.math_validation.stale import stale_for_answer
from app.models import (
    QuestionRecognitionEvidence,
    RecognitionRevision,
    RegionEvidenceImage,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    now_utc,
)
from app.recognition.answer_evidence import next_revision
from app.recognition.answer_providers import normalize_math
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["answer-recognition"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


class BlockPatch(BaseModel):
    raw_text: str | None = None
    normalized_text: str | None = None
    latex: str | None = None
    block_type: Literal["text", "formula", "matrix", "table", "diagram", "unknown"] | None = None


class SplitInput(BaseModel):
    offset: int = Field(gt=0)


class MergeInput(BaseModel):
    block_ids: list[uuid.UUID] = Field(min_length=2)


class ReorderInput(BaseModel):
    block_ids: list[uuid.UUID] = Field(min_length=1)


def _submission(db: Session, actor_id: uuid.UUID, submission_id: uuid.UUID) -> Submission:
    submission = db.scalar(
        select(Submission).where(Submission.id == submission_id, Submission.owner_id == actor_id)
    )
    if submission is None:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "答卷不存在")
    return submission


def _editable(db: Session, actor_id: uuid.UUID, submission_id: uuid.UUID) -> Submission:
    submission = _submission(db, actor_id, submission_id)
    if submission.finalized_at is not None or submission.status == "finalized":
        raise ApiProblem(409, "FINALIZED_SUBMISSION_IMMUTABLE", "已完成答卷只能查看")
    return submission


def _block(
    db: Session, actor_id: uuid.UUID, submission_id: uuid.UUID, block_id: uuid.UUID
) -> SubmissionRecognitionBlock:
    row = db.scalar(
        select(SubmissionRecognitionBlock)
        .join(
            SubmissionRecognitionJob,
            SubmissionRecognitionJob.id == SubmissionRecognitionBlock.submission_recognition_job_id,
        )
        .where(
            SubmissionRecognitionBlock.id == block_id,
            SubmissionRecognitionJob.owner_id == actor_id,
            SubmissionRecognitionJob.submission_id == submission_id,
        )
    )
    if row is None:
        raise ApiProblem(404, "RECOGNITION_BLOCK_NOT_FOUND", "识别块不存在")
    return row


def _display_evidence_key(
    db: Session, block: SubmissionRecognitionBlock
) -> str | None:
    if block.region_evidence_image_id is None:
        return block.evidence_image_key
    evidence = db.get(RegionEvidenceImage, block.region_evidence_image_id)
    if (
        evidence is None
        or evidence.source_kind != "processed"
        or evidence.submission_page_id != block.submission_page_id
        or evidence.student_answer_region_id != block.student_answer_region_id
    ):
        return None
    return evidence.object_key


def _json(
    db: Session, block: SubmissionRecognitionBlock, storage: ObjectStorage
) -> dict[str, Any]:
    evidence_image_key = _display_evidence_key(db, block)
    return {
        "id": str(block.id),
        "job_id": str(block.submission_recognition_job_id),
        "page_id": str(block.submission_page_id),
        "region_id": str(block.student_answer_region_id)
        if block.student_answer_region_id
        else None,
        "source_page_number": block.source_page_number,
        "block_type": block.block_type,
        "bbox": {
            "x": block.x,
            "y": block.y,
            "width": block.width,
            "height": block.height,
        },
        "reading_order": block.reading_order,
        "raw_text": block.text,
        "normalized_text": block.normalized_text,
        "latex": block.latex,
        "confidence": block.confidence,
        "provider": block.provider,
        "provider_version": block.provider_version,
        "warning_codes": block.warning_codes,
        "requires_review": block.requires_review,
        "status": block.status,
        "recognition_version": block.recognition_version,
        "stale": block.stale_at is not None,
        "confirmed_at": block.confirmed_at,
        "evidence_image_key": evidence_image_key,
        "evidence_image_url": storage.presigned_get(evidence_image_key)
        if evidence_image_key
        else None,
    }


def _human_revision(
    db: Session, block: SubmissionRecognitionBlock, actor_id: uuid.UUID, *, confirmed: bool = False
) -> None:
    db.add(
        RecognitionRevision(
            recognition_block_id=block.id,
            revision=next_revision(db, block.id),
            source="human",
            raw_text=block.text,
            normalized_text=block.normalized_text,
            latex=block.latex,
            warning_codes=block.warning_codes,
            editor_id=actor_id,
            base_recognition_version=block.recognition_version,
            confirmed=confirmed,
        )
    )


def _stale_for_block(db: Session, block: SubmissionRecognitionBlock, reason: str) -> None:
    if block.student_answer_region_id is None:
        return
    answer_id = db.scalar(
        select(StudentAnswerRegion.student_answer_id).where(
            StudentAnswerRegion.id == block.student_answer_region_id
        )
    )
    if answer_id is not None:
        stale_for_answer(db, answer_id, reason)


@router.get("/submissions/{submission_id}/recognition-blocks")
def list_blocks(
    submission_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    region_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    _submission(db, actor.id, submission_id)
    query = (
        select(SubmissionRecognitionBlock)
        .join(
            SubmissionRecognitionJob,
            SubmissionRecognitionJob.id == SubmissionRecognitionBlock.submission_recognition_job_id,
        )
        .where(SubmissionRecognitionJob.submission_id == submission_id)
        .order_by(SubmissionRecognitionBlock.reading_order)
    )
    if region_id:
        query = query.where(SubmissionRecognitionBlock.student_answer_region_id == region_id)
    if status:
        query = query.where(SubmissionRecognitionBlock.status == status)
    return [_json(db, block, storage) for block in db.scalars(query).all()]


@router.get("/submissions/{submission_id}/question-recognition-evidence")
def list_question_evidence(submission_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    _submission(db, actor.id, submission_id)
    rows = db.execute(
        select(QuestionRecognitionEvidence, StudentAnswer)
        .join(StudentAnswer, StudentAnswer.id == QuestionRecognitionEvidence.student_answer_id)
        .where(
            QuestionRecognitionEvidence.submission_id == submission_id,
            QuestionRecognitionEvidence.owner_id == actor.id,
        )
        .order_by(
            QuestionRecognitionEvidence.student_answer_id,
            QuestionRecognitionEvidence.recognition_version.desc(),
        )
    ).all()
    return [
        {
            "id": str(evidence.id),
            "student_answer_id": str(evidence.student_answer_id),
            "question_id": str(answer.question_id),
            "status": evidence.status,
            "block_sources": evidence.block_sources,
            "normalized_text": evidence.normalized_text,
            "latex": evidence.latex,
            "provider_versions": evidence.provider_versions,
            "recognition_version": evidence.recognition_version,
            "requires_review": evidence.requires_review,
            "stale": evidence.stale_at is not None,
            "confirmed_at": evidence.confirmed_at,
        }
        for evidence, answer in rows
    ]


@router.patch("/submissions/{submission_id}/recognition-blocks/{block_id}")
def edit_block(
    submission_id: uuid.UUID,
    block_id: uuid.UUID,
    data: BlockPatch,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    _editable(db, actor.id, submission_id)
    block = _block(db, actor.id, submission_id, block_id)
    if block.stale_at is not None:
        raise ApiProblem(409, "RECOGNITION_BLOCK_STALE", "过期识别块不可编辑")
    if data.raw_text is not None:
        block.text = data.raw_text
    if data.block_type is not None:
        block.block_type = data.block_type
    normalized = normalize_math(
        data.normalized_text if data.normalized_text is not None else block.text,
        data.latex if data.latex is not None else block.latex,
        block.block_type,
    )
    block.normalized_text, block.latex = normalized.text, normalized.latex
    block.warning_codes = normalized.warnings
    block.requires_review, block.status = True, "human_edited"
    _stale_for_block(db, block, "RECOGNITION_REVISION_CHANGED")
    _human_revision(db, block, actor.id)
    audit(db, actor.id, "recognition_block.edit", "submission_recognition_block", block.id)
    db.commit()
    return _json(db, block, storage)


@router.post("/submissions/{submission_id}/recognition-blocks/{block_id}/split")
def split_block(
    submission_id: uuid.UUID,
    block_id: uuid.UUID,
    data: SplitInput,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> list[dict[str, Any]]:
    _editable(db, actor.id, submission_id)
    block = _block(db, actor.id, submission_id, block_id)
    text = block.text or ""
    if data.offset >= len(text):
        raise ApiProblem(422, "SPLIT_OFFSET_INVALID", "拆分位置必须位于文本内部")
    first, second = text[: data.offset], text[data.offset :]
    block.text, block.normalized_text, block.requires_review, block.status = (
        first,
        first,
        True,
        "human_edited",
    )
    max_index = db.scalar(
        select(func.max(SubmissionRecognitionBlock.block_index)).where(
            SubmissionRecognitionBlock.submission_page_id == block.submission_page_id
        )
    )
    new = SubmissionRecognitionBlock(
        submission_recognition_job_id=block.submission_recognition_job_id,
        submission_page_id=block.submission_page_id,
        student_answer_region_id=block.student_answer_region_id,
        region_evidence_image_id=block.region_evidence_image_id,
        source_page_number=block.source_page_number,
        block_index=(max_index or 0) + 1,
        block_type=block.block_type,
        text=second,
        normalized_text=second,
        latex=None,
        confidence=block.confidence,
        status="human_edited",
        x=block.x,
        y=block.y,
        width=block.width,
        height=block.height,
        reading_order=block.reading_order + 1,
        provider=block.provider,
        provider_version=block.provider_version,
        warning_codes=[],
        requires_review=True,
        evidence_image_key=block.evidence_image_key,
        recognition_version=block.recognition_version,
        input_hash=block.input_hash,
    )
    db.add(new)
    db.flush()
    _stale_for_block(db, block, "RECOGNITION_REVISION_CHANGED")
    _human_revision(db, block, actor.id)
    _human_revision(db, new, actor.id)
    audit(db, actor.id, "recognition_block.split", "submission_recognition_block", block.id)
    db.commit()
    return [_json(db, block, storage), _json(db, new, storage)]


@router.post("/submissions/{submission_id}/recognition-blocks/merge")
def merge_blocks(
    submission_id: uuid.UUID,
    data: MergeInput,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    _editable(db, actor.id, submission_id)
    blocks = [_block(db, actor.id, submission_id, block_id) for block_id in data.block_ids]
    if len({block.student_answer_region_id for block in blocks}) != 1:
        raise ApiProblem(409, "MERGE_REGION_MISMATCH", "只能合并同一区域的识别块")
    blocks.sort(key=lambda item: item.reading_order)
    target = blocks[0]
    target.text = "\n".join(block.text or "" for block in blocks)
    target.normalized_text = "\n".join(
        block.normalized_text or block.text or "" for block in blocks
    )
    target.status, target.requires_review = "human_edited", True
    _stale_for_block(db, target, "RECOGNITION_REVISION_CHANGED")
    for merged in blocks[1:]:
        merged.status, merged.requires_review, merged.stale_at = "merged", True, now_utc()
        _human_revision(db, merged, actor.id)
    _human_revision(db, target, actor.id)
    audit(db, actor.id, "recognition_block.merge", "submission_recognition_block", target.id)
    db.commit()
    return _json(db, target, storage)


@router.put("/submissions/{submission_id}/recognition-blocks/order")
def reorder_blocks(
    submission_id: uuid.UUID, data: ReorderInput, db: Db, actor: Actor
) -> dict[str, Any]:
    _editable(db, actor.id, submission_id)
    blocks = [_block(db, actor.id, submission_id, block_id) for block_id in data.block_ids]
    if len({block.id for block in blocks}) != len(data.block_ids):
        raise ApiProblem(422, "BLOCK_ORDER_INVALID", "排序包含重复识别块")
    for order, block in enumerate(blocks):
        block.reading_order = order
        block.status, block.requires_review = "human_edited", True
        _human_revision(db, block, actor.id)
        _stale_for_block(db, block, "RECOGNITION_REVISION_CHANGED")
    audit(db, actor.id, "recognition_block.reorder", "submission", submission_id)
    db.commit()
    return {"block_ids": [str(block.id) for block in blocks]}


@router.post("/submissions/{submission_id}/answers/{answer_id}/recognition/confirm")
def confirm_answer(
    submission_id: uuid.UUID, answer_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    _editable(db, actor.id, submission_id)
    answer = db.scalar(
        select(StudentAnswer).where(
            StudentAnswer.id == answer_id, StudentAnswer.submission_id == submission_id
        )
    )
    if answer is None:
        raise ApiProblem(404, "STUDENT_ANSWER_NOT_FOUND", "题目答案不存在")
    evidence = db.scalar(
        select(QuestionRecognitionEvidence)
        .where(
            QuestionRecognitionEvidence.student_answer_id == answer.id,
            QuestionRecognitionEvidence.stale_at.is_(None),
        )
        .order_by(QuestionRecognitionEvidence.recognition_version.desc())
    )
    if evidence is None:
        raise ApiProblem(409, "RECOGNITION_EVIDENCE_UNAVAILABLE", "没有可确认的识别证据")
    evidence.status, evidence.requires_review = "confirmed", False
    stale_for_answer(db, answer.id, "RECOGNITION_CONFIRMATION_CHANGED")
    evidence.confirmed_at, evidence.confirmed_by = now_utc(), actor.id
    evidence.confirmation_origin = "teacher_explicit"
    evidence.confirmed_revision = (evidence.confirmed_revision or 0) + 1
    region_ids = select(StudentAnswerRegion.id).where(
        StudentAnswerRegion.student_answer_id == answer.id
    )
    for block in db.scalars(
        select(SubmissionRecognitionBlock).where(
            SubmissionRecognitionBlock.student_answer_region_id.in_(region_ids),
            SubmissionRecognitionBlock.stale_at.is_(None),
        )
    ):
        block.status, block.requires_review = "confirmed", False
        block.confirmed_at, block.confirmed_by = now_utc(), actor.id
        _human_revision(db, block, actor.id, confirmed=True)
    answer.status, answer.requires_review = "recognition_confirmed", False
    audit(db, actor.id, "answer_recognition.confirm", "student_answer", answer.id)
    db.commit()
    return {"status": "confirmed", "student_answer_id": str(answer.id)}


@router.post("/submissions/{submission_id}/regions/{region_id}/recognition/retry")
def retry_region(
    submission_id: uuid.UUID,
    region_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    run_now: bool = False,
) -> dict[str, Any]:
    _editable(db, actor.id, submission_id)
    region = db.scalar(
        select(StudentAnswerRegion)
        .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
        .where(
            StudentAnswerRegion.id == region_id,
            StudentAnswerRegion.status == "confirmed",
            StudentAnswer.submission_id == submission_id,
        )
    )
    if region is None:
        raise ApiProblem(409, "CONFIRMED_REGION_NOT_FOUND", "只能重试已确认区域")
    job = db.scalar(
        select(SubmissionRecognitionJob)
        .where(
            SubmissionRecognitionJob.submission_id == submission_id,
            SubmissionRecognitionJob.owner_id == actor.id,
        )
        .order_by(SubmissionRecognitionJob.created_at.desc())
    )
    if job is None:
        raise ApiProblem(404, "SUBMISSION_RECOGNITION_NOT_FOUND", "识别任务不存在")
    job.generation += 1
    job.status, job.progress, job.error_code, job.error_message = "queued", 0, None, None
    db.commit()
    if run_now:
        from app.core.config import get_settings
        from app.recognition.answer_evidence import run_answer_evidence_phase

        run_answer_evidence_phase(db, storage, get_settings(), job.id, region_id=region.id)
    else:
        from workers.celery_app import celery_app

        celery_app.send_task(
            "ahamark.answer_recognition.run",
            args=[str(job.id), str(region.id)],
            headers=celery_request_headers(),
        )
    return {"job_id": str(job.id), "status": job.status, "generation": job.generation}


@router.get("/submissions/{submission_id}/recognition-review-queue")
def review_queue(submission_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    _submission(db, actor.id, submission_id)
    jobs = db.scalars(
        select(SubmissionRecognitionJob).where(
            SubmissionRecognitionJob.submission_id == submission_id,
            SubmissionRecognitionJob.owner_id == actor.id,
            SubmissionRecognitionJob.status.in_(["failed", "partially_completed", "cancelled"]),
        )
    ).all()
    evidence = db.scalars(
        select(QuestionRecognitionEvidence).where(
            QuestionRecognitionEvidence.submission_id == submission_id,
            (
                (QuestionRecognitionEvidence.requires_review.is_(True))
                | (QuestionRecognitionEvidence.stale_at.is_not(None))
            ),
        )
    ).all()
    return {
        "jobs": [
            {
                "id": str(job.id),
                "status": job.status,
                "error_code": job.error_code,
                "warning_codes": job.warning_codes,
            }
            for job in jobs
        ],
        "questions": [
            {
                "student_answer_id": str(item.student_answer_id),
                "status": item.status,
                "stale": item.stale_at is not None,
            }
            for item in evidence
        ],
    }
