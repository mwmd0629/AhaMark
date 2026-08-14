from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NoReturn

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.request_id import celery_request_headers
from app.models import (
    Assignment,
    AuditLog,
    CodexWorkItem,
    GradingBatch,
    ProcessingRun,
    ProcessingRunCommand,
    ProcessingStep,
    Question,
    QuestionStatus,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionProcessingJob,
    SubmissionRecognitionJob,
    now_utc,
)
from app.processing.automatic_confirmation import (
    auto_confirm_deterministic_recognition,
    auto_confirm_deterministic_regions,
)
from app.processing.codex_local import (
    CodexLocalProblem,
    materialize_work_items,
    validate_applied_work_item_current,
)
from app.processing.contracts import (
    PROCESSING_MANIFEST_SCHEMA,
    ProcessingInputError,
    build_command_hash,
    build_request_hash,
    canonical_hash,
)
from app.processing.input_snapshot import build_processing_input_snapshot
from app.recognition.answer_providers import (
    provider_from_settings as recognition_provider_from_settings,
)
from app.recognition.submission_processing import PROCESSING_VERSION

ACTIVE_RUN_STATUSES = {
    "queued",
    "running",
    "waiting_input",
    "waiting_codex",
    "awaiting_teacher_review",
    "partially_failed",
}
TERMINAL_SUBMISSION_STATUSES = {"finalized", "merged", "voided"}
RETRYABLE_STEP_STATUSES = {"retryable_failed"}
RECOGNITION_BLOCKERS = {
    "RECOGNITION_EVIDENCE_NOT_CONFIRMED",
    "SEGMENTATION_CONFIRMATION_REQUIRED",
    "STUDENT_ANSWERS_REQUIRED",
}
READINESS_BLOCKERS = RECOGNITION_BLOCKERS | {
    "STRUCTURED_SET_REQUIRED",
    "STRUCTURED_SET_STALE",
}
ACTIVE_STEP_STATUSES = {"pending", "dispatched", "running"}
TERMINAL_STEP_STATUSES = {"succeeded", "terminal_failed", "stale", "cancelled"}
TERMINAL_WORK_ITEM_STATUSES = {"applied", "terminal_failed", "stale", "cancelled"}
COMMAND_KEY_CONSTRAINT = "uq_processing_run_command_owner_idempotency"
PROMPT_VERSION = "codex-local-v1"
SCHEMA_VERSION = "criterion-suggestion-v1"
CONFIG_VERSION = "suggestion-only-v1"


@dataclass(frozen=True)
class OrchestratorProblem(RuntimeError):
    status: int
    code: str
    message: str
    details: dict[str, Any] | None = None


def _fail(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> NoReturn:
    raise OrchestratorProblem(status, code, message, details)


def _owned_batch_for_update(db: Session, owner_id: uuid.UUID, batch_id: uuid.UUID) -> GradingBatch:
    batch = db.scalar(
        select(GradingBatch)
        .where(GradingBatch.id == batch_id, GradingBatch.owner_id == owner_id)
        .with_for_update()
    )
    if batch is None:
        _fail(404, "GRADING_BATCH_NOT_FOUND", "Grading batch not found")
    return batch


def _owned_run(
    db: Session,
    owner_id: uuid.UUID,
    batch_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    lock: bool = False,
) -> ProcessingRun:
    statement = select(ProcessingRun).where(
        ProcessingRun.id == run_id,
        ProcessingRun.grading_batch_id == batch_id,
        ProcessingRun.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    run = db.scalar(statement)
    if run is None:
        _fail(404, "PROCESSING_RUN_NOT_FOUND", "Processing run not found")
    return run


def _latest_run(db: Session, batch_id: uuid.UUID) -> ProcessingRun | None:
    return db.scalar(
        select(ProcessingRun)
        .where(ProcessingRun.grading_batch_id == batch_id)
        .order_by(ProcessingRun.generation.desc(), ProcessingRun.id)
        .limit(1)
    )


def get_latest_processing_run(
    db: Session, *, owner_id: uuid.UUID, batch_id: uuid.UUID
) -> ProcessingRun | None:
    owned_batch_id = db.scalar(
        select(GradingBatch.id).where(
            GradingBatch.id == batch_id,
            GradingBatch.owner_id == owner_id,
        )
    )
    if owned_batch_id is None:
        _fail(404, "GRADING_BATCH_NOT_FOUND", "Grading batch not found")
    return _latest_run(db, batch_id)


def _command_replay(
    db: Session,
    *,
    owner_id: uuid.UUID,
    idempotency_key: str,
    request_hash: str,
) -> ProcessingRun | None:
    command = db.scalar(
        select(ProcessingRunCommand).where(
            ProcessingRunCommand.owner_id == owner_id,
            ProcessingRunCommand.idempotency_key == idempotency_key,
        )
    )
    if command is None:
        return None
    if command.request_hash != request_hash:
        _fail(
            409,
            "IDEMPOTENCY_KEY_CONFLICT",
            "Idempotency key was already used for a different processing command",
        )
    run = db.get(ProcessingRun, command.result_run_id)
    if run is None:
        _fail(409, "PROCESSING_COMMAND_RESULT_MISSING", "Command result is unavailable")
    return run


def _is_command_key_conflict(exc: IntegrityError) -> bool:
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diagnostic, "constraint_name", None) == COMMAND_KEY_CONSTRAINT


def _commit_with_command_replay(
    db: Session,
    *,
    result: ProcessingRun,
    owner_id: uuid.UUID,
    idempotency_key: str,
    request_hash: str,
) -> ProcessingRun:
    try:
        db.commit()
    except IntegrityError as exc:
        if not _is_command_key_conflict(exc):
            raise
        db.rollback()
        replay = _command_replay(
            db,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is None:
            raise
        return replay
    return result


def _stale_run_children(db: Session, run: ProcessingRun) -> None:
    timestamp = now_utc()
    steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == run.id)
            .order_by(ProcessingStep.id)
            .with_for_update()
        )
    )
    step_ids = [step.id for step in steps]
    work_items = (
        list(
            db.scalars(
                select(CodexWorkItem)
                .where(CodexWorkItem.processing_step_id.in_(step_ids))
                .order_by(CodexWorkItem.id)
                .with_for_update()
            )
        )
        if step_ids
        else []
    )
    for item in work_items:
        if item.status not in TERMINAL_WORK_ITEM_STATUSES:
            item.status = "stale"
            item.stale_at = timestamp
        item.lease_token_hash = None
        item.lease_owner = None
        item.lease_expires_at = None
    for step in steps:
        if step.status not in TERMINAL_STEP_STATUSES:
            step.status = "stale"
            step.stale_at = timestamp
            step.dispatch_token = None
            step.dispatch_owner = None
            step.dispatch_lease_expires_at = None
    run.status = "stale"
    run.stale_at = timestamp


