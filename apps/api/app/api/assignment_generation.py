import uuid
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.assignment_generation.metadata_analysis import plain_text
from app.assignment_generation.providers import select_provider
from app.assignment_generation.question_extraction import eligible, materialize
from app.assignment_generation.service import (
    ACTIVE_STATUSES,
    STAGES,
    create_job,
    ensure_current,
    issue,
    job_json,
    mark_stale,
    next_stage_generation,
    owned_assignment,
    owned_job,
    owned_revision,
    prepare_stage_retry,
    revision_json,
    transition,
    update_risk_summary,
)
from app.assignment_generation.snapshot import canonical_hash, source_snapshot_hash
from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    Assignment,
    AssignmentDraftRevision,
    AssignmentFieldSuggestion,
    AssignmentGenerationJob,
    AssignmentPageAnalysis,
    AssignmentQuestionExtractionCandidate,
    AssignmentQuestionExtractionRegion,
    AssignmentSourceFileAnalysis,
    AssignmentStatus,
    GenerationIssue,
    GenerationStageResult,
    KnowledgePoint,
    PaperPage,
    PaperPageOrganizationSuggestion,
    PaperVersion,
    QuestionKnowledgePoint,
    StoredFile,
    VersionStatus,
    now_utc,
)
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(tags=["assignment-generation"])
Db = Annotated[Session, Depends(get_db)]


class CreateGenerationInput(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    provider_mode: Literal["unavailable", "fake", "openai_compatible"] | None = None
    expected_source_snapshot: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not 8 <= len(normalized) <= 128:
            raise ValueError("idempotency_key 去除首尾空白后长度必须为 8 到 128")
        return normalized


class RetryStageInput(BaseModel):
    stage: Literal[
        "analyzing",
        "processing_pages",
        "extracting_questions",
        "generating_rubrics",
        "validating",
    ]


class DraftMetadataPatch(BaseModel):
    expected_teacher_edit_version: int = Field(ge=0)
    label: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=2000)


class FieldDispositionInput(BaseModel):
    action: Literal["accept", "modify", "reject"]
    expected_teacher_edit_version: int = Field(ge=0)
    expected_assignment_updated_at: datetime | None = None
    teacher_value: Any | None = None
    review_note: str | None = Field(None, max_length=2000)


class TotalScoreConfirmationInput(BaseModel):
    expected_teacher_edit_version: int = Field(ge=0)
    expected_assignment_updated_at: datetime
    confirmed_value: Decimal = Field(gt=0)
    explicit_confirmation: Literal[True]
    review_note: str | None = Field(None, max_length=2000)


class FileConfirmationInput(BaseModel):
    expected_teacher_edit_version: int = Field(ge=0)
    confirmed_role: Literal[
        "question_paper", "reference_answer", "rubric", "instructions", "attachment", "unknown"
    ]
    confirmed_answer_source: Literal[
        "teacher_official",
        "publisher_official",
        "teacher_provided",
        "third_party",
        "ai_generated",
        "unknown",
        "not_applicable",
    ]
    review_note: str | None = Field(None, max_length=2000)


class PageOrganizationDispositionInput(BaseModel):
    action: Literal["accept", "modify", "reject", "mark_manual_required"]
    expected_teacher_edit_version: int = Field(ge=0)
    expected_draft_revision_edit_version: int = Field(ge=0)
    expected_paper_version_id: uuid.UUID
    expected_source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    teacher_value: dict[str, Any] | None = None
    review_note: str | None = Field(None, max_length=2000)


class QuestionExtractionDispositionInput(BaseModel):
    action: Literal["accept", "modify", "reject", "mark_manual_required"]
    expected_teacher_edit_version: int = Field(ge=0)
    expected_draft_revision_edit_version: int = Field(ge=0)
    expected_paper_version_id: uuid.UUID
    expected_source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    teacher_value: dict[str, Any] | None = None
    review_note: str | None = Field(None, max_length=2000)


class AcceptEligibleInput(BaseModel):
    expected_draft_revision_edit_version: int = Field(ge=0)
    expected_paper_version_id: uuid.UUID
    expected_source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("/api/assignment-generation-capabilities")
def assignment_generation_capabilities(_actor: Actor) -> dict[str, Any]:
    settings = get_settings()
    selection = select_provider(settings)
    return {
        "enabled": settings.assignment_generation_enabled,
        "provider": selection.name,
        "provider_status": "available" if selection.available else "unavailable",
        "provider_error_code": selection.error_code,
        "external_provider_requests": (
            settings.assignment_generation_allow_external_provider_requests
        ),
        "teacher_start_allowed": settings.assignment_generation_allow_teacher_start,
        "suggestion_only": settings.assignment_generation_suggestion_only,
        "real_provider_quality_passed": (
            settings.assignment_generation_real_provider_quality_passed
        ),
    }


def dispatch_job(db: Session, job: AssignmentGenerationJob, stage: str | None = None) -> None:
    try:
        from workers.celery_app import celery_app

        task = celery_app.send_task(
            "ahamark.assignment_generation.run",
            args=[str(job.id)],
            kwargs={"retry_stage": stage},
        )
    except Exception:
        db.rollback()
        locked_job = db.scalar(
            select(AssignmentGenerationJob)
            .where(AssignmentGenerationJob.id == job.id)
            .with_for_update()
        )
        if locked_job is None:
            return
        revision = db.scalar(
            select(AssignmentDraftRevision)
            .where(AssignmentDraftRevision.generation_job_id == locked_job.id)
            .with_for_update()
        )
        if locked_job.status in ACTIVE_STATUSES:
            transition(locked_job, "failed")
        locked_job.retryable = True
        locked_job.error_code = "WORKER_UNAVAILABLE"
        locked_job.error_message = "生成 Worker 当前不可用"
        if stage:
            reserved = db.scalar(
                select(GenerationStageResult)
                .where(
                    GenerationStageResult.job_id == locked_job.id,
                    GenerationStageResult.stage == stage,
                    GenerationStageResult.status == "queued",
                )
                .order_by(GenerationStageResult.stage_generation.desc())
                .with_for_update()
            )
            if reserved:
                reserved.status = "failed"
                reserved.error_code = "WORKER_UNAVAILABLE"
                reserved.error_message = locked_job.error_message
                reserved.completed_at = now_utc()
        issue(
            db,
            locked_job,
            revision,
            stage,
            "blocking",
            "STAGE_FAILED",
            "生成 Worker 当前不可用，任务未执行",
            {"error_code": "WORKER_UNAVAILABLE"},
        )
        if revision:
            update_risk_summary(db, revision)
        db.commit()
        return
    try:
        job.celery_task_id = task.id
        db.commit()
    except Exception:
        db.rollback()
        raise


@router.post("/api/assignments/{assignment_id}/generation-jobs", status_code=201)
def start_generation(
    assignment_id: uuid.UUID, data: CreateGenerationInput, db: Db, actor: Actor
) -> dict[str, Any]:
    try:
        job, _revision, reused = create_job(
            db,
            actor.id,
            assignment_id,
            data.idempotency_key,
            data.provider_mode,
            data.expected_source_snapshot,
        )
    except IntegrityError:
        # A database partial unique index is the final guard against two API
        # processes passing the service-level active-job check concurrently.
        db.rollback()
        job, _revision, reused = create_job(
            db,
            actor.id,
            assignment_id,
            data.idempotency_key,
            data.provider_mode,
            data.expected_source_snapshot,
        )
    db.commit()
    db.refresh(job)
    if not reused:
        dispatch_job(db, job)
    result = job_json(db, job)
    result["reused"] = reused
    return result


