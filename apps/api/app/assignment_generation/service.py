import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.domain import ApiProblem, audit
from app.assignment_generation.providers import select_provider
from app.assignment_generation.snapshot import canonical_hash, source_snapshot_hash
from app.core.config import get_settings
from app.models import (
    Assignment,
    AssignmentDraftRevision,
    AssignmentFieldSuggestion,
    AssignmentGenerationJob,
    AssignmentPageAnalysis,
    AssignmentQuestionExtractionCandidate,
    AssignmentSourceFileAnalysis,
    GenerationIssue,
    GenerationStageResult,
    PaperPage,
    PaperPageOrganizationSuggestion,
    PaperVersion,
    Question,
    QuestionStatus,
    now_utc,
)


def autofill_total_score_from_draft_questions(
    db: Session, revision: AssignmentDraftRevision, actor_id: uuid.UUID
) -> Decimal | None:
    """Fill a missing total from the complete materialized PDF question draft."""
    db.flush()
    assignment = db.get(Assignment, revision.assignment_id)
    if assignment is None or assignment.total_score is not None:
        return None
    latest_version = db.scalar(
        select(func.max(AssignmentQuestionExtractionCandidate.candidate_version)).where(
            AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id
        )
    )
    if latest_version is None:
        return None
    rows = list(
        db.scalars(
            select(AssignmentQuestionExtractionCandidate).where(
                AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
                AssignmentQuestionExtractionCandidate.candidate_version == latest_version,
            )
        )
    )
    if not rows or any(row.materialized_question_id is None for row in rows):
        return None
    paper_ids = {row.paper_version_id for row in rows}
    if len(paper_ids) != 1:
        return None
    questions = list(
        db.scalars(
            select(Question).where(
                Question.paper_version_id == next(iter(paper_ids)),
                Question.status == QuestionStatus.active,
            )
        )
    )
    materialized_ids = {row.materialized_question_id for row in rows}
    if (
        len(materialized_ids) != len(rows)
        or {question.id for question in questions} != materialized_ids
        or any(question.max_score is None for question in questions)
    ):
        return None
    total = sum((Decimal(str(question.max_score)) for question in questions), Decimal("0"))
    if total <= 0:
        return None
    assignment.total_score = total
    assignment.updated_at = now_utc()
    db.flush()
    snapshot = source_snapshot_hash(db, assignment)
    revision.source_snapshot_hash = snapshot
    job = db.get(AssignmentGenerationJob, revision.generation_job_id)
    if job is not None:
        job.source_snapshot_hash = snapshot
    for row in rows:
        row.source_snapshot_hash = snapshot
    for source_file_analysis in db.scalars(
        select(AssignmentSourceFileAnalysis).where(
            AssignmentSourceFileAnalysis.draft_revision_id == revision.id,
            AssignmentSourceFileAnalysis.analysis_status == "suggested",
        )
    ):
        source_file_analysis.source_snapshot_hash = snapshot
    for page_analysis in db.scalars(
        select(AssignmentPageAnalysis).where(
            AssignmentPageAnalysis.draft_revision_id == revision.id
        )
    ):
        page_analysis.source_snapshot_hash = snapshot
    for page_suggestion in db.scalars(
        select(PaperPageOrganizationSuggestion).where(
            PaperPageOrganizationSuggestion.draft_revision_id == revision.id,
            PaperPageOrganizationSuggestion.status == "suggested",
        )
    ):
        page_suggestion.source_snapshot_hash = snapshot
    for field_suggestion in db.scalars(
        select(AssignmentFieldSuggestion).where(
            AssignmentFieldSuggestion.draft_revision_id == revision.id,
            AssignmentFieldSuggestion.field_name == "total_score",
            AssignmentFieldSuggestion.status == "suggested",
        )
    ):
        field_suggestion.status = "superseded"
    now = now_utc()
    for generation_issue in db.scalars(
        select(GenerationIssue).where(
            GenerationIssue.draft_revision_id == revision.id,
            GenerationIssue.code == "TOTAL_SCORE_UNCONFIRMED",
            GenerationIssue.resolution_status == "open",
        )
    ):
        generation_issue.resolution_status = "resolved"
        generation_issue.resolved_by = actor_id
        generation_issue.resolved_at = now
        generation_issue.resolution_note = "已根据 PDF 中完整的题目分值自动汇总作业总分。"
    audit(
        db,
        actor_id,
        "assignment.total_score.autofill",
        "assignment",
        assignment.id,
        {
            "total_score": str(total),
            "source": "materialized_pdf_question_scores",
            "explicit_confirmation_required": False,
        },
    )
    return total