def _aggregate_run_state(
    steps: list[ProcessingStep], pending_codex_count: int
) -> tuple[str, int, int]:
    completed = sum(step.status == "succeeded" for step in steps)
    failed = sum(step.status in {"retryable_failed", "terminal_failed"} for step in steps)
    statuses = {step.status for step in steps}
    if pending_codex_count:
        status = "waiting_codex"
    elif statuses & ACTIVE_STEP_STATUSES:
        status = "running"
    elif "blocked_review" in statuses:
        status = "waiting_input"
    elif failed:
        status = "partially_failed" if completed else "failed"
    elif steps and "succeeded" in statuses and statuses <= {"succeeded", "stale"}:
        status = "awaiting_teacher_review"
    else:
        status = "waiting_input"
    return status, completed, failed


def _reconcile_codex_children(
    db: Session, *, run: ProcessingRun, steps: list[ProcessingStep]
) -> int:
    """Project child state into parents without locking child rows."""
    codex_steps = {step.id: step for step in steps if step.kind == "codex_suggestion"}
    if not codex_steps:
        return 0
    items = list(
        db.scalars(
            select(CodexWorkItem)
            .where(CodexWorkItem.processing_step_id.in_(codex_steps))
            .order_by(CodexWorkItem.id)
        )
    )
    items_by_step = {item.processing_step_id: item for item in items}
    now = now_utc()
    pending = 0
    for step_id, step in codex_steps.items():
        item = items_by_step.get(step_id)
        if item is None:
            if step.status not in TERMINAL_STEP_STATUSES:
                step.status = "terminal_failed"
                step.retryable = False
                step.error_code = "CODEX_WORK_ITEM_MISSING"
                step.failed_at = now
            continue
        if (
            item.owner_id != run.owner_id
            or item.grading_batch_id != run.grading_batch_id
            or item.generation != run.generation
            or item.submission_id != step.submission_id
            or item.student_answer_id != step.student_answer_id
        ):
            step.status = "stale"
            step.retryable = False
            step.error_code = "CODEX_WORK_SCOPE_STALE"
            step.stale_at = now
            continue
        if item.status == "applied":
            try:
                validate_applied_work_item_current(db, item)
            except CodexLocalProblem as exc:
                step.status = "stale"
                step.retryable = False
                step.error_code = exc.code
                step.error_message = exc.message
                step.stale_at = now
                continue
            step.status = "succeeded"
            step.retryable = False
            step.error_code = None
            step.error_message = None
            step.completed_at = item.applied_at or now
            continue
        if item.status in {"queued", "leased", "submitted"}:
            pending += 1
            step.status = "dispatched" if item.status == "queued" else "running"
            step.error_code = None
            step.error_message = None
            continue
        if item.status == "retryable_failed":
            step.status = "retryable_failed"
            step.retryable = True
            step.error_code = item.error_code
            step.error_message = item.error_message
            step.failed_at = item.failed_at or now
        elif item.status == "terminal_failed":
            step.status = "terminal_failed"
            step.retryable = False
            step.error_code = item.error_code
            step.error_message = item.error_message
            step.failed_at = item.failed_at or now
        elif item.status == "stale":
            step.status = "stale"
            step.retryable = False
            step.error_code = item.error_code
            step.error_message = item.error_message
            step.stale_at = item.stale_at or now
        elif item.status == "cancelled":
            step.status = "cancelled"
            step.retryable = False
            step.error_code = item.error_code
            step.error_message = item.error_message
            step.cancelled_at = item.cancelled_at or now
        else:
            step.status = "terminal_failed"
            step.retryable = False
            step.error_code = "CODEX_WORK_STATE_INVALID"
            step.failed_at = now
    return pending