@router.get("/api/assignments/{assignment_id}/generation-jobs")
def list_generation_jobs(
    assignment_id: uuid.UUID,
    db: Db,
    actor: Actor,
    limit: int = Query(50, ge=1, le=100),
) -> list[dict[str, Any]]:
    owned_assignment(db, actor.id, assignment_id)
    jobs = db.scalars(
        select(AssignmentGenerationJob)
        .where(
            AssignmentGenerationJob.assignment_id == assignment_id,
            AssignmentGenerationJob.owner_id == actor.id,
        )
        .order_by(AssignmentGenerationJob.generation.desc())
        .limit(limit)
    ).all()
    return [job_json(db, job) for job in jobs]


@router.get("/api/assignment-generation-jobs/{job_id}")
def get_generation_job(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return job_json(db, owned_job(db, actor.id, job_id))


@router.post("/api/assignment-generation-jobs/{job_id}/cancel")
def cancel_generation(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    job = owned_job(db, actor.id, job_id, for_update=True)
    if job.status in {"cancelled", "stale"}:
        return job_json(db, job)
    if job.status not in ACTIVE_STATUSES and job.status not in {"partial", "failed"}:
        raise ApiProblem(409, "GENERATION_NOT_CANCELLABLE", "当前生成任务不可取消")
    if job.status in {"queued", "partial", "failed"}:
        transition(job, "cancelled")
    else:
        job.cancel_requested_at = now_utc()
    audit(
        db,
        actor.id,
        "assignment_generation.cancel",
        "assignment_generation_job",
        job.id,
    )
    db.commit()
    return job_json(db, job)


@router.post("/api/assignment-generation-jobs/{job_id}/retry-stage")
def retry_stage(job_id: uuid.UUID, data: RetryStageInput, db: Db, actor: Actor) -> dict[str, Any]:
    job = owned_job(db, actor.id, job_id, for_update=True)
    if job.status not in {"failed", "partial"}:
        raise ApiProblem(409, "GENERATION_STAGE_NOT_RETRYABLE", "任务当前不允许阶段重试")
    if job.attempt >= job.max_attempts:
        job.retryable = False
        db.commit()
        raise ApiProblem(409, "GENERATION_MAX_ATTEMPTS_REACHED", "生成任务已达到最大尝试次数")
    previous = db.scalar(
        select(GenerationStageResult)
        .where(
            GenerationStageResult.job_id == job.id,
            GenerationStageResult.stage == data.stage,
        )
        .order_by(GenerationStageResult.stage_generation.desc())
    )
    if previous is None or previous.status not in {"failed", "unavailable", "discarded"}:
        raise ApiProblem(409, "GENERATION_STAGE_NOT_RETRYABLE", "该阶段没有可重试结果")
    stage_index = STAGES.index(data.stage)
    for prerequisite in STAGES[:stage_index]:
        ok = db.scalar(
            select(GenerationStageResult.id).where(
                GenerationStageResult.job_id == job.id,
                GenerationStageResult.stage == prerequisite,
                GenerationStageResult.status.in_({"completed", "unavailable"}),
            )
        )
        if not ok:
            raise ApiProblem(409, "GENERATION_PREREQUISITE_INCOMPLETE", "前置阶段尚未完成")
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.generation_job_id == job.id)
        .with_for_update()
    )
    assert revision is not None
    reason = ensure_current(db, job, revision)
    if reason:
        db.commit()
        raise ApiProblem(409, reason, "任务输入或草稿已变化，不能重试")
    prepare_stage_retry(job, data.stage)
    db.add(
        GenerationStageResult(
            job_id=job.id,
            stage=data.stage,
            stage_generation=next_stage_generation(db, job.id, data.stage),
            status="queued",
            input_hash=canonical_hash(
                {
                    "source_snapshot_hash": job.source_snapshot_hash,
                    "stage": data.stage,
                    "teacher_edit_version": revision.teacher_edit_version,
                }
            ),
            expected_teacher_edit_version=revision.teacher_edit_version,
        )
    )
    audit(
        db,
        actor.id,
        "assignment_generation.retry_stage",
        "assignment_generation_job",
        job.id,
        {"stage": data.stage},
    )
    db.commit()
    dispatch_job(db, job, data.stage)
    return job_json(db, job)


@router.get("/api/assignments/{assignment_id}/draft-revisions")
def list_draft_revisions(
    assignment_id: uuid.UUID,
    db: Db,
    actor: Actor,
    limit: int = Query(50, ge=1, le=100),
) -> list[dict[str, Any]]:
    owned_assignment(db, actor.id, assignment_id)
    rows = db.scalars(
        select(AssignmentDraftRevision)
        .where(
            AssignmentDraftRevision.assignment_id == assignment_id,
            AssignmentDraftRevision.owner_id == actor.id,
        )
        .order_by(AssignmentDraftRevision.revision.desc())
        .limit(limit)
    ).all()
    return [revision_json(row) for row in rows]