STAGES = (
    "analyzing",
    "processing_pages",
    "extracting_questions",
    "generating_rubrics",
    "validating",
)
ACTIVE_STATUSES = {"queued", *STAGES}
TERMINAL_STATUSES = {
    "review_required",
    "ready",
    "partial",
    "failed",
    "cancelled",
    "stale",
}
ALL_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
TRANSITIONS = {
    "queued": {"analyzing", "cancelled", "failed", "stale"},
    "analyzing": {"processing_pages", "cancelled", "failed", "stale"},
    "processing_pages": {"extracting_questions", "cancelled", "failed", "stale"},
    "extracting_questions": {"generating_rubrics", "cancelled", "failed", "stale"},
    "generating_rubrics": {"validating", "cancelled", "failed", "stale"},
    "validating": {"partial", "review_required", "ready", "cancelled", "failed", "stale"},
    "partial": {"cancelled", "stale"},
    "failed": {"cancelled", "stale"},
    "review_required": {"ready", "stale"},
    "ready": {"stale"},
    "cancelled": set(),
    "stale": set(),
}
PROGRESS = {
    "queued": 0,
    "analyzing": 10,
    "processing_pages": 30,
    "extracting_questions": 55,
    "generating_rubrics": 75,
    "validating": 90,
    "partial": 100,
    "review_required": 100,
    "ready": 100,
}


def transition(job: AssignmentGenerationJob, target: str) -> None:
    if target not in ALL_STATUSES or target not in TRANSITIONS.get(job.status, set()):
        raise ApiProblem(
            409,
            "GENERATION_INVALID_TRANSITION",
            f"不能从 {job.status} 转换为 {target}",
            {"from": job.status, "to": target},
        )
    job.status = target
    job.current_stage = target if target in STAGES else job.current_stage
    if target in PROGRESS:
        job.progress = max(job.progress, PROGRESS[target])
    elif target in {"failed", "cancelled", "stale"}:
        job.progress = min(job.progress, 99)
    if target == "cancelled":
        job.cancelled_at = now_utc()
    if target == "stale":
        job.stale_at = now_utc()
    if target in TERMINAL_STATUSES:
        job.completed_at = now_utc()


def owned_assignment(db: Session, owner_id: uuid.UUID, assignment_id: uuid.UUID) -> Assignment:
    item = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.owner_id == owner_id)
    )
    if item is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    return item