def _recognition_input(
    db: Session, submission_id: uuid.UUID
) -> tuple[list[StudentAnswer], list[StudentAnswerRegion]] | None:
    processing_job = db.scalar(
        select(SubmissionProcessingJob)
        .where(SubmissionProcessingJob.submission_id == submission_id)
        .order_by(SubmissionProcessingJob.created_at.desc(), SubmissionProcessingJob.id)
    )
    if processing_job is None or processing_job.status != "completed":
        return None
    answers = _active_paper_answers(db, submission_id=submission_id)
    if not answers:
        return None
    regions = list(
        db.scalars(
            select(StudentAnswerRegion)
            .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
            .join(SubmissionPage, SubmissionPage.id == StudentAnswerRegion.submission_page_id)
            .where(
                StudentAnswer.submission_id == submission_id,
                StudentAnswer.id.in_([answer.id for answer in answers]),
                StudentAnswerRegion.status == "confirmed",
            )
            .order_by(
                SubmissionPage.page_number,
                StudentAnswerRegion.y,
                StudentAnswerRegion.x,
                StudentAnswerRegion.id,
            )
        )
    )
    answer_ids_with_regions = {region.student_answer_id for region in regions}
    if any(answer.id not in answer_ids_with_regions for answer in answers):
        return None
    return answers, regions