@router.get("/api/assignment-draft-revisions/{revision_id}")
def get_draft_revision(revision_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return revision_json(owned_revision(db, actor.id, revision_id))


@router.patch("/api/assignment-draft-revisions/{revision_id}/metadata")
def patch_draft_metadata(
    revision_id: uuid.UUID, data: DraftMetadataPatch, db: Db, actor: Actor
) -> dict[str, Any]:
    revision = owned_revision(db, actor.id, revision_id, for_update=True)
    if revision.status in {"stale", "superseded"}:
        raise ApiProblem(409, "DRAFT_NOT_EDITABLE", "该草稿版本已失效，不能继续编辑")
    job = db.scalar(
        select(AssignmentGenerationJob)
        .where(AssignmentGenerationJob.id == revision.generation_job_id)
        .with_for_update()
    )
    if job is None or job.status in {"cancelled", "stale"}:
        raise ApiProblem(409, "DRAFT_NOT_EDITABLE", "该草稿所属任务已失效")
    if revision.teacher_edit_version != data.expected_teacher_edit_version:
        raise ApiProblem(
            409,
            "DRAFT_MODIFIED_BY_TEACHER",
            "草稿已被修改，请刷新后重试",
            {"current_teacher_edit_version": revision.teacher_edit_version},
        )
    payload = deepcopy(revision.draft_payload)
    metadata = dict(payload.get("teacher_metadata") or {})
    for key in ("label", "notes"):
        value = getattr(data, key)
        if value is not None:
            metadata[key] = value
    payload["teacher_metadata"] = metadata
    result = cast(
        CursorResult[Any],
        db.execute(
            update(AssignmentDraftRevision)
            .where(
                AssignmentDraftRevision.id == revision.id,
                AssignmentDraftRevision.teacher_edit_version == data.expected_teacher_edit_version,
            )
            .values(
                draft_payload=payload,
                teacher_edit_version=AssignmentDraftRevision.teacher_edit_version + 1,
            )
        ),
    )
    if result.rowcount != 1:
        db.rollback()
        current_version = db.scalar(
            select(AssignmentDraftRevision.teacher_edit_version).where(
                AssignmentDraftRevision.id == revision_id,
                AssignmentDraftRevision.owner_id == actor.id,
            )
        )
        raise ApiProblem(
            409,
            "DRAFT_MODIFIED_BY_TEACHER",
            "草稿已被修改，请刷新后重试",
            {"current_teacher_edit_version": current_version},
        )
    audit(
        db,
        actor.id,
        "assignment_draft.metadata_update",
        "assignment_draft_revision",
        revision.id,
        {"fields": [key for key in ("label", "notes") if getattr(data, key) is not None]},
    )
    db.commit()
    db.refresh(revision)
    return revision_json(revision)


def _suggestion_json(row: AssignmentFieldSuggestion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "assignment_id": str(row.assignment_id),
        "draft_revision_id": str(row.draft_revision_id),
        "field_name": row.field_name,
        "suggested_value": row.suggested_value,
        "normalized_value": row.normalized_value,
        "confidence": float(row.confidence),
        "evidence": row.evidence,
        "source_type": row.source_type,
        "source_stage": row.source_stage,
        "suggestion_version": row.suggestion_version,
        "status": row.status,
        "teacher_value": row.teacher_value,
        "teacher_edit_version": row.teacher_edit_version,
        "reviewed_by": str(row.reviewed_by) if row.reviewed_by else None,
        "reviewed_at": row.reviewed_at,
        "review_note": row.review_note,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _analysis_json(row: AssignmentSourceFileAnalysis, db: Session | None = None) -> dict[str, Any]:
    stored = db.get(StoredFile, row.stored_file_id) if db is not None else None
    return {
        "id": str(row.id),
        "assignment_id": str(row.assignment_id),
        "draft_revision_id": str(row.draft_revision_id),
        "stored_file_id": str(row.stored_file_id),
        "source_snapshot_hash": row.source_snapshot_hash,
        "detected_mime_type": row.detected_mime_type,
        "checksum": row.checksum,
        "file_name": stored.original_name if stored else None,
        "file_size": stored.size if stored else None,
        "page_count": row.page_count,
        "suggested_role": row.suggested_role,
        "role_confidence": float(row.role_confidence),
        "suggested_answer_source": row.suggested_answer_source,
        "answer_source_confidence": float(row.answer_source_confidence),
        "duplicate_of_file_id": str(row.duplicate_of_file_id) if row.duplicate_of_file_id else None,
        "analysis_status": row.analysis_status,
        "evidence": row.evidence,
        "warning_codes": row.warning_codes,
        "teacher_confirmed_role": row.teacher_confirmed_role,
        "teacher_confirmed_answer_source": row.teacher_confirmed_answer_source,
        "teacher_edit_version": row.teacher_edit_version,
        "confirmed_by": str(row.confirmed_by) if row.confirmed_by else None,
        "confirmed_at": row.confirmed_at,
        "review_note": row.review_note,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _page_analysis_json(row: AssignmentPageAnalysis) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "paper_page_id": str(row.paper_page_id),
        "source_file_analysis_id": str(row.source_file_analysis_id),
        "status": row.status,
        "quality_score": float(row.quality_score) if row.quality_score is not None else None,
        "blank_probability": float(row.blank_probability)
        if row.blank_probability is not None
        else None,
        "duplicate_probability": float(row.duplicate_probability)
        if row.duplicate_probability is not None
        else None,
        "duplicate_of_page_id": str(row.duplicate_of_page_id) if row.duplicate_of_page_id else None,
        "missing_page_suspected": row.missing_page_suspected,
        "low_quality": row.low_quality,
        "corrupted": row.corrupted,
        "mixed_document_suspected": row.mixed_document_suspected,
        "variant_label": row.variant_label,
        "metrics": row.metrics,
        "evidence": row.evidence,
        "warning_codes": row.warning_codes,
        "teacher_edit_version": row.teacher_edit_version,
        "reviewed_by": str(row.reviewed_by) if row.reviewed_by else None,
        "reviewed_at": row.reviewed_at,
    }


@router.get("/api/assignment-draft-revisions/{revision_id}/field-suggestions")
def list_field_suggestions(revision_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    owned_revision(db, actor.id, revision_id)
    rows = db.scalars(
        select(AssignmentFieldSuggestion)
        .where(
            AssignmentFieldSuggestion.draft_revision_id == revision_id,
            AssignmentFieldSuggestion.owner_id == actor.id,
        )
        .order_by(
            AssignmentFieldSuggestion.field_name,
            AssignmentFieldSuggestion.suggestion_version.desc(),
        )
    ).all()
    return [_suggestion_json(row) for row in rows]


def _locked_suggestion(
    db: Session, actor_id: uuid.UUID, suggestion_id: uuid.UUID
) -> AssignmentFieldSuggestion:
    row = db.scalar(
        select(AssignmentFieldSuggestion)
        .where(
            AssignmentFieldSuggestion.id == suggestion_id,
            AssignmentFieldSuggestion.owner_id == actor_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ApiProblem(404, "FIELD_SUGGESTION_NOT_FOUND", "字段建议不存在")
    return row


def _ensure_suggestion_current(
    db: Session, row: AssignmentFieldSuggestion
) -> tuple[AssignmentDraftRevision, Assignment]:
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.id == row.draft_revision_id)
        .with_for_update()
    )
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == row.assignment_id).with_for_update()
    )
    assert revision is not None and assignment is not None
    if (
        row.status in {"stale", "superseded"}
        or revision.status in {"stale", "superseded"}
        or source_snapshot_hash(db, assignment) != revision.source_snapshot_hash
    ):
        job = db.get(AssignmentGenerationJob, revision.generation_job_id)
        if job is not None:
            mark_stale(db, job, revision)
        row.status = "stale"
        db.commit()
        raise ApiProblem(409, "SUGGESTION_STALE", "字段建议已失效，请重新分析")
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "AI 建议只能用于草稿作业")
    return revision, assignment


def _normalized_teacher_value(field_name: str, value: Any) -> Any:
    limits = {
        "title": 200,
        "subject": 40,
        "grade": 40,
        "description": 4000,
        "instructions": 4000,
        "academic_year": 20,
        "assessment_type": 30,
    }
    if field_name not in limits:
        return value
    if not isinstance(value, str):
        raise ApiProblem(422, "FIELD_VALUE_INVALID", "字段值必须是纯文本")
    normalized = plain_text(value, limits[field_name])
    if field_name == "title" and not normalized:
        raise ApiProblem(422, "FIELD_VALUE_INVALID", "标题不能为空")
    return normalized or None


def _rebase_teacher_snapshot(
    db: Session, revision: AssignmentDraftRevision, assignment: Assignment
) -> None:
    """Keep one review session usable after an explicit teacher-applied field edit.

    The revision edit-version is the late-worker guard; rebasing only the stable
    snapshot prevents that authorized draft edit from making sibling suggestions
    look like an upload change.
    """
    db.flush()
    snapshot = source_snapshot_hash(db, assignment)
    revision.source_snapshot_hash = snapshot
    job = db.get(AssignmentGenerationJob, revision.generation_job_id)
    if job is not None:
        job.source_snapshot_hash = snapshot
    for analysis in db.scalars(
        select(AssignmentSourceFileAnalysis).where(
            AssignmentSourceFileAnalysis.draft_revision_id == revision.id,
            AssignmentSourceFileAnalysis.analysis_status == "suggested",
        )
    ).all():
        analysis.source_snapshot_hash = snapshot


def _resolve_issues(
    db: Session,
    revision_id: uuid.UUID,
    actor_id: uuid.UUID,
    codes: set[str],
    note: str | None,
    entity_id: str | None = None,
) -> None:
    filters: list[Any] = [
        GenerationIssue.draft_revision_id == revision_id,
        GenerationIssue.code.in_(codes),
        GenerationIssue.resolution_status == "open",
    ]
    if entity_id is not None:
        filters.append(GenerationIssue.entity_id == entity_id)
    for item in db.scalars(select(GenerationIssue).where(*filters).with_for_update()).all():
        item.resolution_status = "resolved"
        item.resolved_by = actor_id
        item.resolved_at = now_utc()
        item.resolution_note = note or "教师已明确审查"