def owned_job(
    db: Session,
    owner_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> AssignmentGenerationJob:
    statement = select(AssignmentGenerationJob).where(
        AssignmentGenerationJob.id == job_id,
        AssignmentGenerationJob.owner_id == owner_id,
    )
    if for_update:
        statement = statement.with_for_update()
    item = db.scalar(statement)
    if item is None:
        raise ApiProblem(404, "GENERATION_JOB_NOT_FOUND", "生成任务不存在")
    return item


def owned_revision(
    db: Session,
    owner_id: uuid.UUID,
    revision_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> AssignmentDraftRevision:
    statement = select(AssignmentDraftRevision).where(
        AssignmentDraftRevision.id == revision_id,
        AssignmentDraftRevision.owner_id == owner_id,
    )
    if for_update:
        statement = statement.with_for_update()
    item = db.scalar(statement)
    if item is None:
        raise ApiProblem(404, "DRAFT_REVISION_NOT_FOUND", "草稿版本不存在")
    return item


def create_job(
    db: Session,
    owner_id: uuid.UUID,
    assignment_id: uuid.UUID,
    idempotency_key: str,
    requested_provider: str | None,
    expected_snapshot: str | None,
) -> tuple[AssignmentGenerationJob, AssignmentDraftRevision, bool]:
    assignment = owned_assignment(db, owner_id, assignment_id)
    settings = get_settings()
    if assignment.status == "published":
        raise ApiProblem(409, "ASSIGNMENT_ALREADY_PUBLISHED", "已发布作业不能启动后台生成任务")
    if not settings.assignment_generation_enabled:
        raise ApiProblem(503, "ASSIGNMENT_GENERATION_DISABLED", "AI 草稿生成功能当前未启用")
    if not settings.assignment_generation_allow_teacher_start:
        raise ApiProblem(403, "ASSIGNMENT_GENERATION_START_DISABLED", "教师当前不能启动生成任务")
    if not settings.assignment_generation_suggestion_only:
        raise ApiProblem(503, "SUGGESTION_ONLY_REQUIRED", "生成任务必须保持仅建议模式")
    snapshot = source_snapshot_hash(db, assignment)
    requested_mode = (
        requested_provider
        if settings.app_env == "test" and requested_provider is not None
        else settings.assignment_generation_provider
    )
    provider = select_provider(settings, requested_mode)
    request_fingerprint = canonical_hash(
        {
            "assignment_id": str(assignment_id),
            "source_snapshot_hash": snapshot,
            "requested_provider_mode": requested_mode,
        }
    )
    existing = db.scalar(
        select(AssignmentGenerationJob).where(
            AssignmentGenerationJob.owner_id == owner_id,
            AssignmentGenerationJob.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.request_fingerprint != request_fingerprint:
            raise ApiProblem(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "幂等键已用于不同的生成请求",
            )
        revision = db.scalar(
            select(AssignmentDraftRevision).where(
                AssignmentDraftRevision.generation_job_id == existing.id
            )
        )
        assert revision is not None
        return existing, revision, True
    if expected_snapshot and expected_snapshot != snapshot:
        raise ApiProblem(409, "SOURCE_SNAPSHOT_MISMATCH", "作业输入已发生变化")
    active = db.scalar(
        select(AssignmentGenerationJob.id).where(
            AssignmentGenerationJob.assignment_id == assignment_id,
            AssignmentGenerationJob.status.in_(ACTIVE_STATUSES),
        )
    )
    if active:
        raise ApiProblem(409, "GENERATION_ALREADY_ACTIVE", "该作业已有活动生成任务")
    generation = (
        db.scalar(
            select(func.max(AssignmentGenerationJob.generation)).where(
                AssignmentGenerationJob.assignment_id == assignment_id
            )
        )
        or 0
    ) + 1
    revision_number = (
        db.scalar(
            select(func.max(AssignmentDraftRevision.revision)).where(
                AssignmentDraftRevision.assignment_id == assignment_id
            )
        )
        or 0
    ) + 1
    parent = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.assignment_id == assignment_id)
        .order_by(AssignmentDraftRevision.revision.desc())
        .limit(1)
    )
    job = AssignmentGenerationJob(
        owner_id=owner_id,
        assignment_id=assignment_id,
        generation=generation,
        status="queued",
        progress=0,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        source_snapshot_hash=snapshot,
        provider_mode=provider.name,
        provider_config_version=settings.assignment_generation_provider_config_version,
        prompt_version=settings.assignment_generation_prompt_version,
        schema_version=settings.assignment_generation_schema_version,
        max_attempts=settings.assignment_generation_max_attempts,
    )
    db.add(job)
    db.flush()
    revision = AssignmentDraftRevision(
        owner_id=owner_id,
        assignment_id=assignment_id,
        generation_job_id=job.id,
        revision=revision_number,
        parent_revision_id=parent.id if parent else None,
        source_snapshot_hash=snapshot,
        status="draft",
        draft_payload={
            "orchestration_version": "v1",
            "generation": generation,
            "stages": {},
            "notice": "AI 仅生成草稿，不能发布作业",
        },
        risk_summary={"info": 0, "warning": 0, "blocking": 0},
        created_by_type="worker",
    )
    db.add(revision)
    db.flush()
    audit(
        db,
        owner_id,
        "assignment_generation.create",
        "assignment_generation_job",
        job.id,
        {"assignment_id": str(assignment_id), "generation": generation},
    )
    return job, revision, False


def issue(
    db: Session,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision | None,
    stage: str | None,
    severity: str,
    code: str,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> GenerationIssue:
    row = GenerationIssue(
        owner_id=job.owner_id,
        assignment_id=job.assignment_id,
        job_id=job.id,
        draft_revision_id=revision.id if revision else None,
        stage=stage,
        severity=severity,
        code=code,
        message=message,
        evidence=evidence or {},
    )
    db.add(row)
    return row


def update_risk_summary(db: Session, revision: AssignmentDraftRevision) -> None:
    rows = db.execute(
        select(GenerationIssue.severity, func.count())
        .where(
            GenerationIssue.draft_revision_id == revision.id,
            GenerationIssue.resolution_status == "open",
        )
        .group_by(GenerationIssue.severity)
    ).all()
    counts = {"info": 0, "warning": 0, "blocking": 0}
    counts.update({severity: count for severity, count in rows})
    revision.risk_summary = counts


def mark_stale(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision | None
) -> None:
    if job.status != "stale":
        transition(job, "stale")
        issue(
            db,
            job,
            revision,
            job.current_stage,
            "blocking",
            "SOURCE_CHANGED",
            "生成输入已变化，旧任务和草稿不能继续写入",
        )
        if revision:
            revision.status = "stale"
            update_risk_summary(db, revision)


def ensure_current(
    db: Session,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision,
    expected_edit_version: int | None = None,
) -> str | None:
    if job.status == "stale" or revision.status in {"stale", "superseded"}:
        return "GENERATION_SUPERSEDED"
    if job.cancel_requested_at or job.status == "cancelled":
        return "CANCEL_REQUESTED"
    if revision.generation_job_id != job.id:
        return "REVISION_JOB_MISMATCH"
    latest = db.scalar(
        select(func.max(AssignmentGenerationJob.generation)).where(
            AssignmentGenerationJob.assignment_id == job.assignment_id
        )
    )
    if latest != job.generation:
        return "GENERATION_SUPERSEDED"
    assignment = db.get(Assignment, job.assignment_id)
    if assignment is None or source_snapshot_hash(db, assignment) != job.source_snapshot_hash:
        mark_stale(db, job, revision)
        return "SOURCE_CHANGED"
    if expected_edit_version is not None and revision.teacher_edit_version != expected_edit_version:
        return "DRAFT_MODIFIED_BY_TEACHER"
    return None


def prepare_stage_retry(job: AssignmentGenerationJob, stage: str) -> None:
    if job.status not in {"failed", "partial"} or stage not in STAGES:
        raise ApiProblem(
            409,
            "GENERATION_RETRY_NOT_ALLOWED",
            "当前任务状态不允许重试此阶段",
            {"status": job.status, "stage": stage},
        )
    job.status = "queued"
    job.current_stage = stage
    job.completed_at = None
    job.cancelled_at = None
    job.stale_at = None
    job.cancel_requested_at = None
    job.error_code = None
    job.error_message = None
    job.retryable = False


def complete_stage_retry(job: AssignmentGenerationJob) -> None:
    if job.status not in STAGES:
        raise ApiProblem(
            409,
            "GENERATION_INVALID_TRANSITION",
            "重试阶段尚未运行",
            {"status": job.status},
        )
    job.status = "partial"
    job.progress = 100
    job.retryable = False
    job.error_code = None
    job.error_message = None
    job.completed_at = now_utc()


def has_retryable_stage(db: Session, job: AssignmentGenerationJob) -> bool:
    """Return whether any latest stage result still has retry budget.

    ``job.max_attempts`` is a per-stage safety limit.  A teacher may need to
    resolve more than one sequential prerequisite (file roles, page layout,
    then questions), so retries of different stages must not consume a shared
    whole-job budget.
    """
    rows = db.scalars(
        select(GenerationStageResult)
        .where(GenerationStageResult.job_id == job.id)
        .order_by(
            GenerationStageResult.stage,
            GenerationStageResult.stage_generation.desc(),
        )
    ).all()
    latest: dict[str, GenerationStageResult] = {}
    for row in rows:
        latest.setdefault(row.stage, row)
    return any(
        row.status in {"failed", "unavailable", "discarded"}
        and row.stage_generation < job.max_attempts
        for row in latest.values()
    )


def stage_history(db: Session, job_id: uuid.UUID) -> list[GenerationStageResult]:
    return list(
        db.scalars(
            select(GenerationStageResult)
            .where(GenerationStageResult.job_id == job_id)
            .order_by(
                GenerationStageResult.created_at,
                GenerationStageResult.stage_generation,
            )
        ).all()
    )


def job_json(db: Session, job: AssignmentGenerationJob) -> dict[str, Any]:
    revision = db.scalar(
        select(AssignmentDraftRevision).where(AssignmentDraftRevision.generation_job_id == job.id)
    )
    issues = db.scalars(
        select(GenerationIssue)
        .where(GenerationIssue.job_id == job.id)
        .order_by(GenerationIssue.created_at)
    ).all()
    return {
        "id": str(job.id),
        "assignment_id": str(job.assignment_id),
        "generation": job.generation,
        "status": job.status,
        "current_stage": job.current_stage,
        "progress": job.progress,
        "source_snapshot_hash": job.source_snapshot_hash,
        "provider_mode": job.provider_mode,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "retryable": job.retryable,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "cancel_requested_at": job.cancel_requested_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "revision": revision_json(revision) if revision else None,
        "stages": [stage_json(row) for row in stage_history(db, job.id)],
        "issues": [
            {
                "id": str(row.id),
                "stage": row.stage,
                "severity": row.severity,
                "code": row.code,
                "message": row.message,
                "resolution_status": row.resolution_status,
            }
            for row in issues
        ],
    }


def stage_json(row: GenerationStageResult) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "stage": row.stage,
        "stage_generation": row.stage_generation,
        "status": row.status,
        "error_code": row.error_code,
        "result_payload": row.result_payload,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
    }


def revision_json(revision: AssignmentDraftRevision) -> dict[str, Any]:
    return {
        "id": str(revision.id),
        "assignment_id": str(revision.assignment_id),
        "generation_job_id": str(revision.generation_job_id),
        "revision": revision.revision,
        "parent_revision_id": str(revision.parent_revision_id)
        if revision.parent_revision_id
        else None,
        "source_snapshot_hash": revision.source_snapshot_hash,
        "status": revision.status,
        "draft_payload": revision.draft_payload,
        "risk_summary": revision.risk_summary,
        "teacher_edit_version": revision.teacher_edit_version,
        "created_at": revision.created_at,
        "updated_at": revision.updated_at,
    }


def next_stage_generation(db: Session, job_id: uuid.UUID, stage: str) -> int:
    return (
        db.scalar(
            select(func.max(GenerationStageResult.stage_generation)).where(
                GenerationStageResult.job_id == job_id,
                GenerationStageResult.stage == stage,
            )
        )
        or 0
    ) + 1


def pages_for_job(db: Session, job: AssignmentGenerationJob) -> Iterable[PaperPage]:
    assignment = db.get(Assignment, job.assignment_id)
    if not assignment:
        return []
    paper_id = assignment.active_paper_version_id
    if not paper_id:
        paper_id = db.scalar(
            select(PaperVersion.id)
            .where(PaperVersion.assignment_id == assignment.id)
            .order_by(PaperVersion.version.desc())
        )
    if not paper_id:
        return []
    return db.scalars(select(PaperPage).where(PaperPage.paper_version_id == paper_id)).all()