def _active_paper_answers(
    db: Session, *, submission_id: uuid.UUID, lock: bool = False
) -> list[StudentAnswer]:
    active_paper_version_id = db.scalar(
        select(Assignment.active_paper_version_id)
        .join(GradingBatch, GradingBatch.assignment_id == Assignment.id)
        .join(Submission, Submission.grading_batch_id == GradingBatch.id)
        .where(Submission.id == submission_id)
    )
    if active_paper_version_id is None:
        return []
    statement = (
        select(StudentAnswer)
        .join(Question, Question.id == StudentAnswer.question_id)
        .where(
            StudentAnswer.submission_id == submission_id,
            Question.paper_version_id == active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
        .order_by(StudentAnswer.id)
    )
    if lock:
        statement = statement.with_for_update(of=StudentAnswer)
    return list(db.scalars(statement))


def _materialize_submission_processing_jobs(
    db: Session, *, run: ProcessingRun, steps: list[ProcessingStep]
) -> list[uuid.UUID]:
    created: list[uuid.UUID] = []
    by_submission: dict[uuid.UUID, list[ProcessingStep]] = {}
    for step in steps:
        if step.kind == "recognition" and step.recognition_job_id is None:
            by_submission.setdefault(step.submission_id, []).append(step)
    for submission_id in sorted(by_submission, key=str):
        submission_steps = by_submission[submission_id]
        job = db.scalar(
            select(SubmissionProcessingJob)
            .where(
                SubmissionProcessingJob.owner_id == run.owner_id,
                SubmissionProcessingJob.submission_id == submission_id,
            )
            .order_by(
                SubmissionProcessingJob.created_at.desc(),
                SubmissionProcessingJob.id,
            )
            .limit(1)
            .with_for_update()
        )
        if job is None:
            job = SubmissionProcessingJob(
                owner_id=run.owner_id,
                submission_id=submission_id,
                idempotency_key=f"processing:{run.id}:{submission_id}",
                status="queued",
                stage="page_processing",
                config_version=PROCESSING_VERSION,
            )
            db.add(job)
            db.flush()
            created.append(job.id)
        for step in submission_steps:
            step.submission_processing_job_id = job.id
            step.stage = "submission_processing"
            if job.status == "queued":
                step.status = "dispatched"
                step.error_code = None
                step.error_message = None
            elif job.status == "running":
                step.status = "running"
                step.error_code = None
                step.error_message = None
    return created


def _dispatch_submission_processing_jobs(db: Session, job_ids: list[uuid.UUID]) -> None:
    for job_id in job_ids:
        try:
            from workers.celery_app import celery_app

            celery_app.send_task(
                "ahamark.submission_processing.run",
                args=[str(job_id), None],
                headers=celery_request_headers(),
            )
        except Exception as exc:
            job = db.get(SubmissionProcessingJob, job_id)
            if job is not None and job.status == "queued":
                job.status = "failed"
                job.error_code = "WORKER_UNAVAILABLE"
                job.error_message = type(exc).__name__
                db.commit()


def _reconcile_submission_processing_children(
    db: Session, *, run: ProcessingRun, steps: list[ProcessingStep]
) -> None:
    now = now_utc()
    by_submission: dict[uuid.UUID, list[ProcessingStep]] = {}
    for step in steps:
        if (
            step.kind == "recognition"
            and step.submission_processing_job_id is not None
            and step.recognition_job_id is None
        ):
            by_submission.setdefault(step.submission_id, []).append(step)
    for submission_id, submission_steps in by_submission.items():
        job = db.get(
            SubmissionProcessingJob,
            submission_steps[0].submission_processing_job_id,
        )
        if job is None or job.submission_id != submission_id:
            for step in submission_steps:
                step.status = "terminal_failed"
                step.retryable = False
                step.error_code = "SUBMISSION_PROCESSING_JOB_MISSING"
                step.failed_at = now
            continue
        if job.status == "queued":
            for step in submission_steps:
                step.status = "dispatched"
                step.stage = "submission_processing"
            continue
        if job.status == "running":
            for step in submission_steps:
                step.status = "running"
                step.stage = "submission_processing"
                step.started_at = job.started_at
            continue
        if job.status == "failed":
            for step in submission_steps:
                step.status = "retryable_failed"
                step.stage = "submission_processing"
                step.retryable = True
                step.error_code = job.error_code or "SUBMISSION_PROCESSING_FAILED"
                step.error_message = job.error_message
                step.failed_at = job.completed_at or now
            continue
        if job.status != "completed":
            for step in submission_steps:
                step.status = "blocked_review"
                step.stage = "submission_processing"
                step.retryable = False
                step.error_code = "SUBMISSION_PROCESSING_REVIEW_REQUIRED"
            continue
        decision = auto_confirm_deterministic_regions(
            db,
            owner_id=run.owner_id,
            submission_id=submission_id,
            processing_job_id=job.id,
            processing_run_id=run.id,
        )
        for step in submission_steps:
            if decision.eligible:
                step.status = "blocked_review"
                step.stage = "answer_recognition"
                step.retryable = True
                step.error_code = "RECOGNITION_EVIDENCE_NOT_CONFIRMED"
                step.error_message = "Current recognition evidence is required"
            else:
                step.status = "blocked_review"
                step.stage = "segmentation_confirmation"
                step.retryable = False
                step.error_code = decision.code
                step.error_message = decision.message


def _materialize_recognition_jobs(
    db: Session, *, run: ProcessingRun, steps: list[ProcessingStep]
) -> list[uuid.UUID]:
    """Create durable OCR jobs only for submissions whose teacher segmentation is complete."""
    recognition_steps = [
        step
        for step in steps
        if step.kind == "recognition"
        and (
            step.error_code == "RECOGNITION_EVIDENCE_NOT_CONFIRMED"
            or (
                step.status == "pending"
                and step.error_code is None
                and step.recognition_job_id is None
            )
        )
    ]
    by_submission: dict[uuid.UUID, list[ProcessingStep]] = {}
    for step in recognition_steps:
        by_submission.setdefault(step.submission_id, []).append(step)
    created: list[uuid.UUID] = []
    settings = get_settings()
    provider = recognition_provider_from_settings(settings)
    for submission_id in sorted(by_submission, key=str):
        eligible = _recognition_input(db, submission_id)
        if eligible is None:
            continue
        _, regions = eligible
        input_hash = hashlib.sha256(
            json.dumps(
                [
                    (region.id, region.region_version, region.segmentation_version)
                    for region in regions
                ],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        idempotency_key = f"processing:{run.id}:{submission_id}"
        job = db.scalar(
            select(SubmissionRecognitionJob).where(
                SubmissionRecognitionJob.owner_id == run.owner_id,
                SubmissionRecognitionJob.idempotency_key == idempotency_key,
            )
        )
        if job is None:
            generation = (
                db.scalar(
                    select(func.max(SubmissionRecognitionJob.generation)).where(
                        SubmissionRecognitionJob.submission_id == submission_id
                    )
                )
                or 0
            ) + 1
            job = SubmissionRecognitionJob(
                owner_id=run.owner_id,
                submission_id=submission_id,
                status="queued",
                provider=provider.name,
                provider_version=provider.version,
                provider_kind="mixed",
                config_version=settings.answer_recognition_config_version,
                input_hash=input_hash,
                idempotency_key=idempotency_key,
                max_attempts=settings.answer_recognition_max_attempts,
                generation=generation,
            )
            db.add(job)
            db.flush()
            created.append(job.id)
        elif (
            job.submission_id != submission_id
            or job.input_hash != input_hash
            or job.provider != provider.name
            or job.provider_version != provider.version
            or job.provider_kind != "mixed"
            or job.config_version != settings.answer_recognition_config_version
            or job.max_attempts != settings.answer_recognition_max_attempts
        ):
            for step in by_submission[submission_id]:
                step.status = "stale"
                step.retryable = False
                step.error_code = "RECOGNITION_INPUT_STALE"
                step.stale_at = now_utc()
            continue
        for step in by_submission[submission_id]:
            step.recognition_job_id = job.id
            if job.status == "queued":
                step.status = "dispatched"
                step.error_code = None
                step.error_message = None
                step.retryable = True
    return created


def _dispatch_recognition_jobs(db: Session, job_ids: list[uuid.UUID]) -> None:
    """Dispatch after commit; a broker failure remains durably retryable and reconcilable."""
    for job_id in job_ids:
        try:
            from workers.celery_app import celery_app

            celery_app.send_task(
                "ahamark.answer_recognition.run",
                args=[str(job_id)],
                headers=celery_request_headers(),
            )
        except Exception as exc:
            job = db.get(SubmissionRecognitionJob, job_id)
            if job is not None and job.status == "queued":
                job.status = "failed"
                job.error_code = "WORKER_UNAVAILABLE"
                job.error_message = type(exc).__name__
                db.commit()


def _reconcile_recognition_children(
    db: Session, *, steps: list[ProcessingStep], run: ProcessingRun | None = None
) -> None:
    """Project durable OCR job state without calling the snapshot builder."""
    now = now_utc()
    for step in (item for item in steps if item.kind == "recognition"):
        if step.recognition_job_id is None:
            continue
        job = db.get(SubmissionRecognitionJob, step.recognition_job_id)
        if job is None or job.submission_id != step.submission_id:
            step.status = "terminal_failed"
            step.retryable = False
            step.error_code = "RECOGNITION_JOB_MISSING"
            step.failed_at = now
        elif job.status == "queued":
            step.status = "dispatched"
            step.error_code = None
            step.error_message = None
        elif job.status == "running":
            step.status = "running"
            step.started_at = job.started_at
            step.error_code = None
            step.error_message = None
        elif job.status in {"completed", "partially_completed"}:
            if run is not None and job.status == "completed":
                decision = auto_confirm_deterministic_recognition(
                    db,
                    owner_id=run.owner_id,
                    submission_id=step.submission_id,
                    recognition_job_id=job.id,
                    processing_run_id=run.id,
                    min_confidence=Decimal(str(get_settings().recognition_high_confidence)),
                )
                if decision.eligible:
                    step.status = "succeeded"
                    step.stage = "recognition_confirmation"
                    step.retryable = False
                    step.error_code = None
                    step.error_message = None
                    step.completed_at = job.completed_at or now
                    continue
                step.error_code = decision.code
                step.error_message = decision.message
            else:
                step.error_code = (
                    "RECOGNITION_CONFIRMATION_REQUIRED"
                    if job.status == "completed"
                    else "RECOGNITION_PARTIAL_REVIEW_REQUIRED"
                )
                step.error_message = "Teacher confirmation of recognition evidence is required"
            step.status = "blocked_review"
            step.stage = "recognition_confirmation"
            step.retryable = False
            step.completed_at = job.completed_at or now
        elif job.status == "failed":
            step.status = (
                "retryable_failed" if job.attempt < job.max_attempts else "terminal_failed"
            )
            step.retryable = job.attempt < job.max_attempts
            step.error_code = job.error_code or "RECOGNITION_FAILED"
            step.error_message = job.error_message
            step.failed_at = job.completed_at or now
        elif job.status == "cancelled":
            step.status = "cancelled"
            step.retryable = False
            step.error_code = job.error_code
            step.error_message = job.error_message
            step.cancelled_at = job.cancelled_at or now
        else:
            step.status = "terminal_failed"
            step.retryable = False
            step.error_code = "RECOGNITION_JOB_STATE_INVALID"
            step.failed_at = now


def _materialize_codex_steps_after_recognition(
    db: Session, *, run: ProcessingRun, steps: list[ProcessingStep]
) -> list[ProcessingStep]:
    existing_answer_ids = {
        step.student_answer_id
        for step in steps
        if step.kind == "codex_suggestion" and step.student_answer_id is not None
    }
    ready_submission_ids = {
        step.submission_id
        for step in steps
        if step.kind == "recognition" and step.status == "succeeded"
    }
    created: list[ProcessingStep] = []
    for submission_id in sorted(ready_submission_ids, key=str):
        answers = _active_paper_answers(db, submission_id=submission_id)
        for answer in answers:
            if answer.id in existing_answer_ids:
                continue
            try:
                snapshot = build_processing_input_snapshot(
                    db,
                    owner_id=run.owner_id,
                    grading_batch_id=run.grading_batch_id,
                    submission_id=submission_id,
                    answer_id=answer.id,
                )
            except ProcessingInputError as exc:
                recognition_step = next(
                    step
                    for step in steps
                    if step.kind == "recognition"
                    and step.submission_id == submission_id
                    and step.status == "succeeded"
                )
                recognition_step.status = "blocked_review"
                recognition_step.stage = "review_readiness"
                recognition_step.error_code = exc.code
                recognition_step.error_message = str(exc)
                continue
            step = ProcessingStep(
                processing_run_id=run.id,
                submission_id=submission_id,
                student_answer_id=answer.id,
                scope_key=f"answer:{answer.id}",
                kind="codex_suggestion",
                stage="codex_suggestion",
                status="pending",
                generation=run.generation,
                input_version=snapshot.input_version,
                request_hash=canonical_hash(
                    {
                        "run": run.request_hash,
                        "answer_id": answer.id,
                        "input_version": snapshot.input_version,
                    }
                ),
                retryable=True,
            )
            db.add(step)
            created.append(step)
            existing_answer_ids.add(answer.id)
    if created:
        db.flush()
        steps.extend(created)
        run.step_count = len(steps)
    return created


def _manifest(db: Session, owner_id: uuid.UUID, batch: GradingBatch) -> dict[str, Any]:
    submissions = list(
        db.scalars(
            select(Submission)
            .where(
                Submission.grading_batch_id == batch.id,
                Submission.owner_id == owner_id,
            )
            .order_by(Submission.id)
            .with_for_update()
        )
    )
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for submission in submissions:
        if submission.status in TERMINAL_SUBMISSION_STATUSES or submission.finalized_at is not None:
            excluded.append(
                {
                    "submission_id": str(submission.id),
                    "reason": "SUBMISSION_TERMINAL",
                    "status": submission.status,
                }
            )
            continue
        answers = _active_paper_answers(db, submission_id=submission.id, lock=True)
        answer_entries: list[dict[str, Any]] = []
        blockers: list[str] = []
        if not answers:
            blockers.append("STUDENT_ANSWERS_REQUIRED")
        for answer in answers:
            try:
                snapshot = build_processing_input_snapshot(
                    db,
                    owner_id=owner_id,
                    grading_batch_id=batch.id,
                    submission_id=submission.id,
                    answer_id=answer.id,
                )
            except ProcessingInputError as exc:
                if exc.code not in READINESS_BLOCKERS:
                    _fail(409, exc.code, str(exc))
                blockers.append(exc.code)
                answer_entries.append(
                    {
                        "answer_id": str(answer.id),
                        "question_id": str(answer.question_id),
                        "blocker": exc.code,
                    }
                )
            else:
                answer_entries.append(
                    {
                        "answer_id": str(answer.id),
                        "question_id": str(answer.question_id),
                        "input_version": snapshot.input_version,
                    }
                )
        included.append(
            {
                "submission_id": str(submission.id),
                "status": submission.status,
                "answers": answer_entries,
                "blockers": sorted(set(blockers)),
            }
        )
    payload = {
        "schema": PROCESSING_MANIFEST_SCHEMA,
        "batch_id": str(batch.id),
        "included": included,
        "excluded": excluded,
    }
    payload["input_version"] = canonical_hash(payload)
    return payload


def _plan_hash(input_version: str) -> str:
    return build_request_hash(
        run_input_version=input_version,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        config_version=CONFIG_VERSION,
    )


def _add_command(
    db: Session,
    *,
    owner_id: uuid.UUID,
    batch_id: uuid.UUID,
    operation: str,
    idempotency_key: str,
    request_hash: str,
    request_payload: dict[str, Any],
    result_run: ProcessingRun,
    source_run_id: uuid.UUID | None,
    expected_generation: int | None,
) -> None:
    db.add(
        ProcessingRunCommand(
            owner_id=owner_id,
            grading_batch_id=batch_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_payload=request_payload,
            source_run_id=source_run_id,
            result_run_id=result_run.id,
            expected_generation=expected_generation,
            result_generation=result_run.generation,
        )
    )
    db.add(
        AuditLog(
            actor_id=owner_id,
            action=f"processing.{operation}",
            resource_type="processing_run",
            resource_id=str(result_run.id),
            metadata_={"generation": result_run.generation, "suggestion_only": True},
        )
    )


def continue_processing(
    db: Session, *, owner_id: uuid.UUID, batch_id: uuid.UUID, idempotency_key: str
) -> ProcessingRun:
    command_hash, command_payload = build_command_hash(operation="continue", batch_id=batch_id)
    replay = _command_replay(
        db,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if replay is not None:
        return replay
    batch = _owned_batch_for_update(db, owner_id, batch_id)
    # Replay again after the serialization lock to close the concurrent-key race.
    replay = _command_replay(
        db,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if replay is not None:
        return replay
    manifest = _manifest(db, owner_id, batch)
    included = manifest["included"]
    if not included:
        _fail(409, "NO_PROCESSABLE_SUBMISSIONS", "No nonterminal submissions are available")
    input_version = str(manifest["input_version"])
    plan_hash = _plan_hash(input_version)
    latest = _latest_run(db, batch.id)
    if (
        latest is not None
        and latest.request_hash == plan_hash
        and latest.status in ACTIVE_RUN_STATUSES
    ):
        result = latest
    else:
        if (
            latest is not None
            and latest.request_hash == plan_hash
            and latest.status in {"failed", "cancelled"}
        ):
            _fail(
                409,
                "PROCESSING_RETRY_REQUIRED",
                "The current plan failed or was cancelled; use retry",
            )
        generation = 1 if latest is None else latest.generation + 1
        if latest is not None and latest.status in ACTIVE_RUN_STATUSES:
            _stale_run_children(db, latest)
        planned_steps: list[dict[str, Any]] = []
        for entry in included:
            submission_id = uuid.UUID(entry["submission_id"])
            answers = entry["answers"]
            if not answers:
                planned_steps.append(
                    {
                        "submission_id": submission_id,
                        "student_answer_id": None,
                        "scope_key": f"submission:{submission_id}",
                        "kind": "recognition",
                        "status": "blocked_review",
                        "error_code": "STUDENT_ANSWERS_REQUIRED",
                        "source": entry,
                    }
                )
                continue
            for answer in answers:
                answer_id = uuid.UUID(answer["answer_id"])
                blocker = answer.get("blocker")
                kind = (
                    "recognition"
                    if blocker in RECOGNITION_BLOCKERS
                    else ("review_readiness" if blocker else "codex_suggestion")
                )
                planned_steps.append(
                    {
                        "submission_id": submission_id,
                        "student_answer_id": answer_id,
                        "scope_key": f"answer:{answer_id}",
                        "kind": kind,
                        "status": "blocked_review" if blocker else "pending",
                        "error_code": blocker,
                        "source": {
                            "submission_id": entry["submission_id"],
                            "submission_status": entry["status"],
                            "answer": answer,
                        },
                    }
                )
        has_blocked_steps = any(planned["status"] == "blocked_review" for planned in planned_steps)
        result = ProcessingRun(
            owner_id=owner_id,
            grading_batch_id=batch.id,
            status="waiting_input" if has_blocked_steps else "queued",
            mode="codex_local",
            generation=generation,
            input_version=input_version,
            request_hash=plan_hash,
            input_manifest=manifest,
            submission_count=len(included),
            step_count=len(planned_steps),
        )
        db.add(result)
        db.flush()
        new_steps: list[ProcessingStep] = []
        for planned in planned_steps:
            source = planned["source"]
            answer_source = source.get("answer") if isinstance(source, dict) else None
            step = ProcessingStep(
                processing_run_id=result.id,
                submission_id=planned["submission_id"],
                student_answer_id=planned["student_answer_id"],
                scope_key=planned["scope_key"],
                kind=planned["kind"],
                status=planned["status"],
                generation=generation,
                input_version=(
                    str(answer_source["input_version"])
                    if planned["kind"] == "codex_suggestion"
                    and isinstance(answer_source, dict)
                    and answer_source.get("input_version")
                    else canonical_hash(source)
                ),
                request_hash=canonical_hash(
                    {"run": result.request_hash, "scope": planned["source"]}
                ),
                error_code=planned["error_code"],
                error_message="Teacher input or recognition confirmation is required"
                if planned["error_code"]
                else None,
                retryable=planned["error_code"] is None,
            )
            db.add(step)
            new_steps.append(step)
        db.flush()
    current_steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == result.id)
            .order_by(ProcessingStep.id)
            .with_for_update()
        )
    )
    processing_job_ids = _materialize_submission_processing_jobs(
        db, run=result, steps=current_steps
    )
    _reconcile_submission_processing_children(db, run=result, steps=current_steps)
    recognition_job_ids = _materialize_recognition_jobs(db, run=result, steps=current_steps)
    work_items = materialize_work_items(db, run=result)
    pending_codex_count = sum(
        item.status in {"queued", "leased", "submitted"} for item in work_items
    )
    result.status, result.completed_step_count, result.failed_step_count = _aggregate_run_state(
        current_steps, pending_codex_count
    )
    result.pending_codex_count = pending_codex_count
    _add_command(
        db,
        owner_id=owner_id,
        batch_id=batch.id,
        operation="continue",
        idempotency_key=idempotency_key,
        request_hash=command_hash,
        request_payload=command_payload,
        result_run=result,
        source_run_id=None,
        expected_generation=None,
    )
    committed = _commit_with_command_replay(
        db,
        result=result,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if committed.id == result.id:
        _dispatch_submission_processing_jobs(db, processing_job_ids)
        _dispatch_recognition_jobs(db, recognition_job_ids)
    return committed


def retry_processing(
    db: Session,
    *,
    owner_id: uuid.UUID,
    batch_id: uuid.UUID,
    source_run_id: uuid.UUID,
    idempotency_key: str,
    expected_generation: int,
    step_ids: list[uuid.UUID],
) -> ProcessingRun:
    if len(step_ids) != len(set(step_ids)):
        _fail(422, "PROCESSING_STEP_IDS_DUPLICATE", "Retry step ids must be unique")
    command_hash, command_payload = build_command_hash(
        operation="retry",
        batch_id=batch_id,
        source_run_id=source_run_id,
        expected_generation=expected_generation,
        step_ids=step_ids,
    )
    replay = _command_replay(
        db,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if replay is not None:
        return replay
    batch = _owned_batch_for_update(db, owner_id, batch_id)
    replay = _command_replay(
        db,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if replay is not None:
        return replay
    source = _owned_run(db, owner_id, batch.id, source_run_id, lock=True)
    latest = _latest_run(db, batch.id)
    if latest is None or latest.id != source.id or latest.generation != expected_generation:
        _fail(
            409,
            "PROCESSING_GENERATION_CONFLICT",
            "Expected generation is no longer current",
        )
    current_manifest = _manifest(db, owner_id, batch)
    if current_manifest["input_version"] != source.input_version:
        _fail(409, "PROCESSING_INPUT_STALE", "Processing inputs have changed")
    source_steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == source.id)
            .order_by(ProcessingStep.id)
            .with_for_update()
        )
    )
    if any(step.status in ACTIVE_STEP_STATUSES for step in source_steps):
        _fail(
            409,
            "PROCESSING_RUN_ACTIVE",
            "A processing run with active steps cannot be retried",
        )
    by_id = {step.id: step for step in source_steps}
    selected = [by_id.get(step_id) for step_id in step_ids]
    if any(step is None for step in selected):
        _fail(404, "PROCESSING_STEP_NOT_FOUND", "A retry step is outside this run")
    for step in selected:
        assert step is not None
        if (
            step.status not in RETRYABLE_STEP_STATUSES
            or not step.retryable
            or step.attempt >= step.max_attempts
        ):
            _fail(409, "PROCESSING_STEP_NOT_RETRYABLE", "A selected step is not retryable")
    generation = source.generation + 1
    result = ProcessingRun(
        owner_id=owner_id,
        grading_batch_id=batch.id,
        status="queued",
        mode=source.mode,
        generation=generation,
        input_version=source.input_version,
        request_hash=source.request_hash,
        input_manifest=source.input_manifest,
        submission_count=source.submission_count,
        step_count=source.step_count,
    )
    db.add(result)
    db.flush()
    selected_ids = set(step_ids)
    new_steps: list[ProcessingStep] = []
    for old in source_steps:
        reset = old.id in selected_ids
        status = "pending" if reset else old.status
        new_step = ProcessingStep(
            processing_run_id=result.id,
            submission_id=old.submission_id,
            student_answer_id=old.student_answer_id,
            scope_key=old.scope_key,
            kind=old.kind,
            stage=old.stage,
            status=status,
            generation=generation,
            input_version=old.input_version,
            request_hash=old.request_hash,
            attempt=old.attempt + 1 if reset else old.attempt,
            max_attempts=old.max_attempts,
            retryable=old.retryable,
            error_code=None if reset else old.error_code,
            error_message=None if reset else old.error_message,
            completed_at=old.completed_at if status == "succeeded" else None,
            failed_at=old.failed_at if not reset else None,
            recognition_job_id=(
                None if reset and old.kind == "recognition" else old.recognition_job_id
            ),
            submission_processing_job_id=old.submission_processing_job_id,
        )
        db.add(new_step)
        new_steps.append(new_step)
    db.flush()
    recognition_job_ids = _materialize_recognition_jobs(db, run=result, steps=new_steps)
    work_items = materialize_work_items(db, run=result, steps=new_steps)
    pending_codex_count = sum(
        item.status in {"queued", "leased", "submitted"} for item in work_items
    )
    result.status, result.completed_step_count, result.failed_step_count = _aggregate_run_state(
        new_steps, pending_codex_count
    )
    result.pending_codex_count = pending_codex_count
    _stale_run_children(db, source)
    _add_command(
        db,
        owner_id=owner_id,
        batch_id=batch.id,
        operation="retry",
        idempotency_key=idempotency_key,
        request_hash=command_hash,
        request_payload=command_payload,
        result_run=result,
        source_run_id=source.id,
        expected_generation=expected_generation,
    )
    committed = _commit_with_command_replay(
        db,
        result=result,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if committed.id == result.id:
        _dispatch_recognition_jobs(db, recognition_job_ids)
    return committed


def reconcile_processing(
    db: Session,
    *,
    owner_id: uuid.UUID,
    batch_id: uuid.UUID,
    run_id: uuid.UUID,
    idempotency_key: str,
    expected_generation: int,
) -> ProcessingRun:
    command_hash, command_payload = build_command_hash(
        operation="reconcile",
        batch_id=batch_id,
        source_run_id=run_id,
        expected_generation=expected_generation,
    )
    replay = _command_replay(
        db,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if replay is not None:
        return replay
    batch = _owned_batch_for_update(db, owner_id, batch_id)
    replay = _command_replay(
        db,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if replay is not None:
        return replay
    run = _owned_run(db, owner_id, batch.id, run_id, lock=True)
    latest = _latest_run(db, batch.id)
    if latest is None or latest.id != run.id or run.generation != expected_generation:
        _fail(
            409,
            "PROCESSING_GENERATION_CONFLICT",
            "Only the latest expected generation may be reconciled",
        )
    steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == run.id)
            .order_by(ProcessingStep.id)
            .with_for_update()
        )
    )
    processing_job_ids = _materialize_submission_processing_jobs(db, run=run, steps=steps)
    _reconcile_submission_processing_children(db, run=run, steps=steps)
    recognition_job_ids = _materialize_recognition_jobs(db, run=run, steps=steps)
    _reconcile_recognition_children(db, steps=steps, run=run)
    new_codex_steps = _materialize_codex_steps_after_recognition(db, run=run, steps=steps)
    if new_codex_steps:
        materialize_work_items(db, run=run, steps=new_codex_steps)
    run.pending_codex_count = _reconcile_codex_children(db, run=run, steps=steps)
    run.status, run.completed_step_count, run.failed_step_count = _aggregate_run_state(
        steps, run.pending_codex_count
    )
    _add_command(
        db,
        owner_id=owner_id,
        batch_id=batch.id,
        operation="reconcile",
        idempotency_key=idempotency_key,
        request_hash=command_hash,
        request_payload=command_payload,
        result_run=run,
        source_run_id=run.id,
        expected_generation=expected_generation,
    )
    committed = _commit_with_command_replay(
        db,
        result=run,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request_hash=command_hash,
    )
    if committed.id == run.id:
        _dispatch_submission_processing_jobs(db, processing_job_ids)
        _dispatch_recognition_jobs(db, recognition_job_ids)
    return committed


def get_processing_run(
    db: Session, *, owner_id: uuid.UUID, batch_id: uuid.UUID, run_id: uuid.UUID
) -> ProcessingRun:
    return _owned_run(db, owner_id, batch_id, run_id)


def processing_run_json(db: Session, run: ProcessingRun) -> dict[str, Any]:
    steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == run.id)
            .order_by(ProcessingStep.scope_key, ProcessingStep.kind, ProcessingStep.id)
        )
    )
    return {
        "id": str(run.id),
        "grading_batch_id": str(run.grading_batch_id),
        "generation": run.generation,
        "status": run.status,
        "provider": "codex_local",
        "provider_label": "Codex-assisted",
        "suggestion_only": True,
        "target_state": "awaiting_teacher_review",
        "input_version": run.input_version,
        "request_hash": run.request_hash,
        "input_manifest": run.input_manifest,
        "submission_count": run.submission_count,
        "step_count": run.step_count,
        "completed_step_count": run.completed_step_count,
        "failed_step_count": run.failed_step_count,
        "pending_codex_count": run.pending_codex_count,
        "retryable": run.retryable,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "steps": [
            {
                "id": str(step.id),
                "submission_id": str(step.submission_id),
                "student_answer_id": str(step.student_answer_id)
                if step.student_answer_id
                else None,
                "scope_key": step.scope_key,
                "kind": step.kind,
                "status": step.status,
                "generation": step.generation,
                "attempt": step.attempt,
                "max_attempts": step.max_attempts,
                "retryable": step.retryable,
                "error_code": step.error_code,
                "error_message": step.error_message,
            }
            for step in steps
        ],
    }