@router.patch("/api/assignment-field-suggestions/{suggestion_id}/disposition")
def disposition_field_suggestion(
    suggestion_id: uuid.UUID, data: FieldDispositionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    row = _locked_suggestion(db, actor.id, suggestion_id)
    revision, assignment = _ensure_suggestion_current(db, row)
    if row.field_name == "total_score" and data.action != "reject":
        raise ApiProblem(
            409, "TOTAL_SCORE_EXPLICIT_CONFIRMATION_REQUIRED", "总分必须使用明确确认接口"
        )
    if row.teacher_edit_version != data.expected_teacher_edit_version or row.status not in {
        "suggested"
    }:
        raise ApiProblem(
            409,
            "SUGGESTION_MODIFIED_BY_TEACHER",
            "建议已被审查，请刷新后重试",
            {"current_teacher_edit_version": row.teacher_edit_version},
        )
    if data.action == "modify" and data.teacher_value is None:
        raise ApiProblem(422, "TEACHER_VALUE_REQUIRED", "修改时必须提供教师值")
    if data.action != "modify" and data.teacher_value is not None:
        raise ApiProblem(422, "TEACHER_VALUE_NOT_ALLOWED", "仅修改操作可提供教师值")
    final_value = row.normalized_value if data.action == "accept" else data.teacher_value
    if data.action == "accept" and final_value is None:
        raise ApiProblem(422, "UNKNOWN_SUGGESTION_NOT_ACCEPTABLE", "无法判断的空建议不能直接接受")
    final_value = (
        _normalized_teacher_value(row.field_name, final_value) if data.action != "reject" else None
    )
    writable = {"title", "subject", "grade", "description", "instructions"}
    if data.action != "reject" and row.field_name in writable:
        if (
            data.expected_assignment_updated_at is None
            or assignment.updated_at != data.expected_assignment_updated_at
        ):
            raise ApiProblem(409, "EDIT_CONFLICT", "作业已被其他教师修改，请刷新后重试")
        setattr(assignment, row.field_name, final_value)
        assignment.updated_at = now_utc()
    row.status = {"accept": "accepted", "modify": "modified", "reject": "rejected"}[data.action]
    row.teacher_value = final_value
    row.teacher_edit_version += 1
    row.reviewed_by = actor.id
    row.reviewed_at = now_utc()
    row.review_note = data.review_note
    revision.teacher_edit_version += 1
    _resolve_issues(
        db,
        revision.id,
        actor.id,
        {"BASIC_INFO_LOW_CONFIDENCE", "BASIC_INFO_CONFLICT", "MANUAL_FIELD_CONFIRMATION_REQUIRED"},
        data.review_note,
        row.field_name,
    )
    if data.action != "reject" and row.field_name in writable:
        _rebase_teacher_snapshot(db, revision, assignment)
    update_risk_summary(db, revision)
    audit(
        db,
        actor.id,
        f"assignment_field_suggestion.{data.action}",
        "assignment_field_suggestion",
        row.id,
        {
            "field_name": row.field_name,
            "original_suggestion": row.suggested_value,
            "teacher_final_value": final_value,
            "draft_revision_id": str(revision.id),
            "teacher_edit_version": row.teacher_edit_version,
            "reason": data.review_note,
        },
    )
    db.commit()
    db.refresh(row)
    return _suggestion_json(row)


@router.post("/api/assignment-field-suggestions/{suggestion_id}/confirm-total-score")
def confirm_total_score(
    suggestion_id: uuid.UUID, data: TotalScoreConfirmationInput, db: Db, actor: Actor
) -> dict[str, Any]:
    row = _locked_suggestion(db, actor.id, suggestion_id)
    revision, assignment = _ensure_suggestion_current(db, row)
    if row.field_name != "total_score":
        raise ApiProblem(422, "NOT_TOTAL_SCORE_SUGGESTION", "该建议不是总分建议")
    if row.teacher_edit_version != data.expected_teacher_edit_version or row.status != "suggested":
        raise ApiProblem(409, "SUGGESTION_MODIFIED_BY_TEACHER", "建议已被审查，请刷新后重试")
    if assignment.updated_at != data.expected_assignment_updated_at:
        raise ApiProblem(409, "EDIT_CONFLICT", "作业已被其他教师修改，请刷新后重试")
    assignment.total_score = data.confirmed_value
    assignment.updated_at = now_utc()
    row.status = "accepted"
    row.teacher_value = str(data.confirmed_value)
    row.teacher_edit_version += 1
    row.reviewed_by = actor.id
    row.reviewed_at = now_utc()
    row.review_note = data.review_note
    revision.teacher_edit_version += 1
    _resolve_issues(
        db,
        revision.id,
        actor.id,
        {"TOTAL_SCORE_UNCONFIRMED", "TOTAL_SCORE_CONFLICT"},
        data.review_note,
        "total_score",
    )
    _rebase_teacher_snapshot(db, revision, assignment)
    update_risk_summary(db, revision)
    audit(
        db,
        actor.id,
        "assignment_field_suggestion.confirm_total_score",
        "assignment_field_suggestion",
        row.id,
        {
            "original_suggestion": row.suggested_value,
            "teacher_final_value": str(data.confirmed_value),
            "explicit_confirmation": True,
            "draft_revision_id": str(revision.id),
            "teacher_edit_version": row.teacher_edit_version,
            "reason": data.review_note,
        },
    )
    db.commit()
    db.refresh(row)
    return _suggestion_json(row)


@router.get("/api/assignment-draft-revisions/{revision_id}/file-analyses")
def list_file_analyses(revision_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    owned_revision(db, actor.id, revision_id)
    rows = db.scalars(
        select(AssignmentSourceFileAnalysis)
        .where(
            AssignmentSourceFileAnalysis.draft_revision_id == revision_id,
            AssignmentSourceFileAnalysis.owner_id == actor.id,
        )
        .order_by(AssignmentSourceFileAnalysis.created_at)
    ).all()
    return [_analysis_json(row, db) for row in rows]


@router.get("/api/assignment-source-file-analyses/{analysis_id}")
def get_file_analysis(analysis_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    row = db.scalar(
        select(AssignmentSourceFileAnalysis).where(
            AssignmentSourceFileAnalysis.id == analysis_id,
            AssignmentSourceFileAnalysis.owner_id == actor.id,
        )
    )
    if row is None:
        raise ApiProblem(404, "FILE_ANALYSIS_NOT_FOUND", "文件分析不存在")
    return _analysis_json(row, db)


@router.patch("/api/assignment-source-file-analyses/{analysis_id}/confirmation")
def confirm_file_analysis(
    analysis_id: uuid.UUID, data: FileConfirmationInput, db: Db, actor: Actor
) -> dict[str, Any]:
    row = db.scalar(
        select(AssignmentSourceFileAnalysis)
        .where(
            AssignmentSourceFileAnalysis.id == analysis_id,
            AssignmentSourceFileAnalysis.owner_id == actor.id,
        )
        .with_for_update()
    )
    if row is None:
        raise ApiProblem(404, "FILE_ANALYSIS_NOT_FOUND", "文件分析不存在")
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.id == row.draft_revision_id)
        .with_for_update()
    )
    assignment = db.get(Assignment, row.assignment_id)
    assert revision is not None and assignment is not None
    if (
        row.analysis_status in {"stale", "superseded"}
        or revision.status in {"stale", "superseded"}
        or source_snapshot_hash(db, assignment) != row.source_snapshot_hash
    ):
        job = db.get(AssignmentGenerationJob, revision.generation_job_id)
        if job is not None:
            mark_stale(db, job, revision)
        row.analysis_status = "stale"
        db.commit()
        raise ApiProblem(409, "FILE_ANALYSIS_STALE", "文件分析已失效，请重新分析")
    if (
        row.teacher_edit_version != data.expected_teacher_edit_version
        or row.analysis_status != "suggested"
    ):
        raise ApiProblem(409, "FILE_ANALYSIS_MODIFIED_BY_TEACHER", "文件分析已确认，请刷新后重试")
    if (
        data.confirmed_role == "reference_answer"
        and data.confirmed_answer_source == "not_applicable"
    ):
        raise ApiProblem(422, "ANSWER_SOURCE_REQUIRED", "答案文件必须由教师确认答案来源")
    if (
        data.confirmed_role != "reference_answer"
        and data.confirmed_answer_source != "not_applicable"
    ):
        raise ApiProblem(422, "ANSWER_SOURCE_NOT_APPLICABLE", "非答案文件的答案来源必须为不适用")
    if row.suggested_answer_source in {
        "ai_generated",
        "third_party",
        "unknown",
    } and data.confirmed_answer_source in {"teacher_official", "publisher_official"}:
        raise ApiProblem(
            422, "UNTRUSTED_ANSWER_CANNOT_BE_OFFICIAL", "AI、第三方或未知来源答案不能标记为官方答案"
        )
    row.teacher_confirmed_role = data.confirmed_role
    row.teacher_confirmed_answer_source = data.confirmed_answer_source
    row.analysis_status = "confirmed"
    row.teacher_edit_version += 1
    row.confirmed_by = actor.id
    row.confirmed_at = now_utc()
    row.review_note = data.review_note
    revision.teacher_edit_version += 1
    _resolve_issues(
        db,
        revision.id,
        actor.id,
        {"FILE_ROLE_REVIEW_REQUIRED", "ANSWER_SOURCE_CONFIRMATION_REQUIRED"},
        data.review_note,
        str(row.stored_file_id),
    )
    update_risk_summary(db, revision)
    audit(
        db,
        actor.id,
        "assignment_source_file_analysis.confirm",
        "assignment_source_file_analysis",
        row.id,
        {
            "suggested_role": row.suggested_role,
            "suggested_answer_source": row.suggested_answer_source,
            "teacher_final_role": data.confirmed_role,
            "teacher_final_answer_source": data.confirmed_answer_source,
            "draft_revision_id": str(revision.id),
            "teacher_edit_version": row.teacher_edit_version,
            "reason": data.review_note,
        },
    )
    db.commit()
    db.refresh(row)
    return _analysis_json(row, db)


@router.get("/api/assignment-source-file-analyses/{analysis_id}/pages")
def list_page_analyses(analysis_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    source = db.scalar(
        select(AssignmentSourceFileAnalysis).where(
            AssignmentSourceFileAnalysis.id == analysis_id,
            AssignmentSourceFileAnalysis.owner_id == actor.id,
        )
    )
    if source is None:
        raise ApiProblem(404, "FILE_ANALYSIS_NOT_FOUND", "文件分析不存在")
    rows = db.scalars(
        select(AssignmentPageAnalysis)
        .where(
            AssignmentPageAnalysis.source_file_analysis_id == source.id,
            AssignmentPageAnalysis.owner_id == actor.id,
        )
        .order_by(AssignmentPageAnalysis.created_at)
    ).all()
    return [_page_analysis_json(row) for row in rows]


@router.get("/api/assignment-page-analyses/{analysis_id}")
def get_page_analysis(analysis_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    row = db.scalar(
        select(AssignmentPageAnalysis).where(
            AssignmentPageAnalysis.id == analysis_id, AssignmentPageAnalysis.owner_id == actor.id
        )
    )
    if row is None:
        raise ApiProblem(404, "PAGE_ANALYSIS_NOT_FOUND", "页面分析不存在")
    return _page_analysis_json(row)


@router.post("/api/assignment-draft-revisions/{revision_id}/activate")
def activate_draft_revision(revision_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    revision = owned_revision(db, actor.id, revision_id, for_update=True)
    assignment = owned_assignment(db, actor.id, revision.assignment_id)
    job = db.scalar(
        select(AssignmentGenerationJob)
        .where(
            AssignmentGenerationJob.id == revision.generation_job_id,
            AssignmentGenerationJob.owner_id == actor.id,
        )
        .with_for_update()
    )
    if job is None or revision.generation_job_id != job.id:
        raise ApiProblem(409, "REVISION_JOB_MISMATCH", "草稿版本与生成任务不匹配")
    latest_generation = db.scalar(
        select(AssignmentGenerationJob.generation)
        .where(AssignmentGenerationJob.assignment_id == assignment.id)
        .order_by(AssignmentGenerationJob.generation.desc())
        .limit(1)
    )
    if latest_generation != job.generation:
        raise ApiProblem(409, "GENERATION_SUPERSEDED", "只能激活最新生成任务的草稿")
    if job.status not in {"partial", "review_required", "ready"}:
        raise ApiProblem(
            409,
            "DRAFT_NOT_ACTIVATABLE",
            "生成任务尚未进入可审阅状态",
            {"status": job.status},
        )
    if revision.status not in {"draft", "review_required", "ready"}:
        raise ApiProblem(
            409,
            "DRAFT_NOT_ACTIVATABLE",
            "当前草稿状态不可激活",
            {"status": revision.status},
        )
    if source_snapshot_hash(db, assignment) != revision.source_snapshot_hash:
        mark_stale(db, job, revision)
        db.commit()
        raise ApiProblem(409, "SOURCE_CHANGED", "作业输入已变化，不能激活旧草稿")
    blocking = db.scalar(
        select(GenerationIssue.id).where(
            GenerationIssue.draft_revision_id == revision.id,
            GenerationIssue.severity == "blocking",
            GenerationIssue.resolution_status == "open",
        )
    )
    if blocking:
        raise ApiProblem(409, "DRAFT_HAS_BLOCKING_ISSUES", "草稿仍有阻断问题")
    for old in db.scalars(
        select(AssignmentDraftRevision)
        .where(
            AssignmentDraftRevision.assignment_id == assignment.id,
            AssignmentDraftRevision.status == "active",
            AssignmentDraftRevision.id != revision.id,
        )
        .with_for_update()
    ).all():
        old.status = "superseded"
        old.superseded_at = now_utc()
    revision.status = "active"
    audit(
        db,
        actor.id,
        "assignment_draft.activate",
        "assignment_draft_revision",
        revision.id,
    )
    db.commit()
    return revision_json(revision)


def _region_json(row: AssignmentQuestionExtractionRegion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "paper_page_id": str(row.paper_page_id),
        "display_order": row.display_order,
        "region_type": row.region_type,
        "x": float(row.x),
        "y": float(row.y),
        "width": float(row.width),
        "height": float(row.height),
        "confidence": float(row.confidence),
        "evidence": row.evidence,
        "source_block_ids": row.source_block_ids,
        "cross_page_group": row.cross_page_group,
    }


def _candidate_json(
    db: Session, row: AssignmentQuestionExtractionCandidate, *, variant_unresolved: bool = False
) -> dict[str, Any]:
    regions = list(
        db.scalars(
            select(AssignmentQuestionExtractionRegion)
            .where(AssignmentQuestionExtractionRegion.candidate_id == row.id)
            .order_by(
                AssignmentQuestionExtractionRegion.display_order,
                AssignmentQuestionExtractionRegion.id,
            )
        ).all()
    )
    return {
        "id": str(row.id),
        "assignment_id": str(row.assignment_id),
        "draft_revision_id": str(row.draft_revision_id),
        "paper_version_id": str(row.paper_version_id),
        "source_recognition_job_id": str(row.source_recognition_job_id)
        if row.source_recognition_job_id
        else None,
        "source_question_candidate_id": str(row.source_question_candidate_id)
        if row.source_question_candidate_id
        else None,
        "candidate_version": row.candidate_version,
        "parent_candidate_id": str(row.parent_candidate_id) if row.parent_candidate_id else None,
        "question_number": row.question_number,
        "question_type": row.question_type,
        "content_text": row.content_text,
        "content_latex": row.content_latex,
        "max_score": float(row.max_score) if row.max_score is not None else None,
        "difficulty": row.difficulty,
        "knowledge_point_suggestions": row.knowledge_point_suggestions,
        "field_confidences": row.field_confidences,
        "overall_confidence": float(row.overall_confidence),
        "extraction_method": row.extraction_method,
        "evidence": row.evidence,
        "warning_codes": row.warning_codes,
        "status": row.status,
        "manual_required": row.manual_required,
        "teacher_edit_version": row.teacher_edit_version,
        "teacher_value": row.teacher_value,
        "materialized_question_id": str(row.materialized_question_id)
        if row.materialized_question_id
        else None,
        "reviewed_by": str(row.reviewed_by) if row.reviewed_by else None,
        "reviewed_at": row.reviewed_at,
        "review_note": row.review_note,
        "regions": [_region_json(x) for x in regions],
        "server_eligible": eligible(row, regions, variant_unresolved=variant_unresolved),
    }


def _page_suggestion_json(row: PaperPageOrganizationSuggestion, page: PaperPage) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "paper_version_id": str(row.paper_version_id),
        "paper_page_id": str(row.paper_page_id),
        "source_page_number": page.source_page_number,
        "current_page_number": page.page_number,
        "current_rotation": page.rotation,
        "current_status": page.status,
        "thumbnail_available": bool(page.thumbnail_storage_key),
        "suggestion_version": row.suggestion_version,
        "suggested_page_number": row.suggested_page_number,
        "suggested_rotation": row.suggested_rotation,
        "suggested_status": row.suggested_status,
        "duplicate_of_page_id": str(row.duplicate_of_page_id) if row.duplicate_of_page_id else None,
        "variant_label": row.variant_label,
        "confidence": float(row.confidence),
        "reason_codes": row.reason_codes,
        "evidence": row.evidence,
        "status": row.status,
        "teacher_edit_version": row.teacher_edit_version,
        "reviewed_at": row.reviewed_at,
        "review_note": row.review_note,
    }


@router.get("/api/assignment-draft-revisions/{revision_id}/page-organization-suggestions")
def list_page_organization_suggestions(
    revision_id: uuid.UUID, db: Db, actor: Actor, limit: int = Query(50, ge=1, le=100)
) -> list[dict[str, Any]]:
    owned_revision(db, actor.id, revision_id)
    rows = list(
        db.scalars(
            select(PaperPageOrganizationSuggestion)
            .where(
                PaperPageOrganizationSuggestion.draft_revision_id == revision_id,
                PaperPageOrganizationSuggestion.owner_id == actor.id,
            )
            .order_by(
                PaperPageOrganizationSuggestion.suggested_page_number,
                PaperPageOrganizationSuggestion.id,
            )
            .limit(limit)
        ).all()
    )
    return [
        _page_suggestion_json(row, cast(PaperPage, db.get(PaperPage, row.paper_page_id)))
        for row in rows
    ]


@router.patch("/api/page-organization-suggestions/{suggestion_id}/disposition")
def disposition_page_organization(
    suggestion_id: uuid.UUID, data: PageOrganizationDispositionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    row = db.scalar(
        select(PaperPageOrganizationSuggestion)
        .where(
            PaperPageOrganizationSuggestion.id == suggestion_id,
            PaperPageOrganizationSuggestion.owner_id == actor.id,
        )
        .with_for_update()
    )
    if row is None:
        raise ApiProblem(404, "PAGE_SUGGESTION_NOT_FOUND", "页面建议不存在")
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.id == row.draft_revision_id)
        .with_for_update()
    )
    page = db.scalar(select(PaperPage).where(PaperPage.id == row.paper_page_id).with_for_update())
    assert revision is not None and page is not None
    if (
        row.source_snapshot_hash != data.expected_source_snapshot
        or revision.source_snapshot_hash != data.expected_source_snapshot
        or row.paper_version_id != data.expected_paper_version_id
    ):
        raise ApiProblem(409, "PAGE_SUGGESTION_STALE", "页面建议来源已变化")
    if (
        row.teacher_edit_version != data.expected_teacher_edit_version
        or revision.teacher_edit_version != data.expected_draft_revision_edit_version
        or row.status != "suggested"
    ):
        raise ApiProblem(409, "PAGE_SUGGESTION_EDIT_CONFLICT", "页面建议已被其他教师处理")
    if data.action == "modify" and data.teacher_value is None:
        raise ApiProblem(422, "TEACHER_VALUE_REQUIRED", "修改时必须提供教师值")
    if data.action != "modify" and data.teacher_value is not None:
        raise ApiProblem(422, "TEACHER_VALUE_NOT_ALLOWED", "仅修改操作可提供教师值")
    if data.action in {"accept", "modify"}:
        values = data.teacher_value or {
            "page_number": row.suggested_page_number,
            "rotation": row.suggested_rotation,
            "status": row.suggested_status,
        }
        allowed = {"page_number", "rotation", "status"}
        if set(values) - allowed:
            raise ApiProblem(422, "PAGE_VALUE_INVALID", "页面修改包含禁止字段")
        number = int(values.get("page_number", row.suggested_page_number))
        rotation = int(values.get("rotation", row.suggested_rotation))
        status = str(values.get("status", row.suggested_status))
        if (
            number <= 0
            or rotation not in {0, 90, 180, 270}
            or status not in {"ready", "excluded", "manual_required"}
        ):
            raise ApiProblem(422, "PAGE_VALUE_INVALID", "页面排序、旋转或状态无效")
        collision = db.scalar(
            select(PaperPage.id).where(
                PaperPage.paper_version_id == page.paper_version_id,
                PaperPage.page_number == number,
                PaperPage.id != page.id,
            )
        )
        if collision:
            raise ApiProblem(409, "PAGE_ORDER_CONFLICT", "目标页码已被占用，请逐页调整后重试")
        page.page_number = number
        page.rotation = rotation
        page.status = status
    row.status = {
        "accept": "accepted",
        "modify": "modified",
        "reject": "rejected",
        "mark_manual_required": "manual_required",
    }[data.action]
    row.teacher_edit_version += 1
    row.reviewed_by = actor.id
    row.reviewed_at = now_utc()
    row.review_note = data.review_note
    revision.teacher_edit_version += 1
    if data.action in {"accept", "modify"}:
        db.flush()
        assignment = db.get(Assignment, row.assignment_id)
        assert assignment is not None
        snapshot = source_snapshot_hash(db, assignment)
        revision.source_snapshot_hash = snapshot
        job = db.get(AssignmentGenerationJob, row.generation_job_id)
        if job is not None:
            job.source_snapshot_hash = snapshot
        for pending in db.scalars(
            select(PaperPageOrganizationSuggestion).where(
                PaperPageOrganizationSuggestion.draft_revision_id == revision.id,
                PaperPageOrganizationSuggestion.status == "suggested",
            )
        ).all():
            pending.source_snapshot_hash = snapshot
        for candidate in db.scalars(
            select(AssignmentQuestionExtractionCandidate).where(
                AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
                AssignmentQuestionExtractionCandidate.status == "suggested",
            )
        ).all():
            candidate.status = "stale"
    audit(
        db,
        actor.id,
        f"page_organization_suggestion.{data.action}",
        "paper_page_organization_suggestion",
        row.id,
        {
            "source_page_number": page.source_page_number,
            "draft_revision_id": str(revision.id),
            "reason": data.review_note,
        },
    )
    db.commit()
    db.refresh(row)
    return _page_suggestion_json(row, page)


@router.get("/api/assignment-draft-revisions/{revision_id}/question-extraction-candidates")
def list_question_extraction_candidates(
    revision_id: uuid.UUID, db: Db, actor: Actor, limit: int = Query(50, ge=1, le=100)
) -> list[dict[str, Any]]:
    owned_revision(db, actor.id, revision_id)
    variant = bool(
        db.scalar(
            select(GenerationIssue.id).where(
                GenerationIssue.draft_revision_id == revision_id,
                GenerationIssue.code == "VARIANT_UNRESOLVED",
                GenerationIssue.resolution_status == "open",
            )
        )
    )
    rows = db.scalars(
        select(AssignmentQuestionExtractionCandidate)
        .where(
            AssignmentQuestionExtractionCandidate.draft_revision_id == revision_id,
            AssignmentQuestionExtractionCandidate.owner_id == actor.id,
        )
        .order_by(
            AssignmentQuestionExtractionCandidate.candidate_version,
            AssignmentQuestionExtractionCandidate.question_number,
            AssignmentQuestionExtractionCandidate.id,
        )
        .limit(limit)
    ).all()
    return [_candidate_json(db, x, variant_unresolved=variant) for x in rows]


@router.get("/api/question-extraction-candidates/{candidate_id}")
def get_question_extraction_candidate(
    candidate_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    row = db.scalar(
        select(AssignmentQuestionExtractionCandidate).where(
            AssignmentQuestionExtractionCandidate.id == candidate_id,
            AssignmentQuestionExtractionCandidate.owner_id == actor.id,
        )
    )
    if row is None:
        raise ApiProblem(404, "QUESTION_CANDIDATE_NOT_FOUND", "题目候选不存在")
    return _candidate_json(db, row)


@router.get("/api/question-extraction-candidates/{candidate_id}/evidence")
def get_question_extraction_evidence(
    candidate_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    value = get_question_extraction_candidate(candidate_id, db, actor)
    return {
        "candidate_id": value["id"],
        "field_evidence": value["evidence"],
        "field_confidences": value["field_confidences"],
        "regions": value["regions"],
    }


def _ensure_candidate_current(
    db: Session,
    row: AssignmentQuestionExtractionCandidate,
    data: QuestionExtractionDispositionInput | AcceptEligibleInput,
) -> AssignmentDraftRevision:
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.id == row.draft_revision_id)
        .with_for_update()
    )
    assignment = db.get(Assignment, row.assignment_id)
    assert revision is not None and assignment is not None
    if (
        row.source_snapshot_hash != data.expected_source_snapshot
        or revision.source_snapshot_hash != data.expected_source_snapshot
        or source_snapshot_hash(db, assignment) != data.expected_source_snapshot
        or row.paper_version_id != data.expected_paper_version_id
    ):
        row.status = "stale"
        db.commit()
        raise ApiProblem(409, "QUESTION_CANDIDATE_STALE", "题目候选已失效")
    if revision.teacher_edit_version != data.expected_draft_revision_edit_version:
        raise ApiProblem(409, "QUESTION_CANDIDATE_EDIT_CONFLICT", "草稿已被其他教师修改")
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "只能物化到草稿作业")
    return revision


def _derive_draft_paper(
    db: Session,
    paper: PaperVersion,
    revision: AssignmentDraftRevision,
    actor_id: uuid.UUID,
) -> PaperVersion:
    version = (
        int(
            db.scalar(
                select(func.coalesce(func.max(PaperVersion.version), 0)).where(
                    PaperVersion.assignment_id == paper.assignment_id
                )
            )
            or 0
        )
        + 1
    )
    draft = PaperVersion(
        assignment_id=paper.assignment_id,
        version=version,
        status=VersionStatus.draft,
        source_type="ai_draft",
        created_by=actor_id,
        notes=f"Derived from PaperVersion {paper.id} for draft revision {revision.id}",
    )
    db.add(draft)
    db.flush()
    page_map: dict[uuid.UUID, uuid.UUID] = {}
    for source in db.scalars(
        select(PaperPage)
        .where(PaperPage.paper_version_id == paper.id)
        .order_by(PaperPage.page_number)
    ).all():
        copied = PaperPage(
            paper_version_id=draft.id,
            stored_file_id=source.stored_file_id,
            page_number=source.page_number,
            source_page_number=source.source_page_number,
            width=source.width,
            height=source.height,
            rotation=source.rotation,
            status=source.status,
            preview_storage_key=source.preview_storage_key,
            thumbnail_storage_key=source.thumbnail_storage_key,
        )
        db.add(copied)
        db.flush()
        page_map[source.id] = copied.id
    affected = list(
        db.scalars(
            select(AssignmentQuestionExtractionCandidate).where(
                AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
                AssignmentQuestionExtractionCandidate.paper_version_id == paper.id,
            )
        ).all()
    )
    for candidate in affected:
        candidate.paper_version_id = draft.id
        for region in db.scalars(
            select(AssignmentQuestionExtractionRegion).where(
                AssignmentQuestionExtractionRegion.candidate_id == candidate.id
            )
        ).all():
            region.paper_page_id = page_map[region.paper_page_id]
    return draft


@router.patch("/api/question-extraction-candidates/{candidate_id}/disposition")
def disposition_question_extraction(
    candidate_id: uuid.UUID, data: QuestionExtractionDispositionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    row = db.scalar(
        select(AssignmentQuestionExtractionCandidate)
        .where(
            AssignmentQuestionExtractionCandidate.id == candidate_id,
            AssignmentQuestionExtractionCandidate.owner_id == actor.id,
        )
        .with_for_update()
    )
    if row is None:
        raise ApiProblem(404, "QUESTION_CANDIDATE_NOT_FOUND", "题目候选不存在")
    revision = _ensure_candidate_current(db, row, data)
    if row.teacher_edit_version != data.expected_teacher_edit_version or row.status != "suggested":
        raise ApiProblem(409, "QUESTION_CANDIDATE_EDIT_CONFLICT", "题目候选已被处理")
    if data.action == "modify" and not data.teacher_value:
        raise ApiProblem(422, "TEACHER_VALUE_REQUIRED", "修改时必须提供教师值")
    if data.action != "modify" and data.teacher_value is not None:
        raise ApiProblem(422, "TEACHER_VALUE_NOT_ALLOWED", "仅修改操作可提供教师值")
    allowed = {
        "question_number",
        "question_type",
        "content_text",
        "content_latex",
        "max_score",
        "difficulty",
        "knowledge_points",
        "parent_candidate_id",
    }
    if data.teacher_value and set(data.teacher_value) - allowed:
        raise ApiProblem(422, "TEACHER_VALUE_INVALID", "教师修改包含禁止字段")
    if (
        data.teacher_value
        and data.teacher_value.get("max_score") is not None
        and Decimal(str(data.teacher_value["max_score"])) <= 0
    ):
        raise ApiProblem(422, "QUESTION_SCORE_INVALID", "分值必须为正数或空")
    if data.teacher_value and "question_type" in data.teacher_value:
        if data.teacher_value["question_type"] not in {
            "single_choice",
            "multiple_choice",
            "true_false",
            "fill_blank",
            "calculation",
            "short_answer",
            "proof",
            "other",
        }:
            raise ApiProblem(422, "QUESTION_TYPE_INVALID", "题型不在受控枚举中")
    if data.teacher_value and "parent_candidate_id" in data.teacher_value:
        raw_parent = data.teacher_value["parent_candidate_id"]
        row.parent_candidate_id = uuid.UUID(str(raw_parent)) if raw_parent else None
        if row.parent_candidate_id == row.id:
            raise ApiProblem(422, "PARENT_CHILD_CONFLICT", "候选不能以自身为父题")
        cursor = db.get(AssignmentQuestionExtractionCandidate, row.parent_candidate_id)
        seen = {row.id}
        while cursor is not None:
            if cursor.id in seen:
                raise ApiProblem(422, "PARENT_CHILD_CONFLICT", "父子题关系不能成环")
            if (
                cursor.draft_revision_id != row.draft_revision_id
                or cursor.paper_version_id != row.paper_version_id
            ):
                raise ApiProblem(422, "PARENT_CHILD_CONFLICT", "父题不属于同一草稿和试卷版本")
            seen.add(cursor.id)
            cursor = db.get(AssignmentQuestionExtractionCandidate, cursor.parent_candidate_id)
    regions = list(
        db.scalars(
            select(AssignmentQuestionExtractionRegion)
            .where(AssignmentQuestionExtractionRegion.candidate_id == row.id)
            .order_by(AssignmentQuestionExtractionRegion.display_order)
            .with_for_update()
        ).all()
    )
    if data.action in {"accept", "modify"}:
        paper = db.scalar(
            select(PaperVersion).where(PaperVersion.id == row.paper_version_id).with_for_update()
        )
        if paper is None:
            raise ApiProblem(409, "PAPER_VERSION_NOT_FOUND", "试卷版本不存在")
        if paper.status != VersionStatus.draft:
            paper = _derive_draft_paper(db, paper, revision, actor.id)
            row.paper_version_id = paper.id
            regions = list(
                db.scalars(
                    select(AssignmentQuestionExtractionRegion)
                    .where(AssignmentQuestionExtractionRegion.candidate_id == row.id)
                    .order_by(AssignmentQuestionExtractionRegion.display_order)
                ).all()
            )
        row.teacher_value = deepcopy(data.teacher_value) if data.teacher_value else None
        question = materialize(db, row, regions, modified=data.action == "modify")
        if row.parent_candidate_id:
            parent = db.get(AssignmentQuestionExtractionCandidate, row.parent_candidate_id)
            if (
                parent is None
                or parent.draft_revision_id != row.draft_revision_id
                or parent.paper_version_id != row.paper_version_id
            ):
                raise ApiProblem(422, "PARENT_CHILD_CONFLICT", "父题不属于同一草稿和试卷版本")
            if parent.materialized_question_id is None:
                raise ApiProblem(409, "PARENT_QUESTION_NOT_MATERIALIZED", "请先确认并物化父题")
            question.parent_question_id = parent.materialized_question_id
        assignment = db.get(Assignment, row.assignment_id)
        assert assignment is not None
        requested_points = (
            data.teacher_value.get("knowledge_points", [])
            if data.teacher_value is not None
            else row.knowledge_point_suggestions
        )
        for raw_name in requested_points[:20]:
            name = " ".join(str(raw_name).split())[:120]
            if not name:
                continue
            point = db.scalar(
                select(KnowledgePoint).where(
                    KnowledgePoint.owner_id == actor.id,
                    KnowledgePoint.subject == assignment.subject,
                    KnowledgePoint.grade == assignment.grade,
                    func.lower(KnowledgePoint.name) == name.lower(),
                )
            )
            if point is None:
                point = KnowledgePoint(
                    owner_id=actor.id,
                    subject=assignment.subject,
                    grade=assignment.grade,
                    name=name,
                )
                db.add(point)
                db.flush()
            if db.get(QuestionKnowledgePoint, (question.id, point.id)) is None:
                db.add(QuestionKnowledgePoint(question_id=question.id, knowledge_point_id=point.id))
    row.status = {
        "accept": "accepted",
        "modify": "modified",
        "reject": "rejected",
        "mark_manual_required": "manual_required",
    }[data.action]
    row.manual_required = row.manual_required or data.action == "mark_manual_required"
    row.teacher_edit_version += 1
    row.reviewed_by = actor.id
    row.reviewed_at = now_utc()
    row.review_note = data.review_note
    revision.teacher_edit_version += 1
    audit(
        db,
        actor.id,
        f"question_extraction_candidate.{data.action}",
        "assignment_question_extraction_candidate",
        row.id,
        {
            "draft_revision_id": str(revision.id),
            "materialized_question_id": str(row.materialized_question_id)
            if row.materialized_question_id
            else None,
            "reason": data.review_note,
        },
    )
    db.commit()
    db.refresh(row)
    return _candidate_json(db, row)


@router.post(
    "/api/assignment-draft-revisions/{revision_id}/question-extraction-candidates/accept-eligible"
)
def accept_eligible_question_candidates(
    revision_id: uuid.UUID, data: AcceptEligibleInput, db: Db, actor: Actor
) -> dict[str, Any]:
    revision = owned_revision(db, actor.id, revision_id, for_update=True)
    rows = list(
        db.scalars(
            select(AssignmentQuestionExtractionCandidate)
            .where(
                AssignmentQuestionExtractionCandidate.draft_revision_id == revision_id,
                AssignmentQuestionExtractionCandidate.owner_id == actor.id,
            )
            .order_by(
                AssignmentQuestionExtractionCandidate.candidate_version,
                AssignmentQuestionExtractionCandidate.question_number,
            )
            .with_for_update()
        ).all()
    )
    rows.sort(key=lambda item: (item.parent_candidate_id is not None, item.question_number or ""))
    if rows:
        _ensure_candidate_current(db, rows[0], data)
    variant = bool(
        db.scalar(
            select(GenerationIssue.id).where(
                GenerationIssue.draft_revision_id == revision_id,
                GenerationIssue.code.in_(
                    {
                        "VARIANT_UNRESOLVED",
                        "QUESTION_NUMBER_CONFLICT",
                        "PARENT_CHILD_CONFLICT",
                        "READING_ORDER_CONFLICT",
                    }
                ),
                GenerationIssue.resolution_status == "open",
            )
        )
    )
    accepted = []
    for row in rows:
        regions = list(
            db.scalars(
                select(AssignmentQuestionExtractionRegion)
                .where(AssignmentQuestionExtractionRegion.candidate_id == row.id)
                .order_by(AssignmentQuestionExtractionRegion.display_order)
            ).all()
        )
        if eligible(row, regions, variant_unresolved=variant):
            parent = None
            if row.parent_candidate_id:
                parent = db.get(AssignmentQuestionExtractionCandidate, row.parent_candidate_id)
                if parent is None or parent.materialized_question_id is None:
                    continue
            question = materialize(db, row, regions)
            if parent is not None:
                question.parent_question_id = parent.materialized_question_id
            row.status = "accepted"
            row.teacher_edit_version += 1
            row.reviewed_by = actor.id
            row.reviewed_at = now_utc()
            accepted.append(str(row.id))
    if accepted:
        revision.teacher_edit_version += 1
        audit(
            db,
            actor.id,
            "question_extraction_candidate.accept_eligible",
            "assignment_draft_revision",
            revision.id,
            {"candidate_ids": accepted},
        )
    db.commit()
    return {
        "accepted_candidate_ids": accepted,
        "accepted_count": len(accepted),
        "server_decided": True,
    }
