from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, NoReturn

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_grading.guards import GuardViolation, require_submission_mutable
from app.ai_grading.request_contract import (
    require_current_recognition_evidence,
    scoring_input_version,
    strict_request_hash,
)
from app.ai_grading.schema import AIGradingOutput, ValidationContext, validate_output
from app.models import (
    AICriterionSuggestion,
    AIFeedbackDraft,
    AIScoringJob,
    Assignment,
    AssignmentRubricPublicationBinding,
    AuditLog,
    CodexWorkItem,
    GradingBatch,
    GradingCriterionResult,
    GradingEvidence,
    GradingJob,
    GradingResult,
    ProcessingRun,
    ProcessingStep,
    Question,
    QuestionRecognitionEvidence,
    QuestionRubric,
    ReferenceAnswerVersion,
    RubricCriterion,
    RubricItem,
    StructuredRubricVersion,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    TeacherReview,
    now_utc,
)
from app.processing.contracts import canonical_hash, canonicalize
from app.processing.input_snapshot import build_processing_input_snapshot

WORK_REQUEST_SCHEMA = "codex-work-request-v1"
OUTPUT_SCHEMA = "criterion-suggestion-v1"
PROMPT_VERSION = "codex-local-v1"
CONFIG_VERSION = "suggestion-only-v1"
TERMINAL_SUBMISSION_STATUSES = {"finalized", "merged", "voided"}
REVIEWED_RESULT_STATUSES = {"accepted", "modified", "rejected"}


@dataclass(frozen=True)
class ApplyContract:
    run: ProcessingRun
    step: ProcessingStep
    submission: Submission
    answer: StudentAnswer
    assignment: Assignment
    question: Question
    evidence: QuestionRecognitionEvidence
    reference: ReferenceAnswerVersion
    rubric: StructuredRubricVersion
    binding: AssignmentRubricPublicationBinding
    criteria: list[RubricCriterion]
    output: AIGradingOutput
    strict_request_hash: str
    job_key: str


class CodexLocalProblem(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def _fail(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> NoReturn:
    raise CodexLocalProblem(status, code, message, details)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def verify_internal_token(authorization: str | None, expected: str) -> None:
    scheme, _, supplied = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not supplied
        or not hmac.compare_digest(supplied.encode(), expected.encode())
    ):
        _fail(401, "CODEX_LOCAL_AUTH_REQUIRED", "Valid internal bearer token required")


def _request_components(
    db: Session,
    *,
    owner_id: uuid.UUID,
    batch_id: uuid.UUID,
    submission_id: uuid.UUID,
    answer_id: uuid.UUID,
) -> tuple[dict[str, Any], str, ValidationContext]:
    snapshot = build_processing_input_snapshot(
        db,
        owner_id=owner_id,
        grading_batch_id=batch_id,
        submission_id=submission_id,
        answer_id=answer_id,
    )
    answer = db.get(StudentAnswer, answer_id)
    if answer is None:
        _fail(409, "CODEX_WORK_INPUT_STALE", "Student answer is no longer current")
    question = db.get(Question, answer.question_id)
    formal = snapshot.payload["formal"]
    reference = db.get(
        ReferenceAnswerVersion,
        uuid.UUID(formal["reference_answer"]["id"]),
    )
    rubric = db.get(
        StructuredRubricVersion,
        uuid.UUID(formal["structured_rubric"]["id"]),
    )
    binding = db.get(
        AssignmentRubricPublicationBinding,
        uuid.UUID(snapshot.payload["legacy_projection"]["binding_id"]),
    )
    if question is None or reference is None or rubric is None or binding is None:
        _fail(409, "CODEX_WORK_INPUT_STALE", "Scoring inputs are no longer available")
    criteria = list(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == rubric.id)
            .order_by(RubricCriterion.display_order, RubricCriterion.id)
        )
    )
    if not criteria or question.max_score is None:
        _fail(409, "CODEX_WORK_INPUT_INCOMPLETE", "Question and rubric must be complete")
    student_text = (
        answer.corrected_text if answer.corrected_text is not None else answer.recognized_text
    )
    student_latex = (
        answer.corrected_latex if answer.corrected_latex is not None else answer.recognized_latex
    )
    if not answer.is_blank and not (student_text or student_latex):
        _fail(409, "CODEX_WORK_INPUT_INCOMPLETE", "Student answer content is required")
    evidence_regions = snapshot.payload["recognition_evidence"]["regions"]
    evidence_refs = sorted(
        {
            str(region["id"])
            for region in evidence_regions
            if isinstance(region, dict) and region.get("id")
        }
    )
    if not evidence_refs:
        _fail(409, "CODEX_WORK_INPUT_INCOMPLETE", "Confirmed evidence references are required")
    criterion_payload = [
        {
            "id": str(item.id),
            "stable_key": item.stable_key,
            "display_order": item.display_order,
            "title": item.title,
            "description": item.description,
            "max_points": item.max_points,
            "criterion_type": item.criterion_type,
            "required": item.required,
            "dependencies": item.dependencies,
            "expected_evidence": item.expected_evidence,
            "validation_mode": item.validation_mode,
            "validation_rule": item.validation_rule,
            "manual_review_policy": item.manual_review_policy,
            "partial_credit_policy": item.partial_credit_policy,
            "error_category": item.error_category,
            "metadata": item.metadata_,
        }
        for item in criteria
    ]
    request = canonicalize(
        {
            "schema": WORK_REQUEST_SCHEMA,
            "provider": "codex_local",
            "provider_label": "Codex-assisted",
            "suggestion_only": True,
            "operation": "continue_to_teacher_review",
            "prompt_version": PROMPT_VERSION,
            "schema_version": OUTPUT_SCHEMA,
            "config_version": CONFIG_VERSION,
            "processing_input": {
                "schema": "processing-input-v1",
                "input_version": snapshot.input_version,
                "payload": snapshot.payload,
            },
            "grading_bundle": {
                "question": {
                    "id": str(question.id),
                    "number": question.question_number,
                    "text": question.content_text,
                    "latex": question.content_latex,
                    "max_points": question.max_score,
                },
                "student_answer": {
                    "id": str(answer.id),
                    "text": student_text,
                    "latex": student_latex,
                    "is_blank": answer.is_blank,
                },
                "reference_answer": {
                    "id": str(reference.id),
                    "raw_content": reference.raw_content,
                    "normalized_content": reference.normalized_content,
                    "structured_content": reference.structured_content,
                    "content_hash": reference.content_hash,
                },
                "structured_rubric": {
                    "id": str(rubric.id),
                    "title": rubric.title,
                    "total_points": rubric.total_points,
                    "content_hash": rubric.content_hash,
                    "criteria": criterion_payload,
                },
                "evidence_refs": evidence_refs,
                "legacy_binding": {
                    "id": str(binding.id),
                    "legacy_rubric_version_id": str(binding.legacy_rubric_version_id),
                    "mapping": binding.mapping,
                    "criterion_to_legacy_mapping": binding.mapping,
                    "target_hash": binding.target_legacy_hash,
                },
            },
        }
    )
    manual_only = {
        item.stable_key
        for item in criteria
        if bool((item.manual_review_policy or {}).get("manual_only"))
        or item.validation_mode == "manual"
    }
    step_sizes: dict[str, Decimal] = {}
    for item in criteria:
        raw_step = (item.partial_credit_policy or {}).get("step")
        if raw_step is not None:
            step_sizes[item.stable_key] = Decimal(str(raw_step))
    context = ValidationContext(
        criterion_maxima={item.stable_key: Decimal(str(item.max_points)) for item in criteria},
        evidence_ids=set(evidence_refs),
        manual_only=manual_only,
        step_sizes=step_sizes,
        question_max_points=Decimal(str(question.max_score)),
        criterion_keys={item.stable_key for item in criteria},
    )
    return request, snapshot.input_version, context


def build_work_request(
    db: Session,
    *,
    owner_id: uuid.UUID,
    batch_id: uuid.UUID,
    submission_id: uuid.UUID,
    answer_id: uuid.UUID,
) -> tuple[dict[str, Any], str, str]:
    request, input_version, _ = _request_components(
        db,
        owner_id=owner_id,
        batch_id=batch_id,
        submission_id=submission_id,
        answer_id=answer_id,
    )
    return request, input_version, canonical_hash(request)


def materialize_work_items(
    db: Session,
    *,
    run: ProcessingRun,
    steps: list[ProcessingStep] | None = None,
) -> list[CodexWorkItem]:
    candidates = steps or list(
        db.scalars(
            select(ProcessingStep).where(
                ProcessingStep.processing_run_id == run.id,
                ProcessingStep.kind == "codex_suggestion",
                ProcessingStep.status == "pending",
            )
        )
    )
    created: list[CodexWorkItem] = []
    for step in candidates:
        if step.kind != "codex_suggestion" or step.status != "pending":
            continue
        if step.student_answer_id is None:
            _fail(409, "CODEX_WORK_INPUT_INCOMPLETE", "Codex step requires an answer")
        request, input_version, request_hash = build_work_request(
            db,
            owner_id=run.owner_id,
            batch_id=run.grading_batch_id,
            submission_id=step.submission_id,
            answer_id=step.student_answer_id,
        )
        if step.input_version != input_version:
            _fail(409, "CODEX_WORK_INPUT_STALE", "Processing step input version is stale")
        existing = db.scalar(
            select(CodexWorkItem)
            .where(CodexWorkItem.processing_step_id == step.id)
            .with_for_update()
        )
        contract = {
            "owner_id": run.owner_id,
            "grading_batch_id": run.grading_batch_id,
            "submission_id": step.submission_id,
            "student_answer_id": step.student_answer_id,
            "generation": step.generation,
            "input_version": input_version,
            "request_hash": request_hash,
            "request_payload": request,
            "provider": "codex_local",
            "prompt_version": PROMPT_VERSION,
            "schema_version": OUTPUT_SCHEMA,
            "config_version": CONFIG_VERSION,
        }
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in contract.items()):
                _fail(
                    409,
                    "CODEX_WORK_ITEM_CONTRACT_CONFLICT",
                    "Existing work item has a different immutable contract",
                )
            created.append(existing)
            continue
        item = CodexWorkItem(
            processing_step_id=step.id,
            status="queued",
            attempt=0,
            max_attempts=step.max_attempts,
            available_at=now_utc(),
            retryable=True,
            **contract,
        )
        db.add(item)
        created.append(item)
    db.flush()
    run.pending_codex_count = (
        db.scalar(
            select(func.count())
            .select_from(CodexWorkItem)
            .join(
                ProcessingStep,
                ProcessingStep.id == CodexWorkItem.processing_step_id,
            )
            .where(
                ProcessingStep.processing_run_id == run.id,
                CodexWorkItem.status.in_(("queued", "leased", "submitted")),
            )
        )
        or 0
    )
    if created and run.status == "queued":
        run.status = "waiting_codex"
    return created


def _current_item_state(
    db: Session, item: CodexWorkItem
) -> tuple[ProcessingRun, ProcessingStep, ValidationContext]:
    batch = db.get(GradingBatch, item.grading_batch_id)
    submission = db.get(Submission, item.submission_id)
    answer = db.get(StudentAnswer, item.student_answer_id)
    step = db.get(ProcessingStep, item.processing_step_id)
    run = db.get(ProcessingRun, step.processing_run_id) if step is not None else None
    if batch is None or submission is None or answer is None or run is None or step is None:
        _fail(409, "CODEX_WORK_INPUT_STALE", "Work item scope no longer exists")
    latest_generation = db.scalar(
        select(ProcessingRun.generation)
        .where(ProcessingRun.grading_batch_id == batch.id)
        .order_by(ProcessingRun.generation.desc())
        .limit(1)
    )
    teacher_review = db.scalar(
        select(TeacherReview.id).where(TeacherReview.student_answer_id == answer.id).limit(1)
    )
    if (
        submission.status in TERMINAL_SUBMISSION_STATUSES
        or submission.finalized_at is not None
        or teacher_review is not None
        or latest_generation != item.generation
        or run.generation != item.generation
        or step.generation != item.generation
    ):
        _fail(409, "CODEX_WORK_INPUT_STALE", "Work item is no longer current")
    request, input_version, context = _request_components(
        db,
        owner_id=item.owner_id,
        batch_id=item.grading_batch_id,
        submission_id=item.submission_id,
        answer_id=item.student_answer_id,
    )
    if (
        input_version != item.input_version
        or canonical_hash(request) != item.request_hash
        or request != item.request_payload
    ):
        _fail(409, "CODEX_WORK_INPUT_STALE", "Work item input contract has drifted")
    return run, step, context


def _lock_item_scope(db: Session, item_id: uuid.UUID) -> CodexWorkItem | None:
    scope = db.execute(
        select(
            CodexWorkItem.grading_batch_id,
            CodexWorkItem.submission_id,
            CodexWorkItem.student_answer_id,
            ProcessingStep.processing_run_id,
            CodexWorkItem.processing_step_id,
        )
        .join(ProcessingStep, ProcessingStep.id == CodexWorkItem.processing_step_id)
        .where(CodexWorkItem.id == item_id)
    ).one_or_none()
    if scope is None:
        return None
    batch_id, submission_id, answer_id, run_id, step_id = scope
    # Frozen parent-to-child order. No provider call is made while these locks are held.
    db.scalar(select(GradingBatch.id).where(GradingBatch.id == batch_id).with_for_update())
    db.scalar(select(Submission.id).where(Submission.id == submission_id).with_for_update())
    answer = db.scalar(
        select(StudentAnswer).where(StudentAnswer.id == answer_id).with_for_update()
    )
    if answer is not None:
        db.scalars(
            select(QuestionRecognitionEvidence.id)
            .where(
                QuestionRecognitionEvidence.student_answer_id == answer.id,
                QuestionRecognitionEvidence.status == "confirmed",
                QuestionRecognitionEvidence.stale_at.is_(None),
            )
            .with_for_update()
        ).all()
        rubric_ids = list(
            db.scalars(
                select(StructuredRubricVersion.id).where(
                    StructuredRubricVersion.question_id == answer.question_id,
                    StructuredRubricVersion.status == "confirmed",
                )
            )
        )
        if rubric_ids:
            reference_ids = list(
                db.scalars(
                    select(StructuredRubricVersion.reference_answer_version_id).where(
                        StructuredRubricVersion.id.in_(rubric_ids)
                    )
                )
            )
            db.scalars(
                select(ReferenceAnswerVersion.id)
                .where(ReferenceAnswerVersion.id.in_(reference_ids))
                .with_for_update()
            ).all()
            db.scalars(
                select(StructuredRubricVersion.id)
                .where(StructuredRubricVersion.id.in_(rubric_ids))
                .with_for_update()
            ).all()
            db.scalars(
                select(RubricCriterion.id)
                .where(RubricCriterion.rubric_version_id.in_(rubric_ids))
                .with_for_update()
            ).all()
    db.scalar(select(ProcessingRun.id).where(ProcessingRun.id == run_id).with_for_update())
    db.scalar(select(ProcessingStep.id).where(ProcessingStep.id == step_id).with_for_update())
    return db.scalar(
        select(CodexWorkItem)
        .where(CodexWorkItem.id == item_id)
        .with_for_update(skip_locked=True)
    )


def _mark_stale(item: CodexWorkItem, *, code: str = "CODEX_WORK_INPUT_STALE") -> None:
    item.status = "stale"
    item.retryable = False
    item.error_code = code
    item.stale_at = now_utc()
    item.lease_token_hash = None
    item.lease_owner = None
    item.lease_expires_at = None


def claim_work_items(
    db: Session,
    *,
    worker_id: str,
    limit: int,
    lease_seconds: int,
) -> list[dict[str, Any]]:
    now = now_utc()
    expired_ids = list(
        db.scalars(
            select(CodexWorkItem.id)
            .where(
                CodexWorkItem.status == "leased",
                CodexWorkItem.lease_expires_at <= now,
            )
            .order_by(CodexWorkItem.created_at, CodexWorkItem.id)
        )
    )
    for expired_id in expired_ids:
        expired_item = _lock_item_scope(db, expired_id)
        if (
            expired_item is None
            or expired_item.status != "leased"
            or expired_item.lease_expires_at is None
            or _as_utc(expired_item.lease_expires_at) > now
        ):
            continue
        expired_item.lease_token_hash = None
        expired_item.lease_owner = None
        expired_item.lease_expires_at = None
        if expired_item.attempt >= expired_item.max_attempts:
            expired_item.status = "terminal_failed"
            expired_item.retryable = False
            expired_item.error_code = "CODEX_LEASE_ATTEMPTS_EXHAUSTED"
            expired_item.failed_at = now
        else:
            expired_item.status = "queued"
            expired_item.available_at = now
        db.add(
            AuditLog(
                actor_id=expired_item.owner_id,
                action="codex_local.lease_expired",
                resource_type="codex_work_item",
                resource_id=str(expired_item.id),
                metadata_={
                    "attempt": expired_item.attempt,
                    "status": expired_item.status,
                },
            )
        )
    candidate_ids = list(
        db.scalars(
            select(CodexWorkItem.id)
            .where(
                CodexWorkItem.status == "queued",
                (CodexWorkItem.available_at.is_(None) | (CodexWorkItem.available_at <= now)),
            )
            .order_by(CodexWorkItem.created_at, CodexWorkItem.id)
            .limit(limit)
        )
    )
    claimed: list[dict[str, Any]] = []
    for item_id in candidate_ids:
        locked_item = _lock_item_scope(db, item_id)
        if locked_item is None or locked_item.status != "queued":
            continue
        try:
            _current_item_state(db, locked_item)
        except CodexLocalProblem as exc:
            if exc.code == "CODEX_WORK_INPUT_STALE":
                _mark_stale(locked_item)
                continue
            raise
        raw_token = secrets.token_urlsafe(32)
        locked_item.status = "leased"
        locked_item.attempt += 1
        locked_item.lease_token_hash = _sha256(raw_token)
        locked_item.lease_owner = worker_id
        locked_item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        locked_item.started_at = locked_item.started_at or now
        claimed.append(
            {
                "work_item_id": str(locked_item.id),
                "processing_step_id": str(locked_item.processing_step_id),
                "processing_run_id": str(locked_item.step.processing_run_id),
                "generation": locked_item.generation,
                "request_hash": locked_item.request_hash,
                "request": locked_item.request_payload,
                "lease_token": raw_token,
                "lease_expires_at": locked_item.lease_expires_at.isoformat(),
            }
        )
        db.add(
            AuditLog(
                actor_id=locked_item.owner_id,
                action="codex_local.claimed",
                resource_type="codex_work_item",
                resource_id=str(locked_item.id),
                metadata_={"attempt": locked_item.attempt, "worker_id": worker_id},
            )
        )
    db.commit()
    return claimed


def submit_work_item(
    db: Session,
    *,
    item_id: uuid.UUID,
    worker_id: str,
    lease_token: str,
    request_hash: str,
    response: dict[str, Any],
) -> CodexWorkItem:
    token_hash = _sha256(lease_token)
    item = db.get(CodexWorkItem, item_id)
    if item is None:
        _fail(404, "CODEX_WORK_ITEM_NOT_FOUND", "Work item not found")

    def replayed(candidate: CodexWorkItem) -> bool:
        if candidate.submitted_lease_token_hash is None or not hmac.compare_digest(
            candidate.submitted_lease_token_hash, token_hash
        ):
            return False
        try:
            replay_payload = canonicalize(
                AIGradingOutput.model_validate(response).model_dump(mode="json")
            )
        except ValidationError:
            replay_payload = canonicalize(response)
        if (
            candidate.response_hash == canonical_hash(replay_payload)
            and candidate.request_hash == request_hash
        ):
            return True
        _fail(409, "CODEX_RESPONSE_CONFLICT", "Submitted lease has a different response")

    if replayed(item):
        return item
    db.expire(item)
    item = _lock_item_scope(db, item_id)
    if item is None:
        _fail(404, "CODEX_WORK_ITEM_NOT_FOUND", "Work item not found")
    if replayed(item):
        return item
    if (
        item.status != "leased"
        or item.lease_owner != worker_id
        or item.lease_token_hash is None
        or not hmac.compare_digest(item.lease_token_hash, token_hash)
    ):
        _fail(409, "CODEX_LEASE_FENCED", "Lease is not current")
    if item.lease_expires_at is None or _as_utc(item.lease_expires_at) <= now_utc():
        _fail(409, "CODEX_LEASE_FENCED", "Lease has expired")
    if item.request_hash != request_hash:
        _fail(409, "CODEX_REQUEST_HASH_CONFLICT", "Request hash does not match work item")
    try:
        _, _, context = _current_item_state(db, item)
    except CodexLocalProblem as exc:
        if exc.code == "CODEX_WORK_INPUT_STALE":
            _mark_stale(item)
            db.commit()
        raise
    try:
        output = validate_output(response, context)
    except (ValidationError, ValueError) as exc:
        _fail(
            422,
            "CODEX_RESPONSE_INVALID",
            "Response does not satisfy the strict suggestion contract",
            {"reason": str(exc)},
        )
    canonical_response = canonicalize(output.model_dump(mode="json"))
    item.status = "submitted"
    item.response_payload = canonical_response
    item.response_hash = canonical_hash(canonical_response)
    item.submitted_lease_token_hash = token_hash
    item.submitted_at = now_utc()
    item.lease_token_hash = None
    item.lease_owner = None
    item.lease_expires_at = None
    db.add(
        AuditLog(
            actor_id=item.owner_id,
            action="codex_local.submitted",
            resource_type="codex_work_item",
            resource_id=str(item.id),
            metadata_={"worker_id": worker_id, "response_hash": item.response_hash},
        )
    )
    db.commit()
    return item


def _apply_item_scope(db: Session, item_id: uuid.UUID) -> CodexWorkItem | None:
    """Lock one apply transaction in the frozen parent-to-child order."""
    preflight = db.execute(
        select(
            CodexWorkItem.grading_batch_id,
            CodexWorkItem.submission_id,
            CodexWorkItem.student_answer_id,
            ProcessingStep.processing_run_id,
            CodexWorkItem.processing_step_id,
            CodexWorkItem.request_payload,
            CodexWorkItem.request_hash,
            CodexWorkItem.response_hash,
        )
        .join(ProcessingStep, ProcessingStep.id == CodexWorkItem.processing_step_id)
        .where(CodexWorkItem.id == item_id)
    ).one_or_none()
    if preflight is None:
        return None
    (
        batch_id,
        submission_id,
        answer_id,
        run_id,
        step_id,
        request_payload,
        request_hash,
        response_hash,
    ) = preflight
    question_id = db.scalar(
        select(StudentAnswer.question_id).where(StudentAnswer.id == answer_id)
    )
    binding_id: uuid.UUID | None
    rubric_id: uuid.UUID | None
    reference_id: uuid.UUID | None
    try:
        payload = request_payload["processing_input"]["payload"]
        binding_id = uuid.UUID(payload["legacy_projection"]["binding_id"])
        rubric_id = uuid.UUID(payload["formal"]["structured_rubric"]["id"])
        reference_id = uuid.UUID(payload["formal"]["reference_answer"]["id"])
    except (KeyError, TypeError, ValueError):
        binding_id = None
        rubric_id = None
        reference_id = None
    binding_preflight = (
        db.execute(
            select(
                AssignmentRubricPublicationBinding.legacy_rubric_version_id,
                AssignmentRubricPublicationBinding.mapping,
            ).where(AssignmentRubricPublicationBinding.id == binding_id)
        ).one_or_none()
        if binding_id is not None
        else None
    )
    question_rubric_ids: set[uuid.UUID] = set()
    rubric_item_ids: set[uuid.UUID] = set()
    if binding_preflight is not None and question_id is not None:
        _, binding_mapping = binding_preflight
        for row in binding_mapping:
            if not isinstance(row, dict) or row.get("question_id") != str(question_id):
                continue
            try:
                question_rubric_ids.add(uuid.UUID(str(row["legacy_question_rubric_id"])))
                rubric_item_ids.update(
                    uuid.UUID(str(entry["rubric_item_id"]))
                    for entry in row.get("criteria", [])
                    if isinstance(entry, dict)
                )
            except (KeyError, TypeError, ValueError):
                question_rubric_ids.clear()
                rubric_item_ids.clear()
                break

    # Frozen order: parents and every scoring input before run/step/work, then children.
    db.scalar(select(GradingBatch.id).where(GradingBatch.id == batch_id).with_for_update())
    db.scalar(select(Submission.id).where(Submission.id == submission_id).with_for_update())
    db.scalar(select(StudentAnswer.id).where(StudentAnswer.id == answer_id).with_for_update())
    if binding_id is not None:
        db.scalar(
            select(AssignmentRubricPublicationBinding.id)
            .where(AssignmentRubricPublicationBinding.id == binding_id)
            .with_for_update()
        )
    db.scalars(
        select(QuestionRecognitionEvidence.id)
        .where(
            QuestionRecognitionEvidence.student_answer_id == answer_id,
            QuestionRecognitionEvidence.status == "confirmed",
            QuestionRecognitionEvidence.stale_at.is_(None),
        )
        .order_by(
            QuestionRecognitionEvidence.recognition_version,
            QuestionRecognitionEvidence.id,
        )
        .with_for_update()
    ).all()
    if rubric_id is not None:
        db.scalar(
            select(StructuredRubricVersion.id)
            .where(StructuredRubricVersion.id == rubric_id)
            .with_for_update()
        )
    if reference_id is not None:
        db.scalar(
            select(ReferenceAnswerVersion.id)
            .where(ReferenceAnswerVersion.id == reference_id)
            .with_for_update()
        )
    if rubric_id is not None:
        db.scalars(
            select(RubricCriterion.id)
            .where(RubricCriterion.rubric_version_id == rubric_id)
            .order_by(RubricCriterion.id)
            .with_for_update()
        ).all()
    if question_rubric_ids:
        db.scalars(
            select(QuestionRubric.id)
            .where(QuestionRubric.id.in_(question_rubric_ids))
            .order_by(QuestionRubric.id)
            .with_for_update()
        ).all()
    if rubric_item_ids:
        db.scalars(
            select(RubricItem.id)
            .where(RubricItem.id.in_(rubric_item_ids))
            .order_by(RubricItem.id)
            .with_for_update()
        ).all()
    db.scalar(select(ProcessingRun.id).where(ProcessingRun.id == run_id).with_for_update())
    db.scalar(select(ProcessingStep.id).where(ProcessingStep.id == step_id).with_for_update())
    item = db.scalar(
        select(CodexWorkItem).where(CodexWorkItem.id == item_id).with_for_update()
    )
    if item is None:
        return None
    if response_hash is not None:
        job_key = "pcx:" + _sha256(f"{item_id}:{request_hash}:{response_hash}")
        legacy_jobs = list(
            db.scalars(
                select(GradingJob)
                .where(
                    GradingJob.owner_id == item.owner_id,
                    GradingJob.idempotency_key == job_key,
                )
                .with_for_update()
            )
        )
        for job in legacy_jobs:
            results = list(
                db.scalars(
                    select(GradingResult)
                    .where(GradingResult.grading_job_id == job.id)
                    .with_for_update()
                )
            )
            for result in results:
                db.scalars(
                    select(GradingCriterionResult.id)
                    .where(GradingCriterionResult.grading_result_id == result.id)
                    .with_for_update()
                ).all()
                db.scalars(
                    select(GradingEvidence.id)
                    .where(GradingEvidence.grading_result_id == result.id)
                    .with_for_update()
                ).all()
        strict_jobs = list(
            db.scalars(
                select(AIScoringJob)
                .where(
                    AIScoringJob.owner_id == item.owner_id,
                    AIScoringJob.idempotency_key == job_key,
                )
                .with_for_update()
            )
        )
        for strict_job in strict_jobs:
            db.scalars(
                select(AICriterionSuggestion.id)
                .where(AICriterionSuggestion.ai_scoring_job_id == strict_job.id)
                .with_for_update()
            ).all()
            db.scalars(
                select(AIFeedbackDraft.id)
                .where(AIFeedbackDraft.ai_scoring_job_id == strict_job.id)
                .with_for_update()
            ).all()
    return item


def _apply_contract_conflict(message: str) -> NoReturn:
    _fail(409, "CODEX_APPLY_CONTRACT_CONFLICT", message)


def _binding_item_map(
    db: Session,
    *,
    item: CodexWorkItem,
    answer: StudentAnswer,
    binding: AssignmentRubricPublicationBinding,
    criteria: list[RubricCriterion],
) -> tuple[QuestionRubric, dict[str, tuple[RubricCriterion, RubricItem]]]:
    if not criteria:
        _apply_contract_conflict("Structured rubric has no criteria")
    question_mapping = [
        row
        for row in binding.mapping
        if isinstance(row, dict) and row.get("question_id") == str(answer.question_id)
    ]
    if len(question_mapping) != 1:
        _apply_contract_conflict("Legacy binding has no unique mapping for the question")
    row = question_mapping[0]
    if row.get("structured_rubric_version_id") != str(criteria[0].rubric_version_id):
        _apply_contract_conflict("Legacy binding points to a different structured rubric")
    try:
        question_rubric_id = uuid.UUID(str(row["legacy_question_rubric_id"]))
    except (KeyError, TypeError, ValueError):
        _apply_contract_conflict("Legacy question rubric mapping is malformed")
    question_rubric = db.get(QuestionRubric, question_rubric_id)
    if (
        question_rubric is None
        or question_rubric.question_id != answer.question_id
        or question_rubric.rubric_version_id != binding.legacy_rubric_version_id
    ):
        _apply_contract_conflict("Legacy question rubric is not current")
    mapped: dict[str, tuple[RubricCriterion, RubricItem]] = {}
    by_id = {str(criterion.id): criterion for criterion in criteria}
    raw_criteria = row.get("criteria")
    if not isinstance(raw_criteria, list):
        _apply_contract_conflict("Legacy criterion mapping is malformed")
    for entry in raw_criteria:
        if not isinstance(entry, dict):
            _apply_contract_conflict("Legacy criterion mapping is malformed")
        criterion = by_id.get(str(entry.get("criterion_id")))
        try:
            rubric_item_id = uuid.UUID(str(entry.get("rubric_item_id")))
        except (TypeError, ValueError):
            _apply_contract_conflict("Legacy rubric item identity is malformed")
        rubric_item = db.get(RubricItem, rubric_item_id)
        if (
            criterion is None
            or rubric_item is None
            or rubric_item.question_rubric_id != question_rubric.id
            or Decimal(str(rubric_item.points)) != Decimal(str(criterion.max_points))
            or criterion.stable_key in mapped
        ):
            _apply_contract_conflict("Legacy criterion mapping is incomplete or inconsistent")
        mapped[criterion.stable_key] = (criterion, rubric_item)
    if set(mapped) != {criterion.stable_key for criterion in criteria}:
        _apply_contract_conflict("Every structured criterion must map to one legacy rubric item")
    return question_rubric, mapped


def _validate_existing_apply(
    db: Session,
    *,
    item: CodexWorkItem,
    contract: ApplyContract,
    mapped: dict[str, tuple[RubricCriterion, RubricItem]],
    lock: bool,
) -> tuple[GradingJob, GradingResult] | None:
    job_query = select(GradingJob).where(
        GradingJob.owner_id == item.owner_id,
        GradingJob.idempotency_key == contract.job_key,
    )
    job = db.scalar(job_query.with_for_update() if lock else job_query)
    if job is None:
        if item.grading_job_id is not None or item.grading_result_id is not None:
            _apply_contract_conflict("Applied child references are incomplete")
        return None
    result_query = (
        select(GradingResult)
        .where(GradingResult.grading_job_id == job.id)
        .order_by(GradingResult.id)
    )
    results = list(db.scalars(result_query.with_for_update() if lock else result_query))
    if len(results) != 1:
        _apply_contract_conflict("Existing grading child is incomplete or ambiguous")
    result = results[0]
    if (
        job.owner_id != item.owner_id
        or job.grading_batch_id != item.grading_batch_id
        or job.submission_id != item.submission_id
        or job.question_id != contract.question.id
        or job.rubric_version_id != contract.binding.legacy_rubric_version_id
        or job.status != "completed"
        or job.provider != "codex_local"
        or job.provider_version != "local"
        or job.prompt_version != PROMPT_VERSION
        or job.config_version != CONFIG_VERSION
        or result.student_answer_id != item.student_answer_id
        or result.grading_job_id != job.id
        or result.question_id != contract.question.id
        or result.rubric_version_id != contract.binding.legacy_rubric_version_id
        or result.provider != "codex_local"
        or result.provider_version != "local"
        or result.grading_method != "codex_assisted"
        or result.status != "suggested"
        or not result.requires_review
        or Decimal(str(result.max_score)) != Decimal(str(contract.question.max_score))
        or (
            Decimal(str(result.score))
            if result.score is not None
            else None
        )
        != contract.output.total_suggested_points
    ):
        _apply_contract_conflict("Existing grading child does not match the work contract")
    criterion_query = (
        select(GradingCriterionResult)
        .where(GradingCriterionResult.grading_result_id == result.id)
        .order_by(GradingCriterionResult.rubric_item_id)
    )
    criterion_rows = list(
        db.scalars(criterion_query.with_for_update() if lock else criterion_query)
    )
    by_item = {row.rubric_item_id: row for row in criterion_rows}
    suggestions = {row.criterion_stable_key: row for row in contract.output.criteria}
    if len(by_item) != len(mapped):
        _apply_contract_conflict("Legacy criterion children are incomplete")
    for key, (_, rubric_item) in mapped.items():
        row = by_item.get(rubric_item.id)
        suggestion = suggestions[key]
        if (
            row is None
            or row.status != suggestion.public_status()
            or (
                Decimal(str(row.awarded_points))
                if row.awarded_points is not None
                else None
            )
            != suggestion.suggested_points
            or Decimal(str(row.max_points)) != suggestion.max_points
            or row.reason != suggestion.reasoning_summary
            or (
                Decimal(str(row.confidence)) if row.confidence is not None else None
            )
            != suggestion.confidence
        ):
            _apply_contract_conflict("Legacy criterion child does not match response")
    evidence_query = (
        select(GradingEvidence)
        .where(GradingEvidence.grading_result_id == result.id)
        .order_by(GradingEvidence.id)
    )
    evidence_rows = list(
        db.scalars(evidence_query.with_for_update() if lock else evidence_query)
    )
    expected_evidence = {
        ref for suggestion in contract.output.criteria for ref in suggestion.evidence_refs
    }
    actual_evidence = {
        str(region.id)
        for row in evidence_rows
        for region in db.scalars(
            select(StudentAnswerRegion).where(
                StudentAnswerRegion.student_answer_id == item.student_answer_id,
                StudentAnswerRegion.submission_page_id == row.submission_page_id,
                StudentAnswerRegion.x == row.x,
                StudentAnswerRegion.y == row.y,
                StudentAnswerRegion.width == row.width,
                StudentAnswerRegion.height == row.height,
            )
        )
    }
    if actual_evidence != expected_evidence or len(evidence_rows) != len(expected_evidence):
        _apply_contract_conflict("Legacy evidence children do not match response")
    return job, result


def _validate_strict_child(
    db: Session,
    *,
    item: CodexWorkItem,
    contract: ApplyContract,
    lock: bool,
) -> AIScoringJob:
    job_query = select(AIScoringJob).where(
        AIScoringJob.owner_id == item.owner_id,
        AIScoringJob.idempotency_key == contract.job_key,
    )
    job = db.scalar(job_query.with_for_update() if lock else job_query)
    if job is None:
        _apply_contract_conflict("Strict grading child is missing")
    expected_status = (
        "completed"
        if all(row.suggested_points is not None for row in contract.output.criteria)
        else "abstained"
        if all(row.suggested_points is None for row in contract.output.criteria)
        else "partially_completed"
    )
    latest_generation = db.scalar(
        select(func.max(AIScoringJob.generation)).where(
            AIScoringJob.student_answer_id == item.student_answer_id
        )
    )
    if (
        job.assignment_id != contract.assignment.id
        or job.question_id != contract.question.id
        or job.submission_id != item.submission_id
        or job.student_answer_id != item.student_answer_id
        or job.recognition_evidence_id != contract.evidence.id
        or job.reference_answer_version_id != contract.reference.id
        or job.rubric_version_id != contract.rubric.id
        or job.question_version != contract.answer.question_version_reference
        or job.scoring_input_version != scoring_input_version(contract.evidence)
        or job.status != expected_status
        or job.idempotency_key != contract.job_key
        or job.generation != latest_generation
        or job.provider != "codex_local"
        or job.model != "local"
        or job.model_snapshot != "local"
        or job.endpoint_mode != "internal_apply"
        or job.prompt_version != PROMPT_VERSION
        or job.schema_version != OUTPUT_SCHEMA
        or job.provider_config_version != CONFIG_VERSION
        or job.grading_config_version != CONFIG_VERSION
        or job.request_hash != contract.strict_request_hash
        or job.response_hash != item.response_hash
        or job.retryable
        or job.error_code is not None
    ):
        _apply_contract_conflict("Strict grading job does not match work contract")
    suggestion_query = (
        select(AICriterionSuggestion)
        .where(AICriterionSuggestion.ai_scoring_job_id == job.id)
        .order_by(AICriterionSuggestion.criterion_stable_key)
    )
    suggestion_rows = list(
        db.scalars(suggestion_query.with_for_update() if lock else suggestion_query)
    )
    expected = {row.criterion_stable_key: row for row in contract.output.criteria}
    actual = {row.criterion_stable_key: row for row in suggestion_rows}
    criteria_by_key = {row.stable_key: row for row in contract.criteria}
    if set(actual) != set(expected) or len(actual) != len(suggestion_rows):
        _apply_contract_conflict("Strict criterion children are incomplete")
    for key, suggestion in expected.items():
        row = actual[key]
        raw = suggestion.model_dump(mode="json")
        if (
            row.criterion_id != criteria_by_key[key].id
            or row.status != suggestion.status
            or row.decision != suggestion.decision
            or (
                Decimal(str(row.suggested_points))
                if row.suggested_points is not None
                else None
            )
            != suggestion.suggested_points
            or Decimal(str(row.max_points)) != suggestion.max_points
            or (
                Decimal(str(row.confidence)) if row.confidence is not None else None
            )
            != suggestion.confidence
            or row.evidence_refs != suggestion.evidence_refs
            or row.validation_refs != suggestion.validation_refs
            or row.error_codes != suggestion.error_codes
            or not row.requires_review
            or row.matched_steps != suggestion.matched_steps
            or row.missing_steps != suggestion.missing_steps
            or row.detected_errors != suggestion.detected_errors
            or row.reasoning_summary != suggestion.reasoning_summary
            or row.manual_review_reason != suggestion.manual_review_reason
            or row.student_feedback != suggestion.student_feedback
            or row.teacher_note != suggestion.teacher_note
            or row.abstained
            != (suggestion.abstained or suggestion.suggested_points is None)
            or row.deterministic_conflict
            != (suggestion.status == "deterministic_conflict")
            or row.input_hash != contract.strict_request_hash
            or row.output_hash != canonical_hash(raw)
        ):
            _apply_contract_conflict("Strict criterion child does not match response")
    feedback_query = select(AIFeedbackDraft).where(AIFeedbackDraft.ai_scoring_job_id == job.id)
    feedback = db.scalar(feedback_query.with_for_update() if lock else feedback_query)
    expected_ids = [str(row.id) for row in suggestion_rows]
    if (
        feedback is None
        or feedback.student_feedback != contract.output.student_feedback
        or feedback.teacher_summary != contract.output.teacher_summary
        or feedback.strengths != contract.output.strengths
        or feedback.improvements != contract.output.improvements
        or feedback.error_categories
        != sorted(
            {error for row in contract.output.criteria for error in row.detected_errors}
        )
        or feedback.risk_flags != contract.output.risk_flags
        or sorted(feedback.suggestion_ids) != sorted(expected_ids)
        or feedback.teacher_disposition != "pending"
    ):
        _apply_contract_conflict("Strict feedback child does not match response")
    return job


def validate_applied_work_item_current(db: Session, item: CodexWorkItem) -> None:
    """Read-only current-state and child-contract validation for reconciliation."""
    if (
        item.status != "applied"
        or item.response_payload is None
        or item.response_hash is None
        or canonical_hash(item.request_payload) != item.request_hash
        or canonical_hash(item.response_payload) != item.response_hash
        or item.grading_job_id is None
        or item.grading_result_id is None
    ):
        _apply_contract_conflict("Applied work item audit is incomplete")
    step = db.get(ProcessingStep, item.processing_step_id)
    run = db.get(ProcessingRun, step.processing_run_id) if step is not None else None
    submission = db.get(Submission, item.submission_id)
    answer = db.get(StudentAnswer, item.student_answer_id)
    batch = db.get(GradingBatch, item.grading_batch_id)
    if run is None or step is None or submission is None or answer is None or batch is None:
        _apply_contract_conflict("Applied work scope is unavailable")
    try:
        require_submission_mutable(submission)
    except GuardViolation:
        _apply_contract_conflict("Applied work submission is no longer mutable")
    latest_generation = db.scalar(
        select(func.max(ProcessingRun.generation)).where(
            ProcessingRun.grading_batch_id == item.grading_batch_id
        )
    )
    if (
        submission.owner_id != item.owner_id
        or batch.owner_id != item.owner_id
        or submission.grading_batch_id != batch.id
        or answer.submission_id != submission.id
        or run.owner_id != item.owner_id
        or run.grading_batch_id != batch.id
        or run.generation != item.generation
        or step.processing_run_id != run.id
        or step.submission_id != submission.id
        or step.student_answer_id != answer.id
        or step.generation != item.generation
        or step.kind != "codex_suggestion"
        or latest_generation != item.generation
        or db.scalar(
            select(TeacherReview.id)
            .where(TeacherReview.student_answer_id == answer.id)
            .limit(1)
        )
        is not None
        or db.scalar(
            select(GradingResult.id)
            .where(
                GradingResult.student_answer_id == answer.id,
                GradingResult.status.in_(REVIEWED_RESULT_STATUSES),
            )
            .limit(1)
        )
        is not None
    ):
        _apply_contract_conflict("Applied work is no longer current")
    try:
        request, input_version, context = _request_components(
            db,
            owner_id=item.owner_id,
            batch_id=item.grading_batch_id,
            submission_id=item.submission_id,
            answer_id=item.student_answer_id,
        )
        evidence = require_current_recognition_evidence(
            db,
            answer=answer,
            submission=submission,
            owner_id=item.owner_id,
        )
        output = validate_output(item.response_payload, context)
    except (CodexLocalProblem, GuardViolation, ValidationError, ValueError):
        _apply_contract_conflict("Applied scoring input no longer validates")
    if (
        input_version != item.input_version
        or canonical_hash(request) != item.request_hash
        or request != item.request_payload
    ):
        _apply_contract_conflict("Applied scoring input has drifted")
    try:
        formal = request["processing_input"]["payload"]["formal"]
        rubric = db.get(
            StructuredRubricVersion,
            uuid.UUID(formal["structured_rubric"]["id"]),
        )
        reference = db.get(
            ReferenceAnswerVersion,
            uuid.UUID(formal["reference_answer"]["id"]),
        )
        binding = db.get(
            AssignmentRubricPublicationBinding,
            uuid.UUID(
                request["processing_input"]["payload"]["legacy_projection"]["binding_id"]
            ),
        )
    except (KeyError, TypeError, ValueError):
        _apply_contract_conflict("Applied formal identity is malformed")
    assignment = db.get(Assignment, submission.assignment_id)
    question = db.get(Question, answer.question_id)
    if (
        rubric is None
        or reference is None
        or binding is None
        or assignment is None
        or question is None
        or binding.owner_id != item.owner_id
        or binding.assignment_id != assignment.id
        or binding.status != "confirmed"
        or assignment.active_rubric_version_id != binding.legacy_rubric_version_id
    ):
        _apply_contract_conflict("Applied formal binding has drifted")
    criteria = list(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == rubric.id)
            .order_by(RubricCriterion.display_order, RubricCriterion.id)
        )
    )
    _, mapped = _binding_item_map(
        db,
        item=item,
        answer=answer,
        binding=binding,
        criteria=criteria,
    )
    job_key = "pcx:" + _sha256(f"{item.id}:{item.request_hash}:{item.response_hash}")
    strict_hash = strict_request_hash(
        answer=answer,
        evidence=evidence,
        rubric_id=rubric.id,
        rubric_content_hash=rubric.content_hash,
        reference_id=reference.id,
        reference_content_hash=reference.content_hash,
        validation_id=None,
        criterion_stable_key=None,
        provider="codex_local",
        model="local",
        endpoint_mode="internal_apply",
        prompt_version=PROMPT_VERSION,
        schema_version=OUTPUT_SCHEMA,
        provider_config_version=CONFIG_VERSION,
        grading_config_version=CONFIG_VERSION,
    )
    contract = ApplyContract(
        run=run,
        step=step,
        submission=submission,
        answer=answer,
        assignment=assignment,
        question=question,
        evidence=evidence,
        reference=reference,
        rubric=rubric,
        binding=binding,
        criteria=criteria,
        output=output,
        strict_request_hash=strict_hash,
        job_key=job_key,
    )
    existing = _validate_existing_apply(
        db,
        item=item,
        contract=contract,
        mapped=mapped,
        lock=False,
    )
    if (
        existing is None
        or existing[0].id != item.grading_job_id
        or existing[1].id != item.grading_result_id
    ):
        _apply_contract_conflict("Applied work references a different legacy child")
    _validate_strict_child(db, item=item, contract=contract, lock=False)


def apply_work_item(
    db: Session,
    *,
    item_id: uuid.UUID,
    worker_id: str,
    request_hash: str,
    response_hash: str,
) -> CodexWorkItem:
    item = _apply_item_scope(db, item_id)
    if item is None:
        _fail(404, "CODEX_WORK_ITEM_NOT_FOUND", "Work item not found")
    replaying = item.status == "applied"
    if item.status not in {"submitted", "applied"}:
        _fail(
            409,
            "CODEX_APPLY_STATE_CONFLICT",
            "Only submitted work items can be applied",
            {"status": item.status},
        )
    if (
        item.request_hash != request_hash
        or item.response_hash is None
        or item.response_hash != response_hash
        or item.response_payload is None
        or canonical_hash(item.response_payload) != response_hash
    ):
        _apply_contract_conflict("Work hashes do not match the apply request")

    def stale_or_conflict(message: str) -> NoReturn:
        if replaying:
            _apply_contract_conflict(message)
        _mark_stale(item)
        db.commit()
        _fail(409, "CODEX_WORK_INPUT_STALE", message)

    run = db.get(ProcessingRun, item.step.processing_run_id)
    step = db.get(ProcessingStep, item.processing_step_id)
    submission = db.get(Submission, item.submission_id)
    answer = db.get(StudentAnswer, item.student_answer_id)
    batch = db.get(GradingBatch, item.grading_batch_id)
    if run is None or step is None or submission is None or answer is None or batch is None:
        stale_or_conflict("Work item scope no longer exists")
    try:
        require_submission_mutable(submission)
    except GuardViolation:
        stale_or_conflict("Submission is no longer mutable")
    latest_generation = db.scalar(
        select(func.max(ProcessingRun.generation)).where(
            ProcessingRun.grading_batch_id == item.grading_batch_id
        )
    )
    reviewed_result = db.scalar(
        select(GradingResult.id)
        .where(
            GradingResult.student_answer_id == answer.id,
            GradingResult.status.in_(REVIEWED_RESULT_STATUSES),
        )
        .limit(1)
    )
    teacher_review = db.scalar(
        select(TeacherReview.id).where(TeacherReview.student_answer_id == answer.id).limit(1)
    )
    if (
        submission.owner_id != item.owner_id
        or batch.owner_id != item.owner_id
        or submission.grading_batch_id != batch.id
        or answer.submission_id != submission.id
        or run.owner_id != item.owner_id
        or run.grading_batch_id != batch.id
        or run.generation != item.generation
        or step.processing_run_id != run.id
        or step.submission_id != submission.id
        or step.student_answer_id != answer.id
        or step.generation != item.generation
        or step.kind != "codex_suggestion"
        or latest_generation != item.generation
        or reviewed_result is not None
        or teacher_review is not None
    ):
        stale_or_conflict("Work item is no longer eligible for apply")
    try:
        request, input_version, context = _request_components(
            db,
            owner_id=item.owner_id,
            batch_id=item.grading_batch_id,
            submission_id=item.submission_id,
            answer_id=item.student_answer_id,
        )
        evidence = require_current_recognition_evidence(
            db,
            answer=answer,
            submission=submission,
            owner_id=item.owner_id,
        )
        output = validate_output(item.response_payload, context)
    except (CodexLocalProblem, GuardViolation, ValidationError, ValueError):
        stale_or_conflict("Current scoring contract no longer validates")
    if (
        input_version != item.input_version
        or canonical_hash(request) != item.request_hash
        or request != item.request_payload
    ):
        stale_or_conflict("Current scoring input has drifted")

    formal = request["processing_input"]["payload"]["formal"]
    rubric = db.get(
        StructuredRubricVersion,
        uuid.UUID(formal["structured_rubric"]["id"]),
    )
    reference = db.get(
        ReferenceAnswerVersion,
        uuid.UUID(formal["reference_answer"]["id"]),
    )
    binding = db.get(
        AssignmentRubricPublicationBinding,
        uuid.UUID(request["processing_input"]["payload"]["legacy_projection"]["binding_id"]),
    )
    assignment = db.get(Assignment, submission.assignment_id)
    question = db.get(Question, answer.question_id)
    if (
        rubric is None
        or reference is None
        or binding is None
        or assignment is None
        or question is None
        or binding.owner_id != item.owner_id
        or binding.assignment_id != assignment.id
        or binding.status != "confirmed"
        or assignment.active_rubric_version_id != binding.legacy_rubric_version_id
    ):
        stale_or_conflict("Current formal binding has drifted")
    criteria = list(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == rubric.id)
            .order_by(RubricCriterion.display_order, RubricCriterion.id)
        )
    )
    _, mapped = _binding_item_map(
        db,
        item=item,
        answer=answer,
        binding=binding,
        criteria=criteria,
    )
    job_key = "pcx:" + _sha256(f"{item.id}:{request_hash}:{response_hash}")
    strict_hash = strict_request_hash(
        answer=answer,
        evidence=evidence,
        rubric_id=rubric.id,
        rubric_content_hash=rubric.content_hash,
        reference_id=reference.id,
        reference_content_hash=reference.content_hash,
        validation_id=None,
        criterion_stable_key=None,
        provider="codex_local",
        model="local",
        endpoint_mode="internal_apply",
        prompt_version=PROMPT_VERSION,
        schema_version=OUTPUT_SCHEMA,
        provider_config_version=CONFIG_VERSION,
        grading_config_version=CONFIG_VERSION,
    )
    contract = ApplyContract(
        run=run,
        step=step,
        submission=submission,
        answer=answer,
        assignment=assignment,
        question=question,
        evidence=evidence,
        reference=reference,
        rubric=rubric,
        binding=binding,
        criteria=criteria,
        output=output,
        strict_request_hash=strict_hash,
        job_key=job_key,
    )
    existing = _validate_existing_apply(
        db,
        item=item,
        contract=contract,
        mapped=mapped,
        lock=True,
    )
    if existing is not None:
        _validate_strict_child(db, item=item, contract=contract, lock=True)
        job, result = existing
        if replaying:
            if (
                item.grading_job_id != job.id
                or item.grading_result_id != result.id
            ):
                _apply_contract_conflict(
                    "Applied work references a different grading child"
                )
            return item
    else:
        existing_strict = db.scalar(
            select(AIScoringJob)
            .where(
                AIScoringJob.owner_id == item.owner_id,
                AIScoringJob.idempotency_key == job_key,
            )
            .with_for_update()
        )
        if existing_strict is not None:
            _apply_contract_conflict("Strict grading child already exists without legacy child")
        strict_generation = (
            db.scalar(
                select(func.max(AIScoringJob.generation)).where(
                    AIScoringJob.student_answer_id == answer.id
                )
            )
            or 0
        ) + 1
        strict_job = AIScoringJob(
            owner_id=item.owner_id,
            assignment_id=assignment.id,
            question_id=question.id,
            submission_id=submission.id,
            student_answer_id=answer.id,
            recognition_evidence_id=evidence.id,
            reference_answer_version_id=reference.id,
            rubric_version_id=rubric.id,
            question_version=answer.question_version_reference,
            scoring_input_version=scoring_input_version(evidence),
            status=(
                "completed"
                if all(row.suggested_points is not None for row in output.criteria)
                else "abstained"
                if all(row.suggested_points is None for row in output.criteria)
                else "partially_completed"
            ),
            idempotency_key=job_key,
            generation=strict_generation,
            attempt=1,
            provider="codex_local",
            model="local",
            model_snapshot="local",
            endpoint_mode="internal_apply",
            prompt_version=PROMPT_VERSION,
            schema_version=OUTPUT_SCHEMA,
            provider_config_version=CONFIG_VERSION,
            grading_config_version=CONFIG_VERSION,
            request_hash=strict_hash,
            response_hash=response_hash,
            image_count=0,
            image_bytes=0,
            retryable=False,
            started_at=now_utc(),
            finished_at=now_utc(),
        )
        db.add(strict_job)
        db.flush()
        suggestion_ids: list[str] = []
        criterion_by_key = {criterion.stable_key: criterion for criterion in criteria}
        for suggestion in output.criteria:
            strict_row = AICriterionSuggestion(
                ai_scoring_job_id=strict_job.id,
                criterion_id=criterion_by_key[suggestion.criterion_stable_key].id,
                criterion_stable_key=suggestion.criterion_stable_key,
                status=suggestion.status,
                decision=suggestion.decision,
                suggested_points=suggestion.suggested_points,
                max_points=suggestion.max_points,
                confidence=suggestion.confidence,
                evidence_refs=suggestion.evidence_refs,
                validation_refs=suggestion.validation_refs,
                error_codes=suggestion.error_codes,
                requires_review=True,
                matched_steps=suggestion.matched_steps,
                missing_steps=suggestion.missing_steps,
                detected_errors=suggestion.detected_errors,
                reasoning_summary=suggestion.reasoning_summary,
                manual_review_reason=suggestion.manual_review_reason,
                student_feedback=suggestion.student_feedback,
                teacher_note=suggestion.teacher_note,
                abstained=suggestion.abstained or suggestion.suggested_points is None,
                deterministic_conflict=suggestion.status == "deterministic_conflict",
                input_hash=strict_hash,
                output_hash=canonical_hash(suggestion.model_dump(mode="json")),
            )
            db.add(strict_row)
            db.flush()
            suggestion_ids.append(str(strict_row.id))
        db.add(
            AIFeedbackDraft(
                ai_scoring_job_id=strict_job.id,
                student_feedback=output.student_feedback,
                teacher_summary=output.teacher_summary,
                strengths=output.strengths,
                improvements=output.improvements,
                error_categories=sorted(
                    {error for row in output.criteria for error in row.detected_errors}
                ),
                risk_flags=output.risk_flags,
                suggestion_ids=suggestion_ids,
            )
        )
        job = GradingJob(
            owner_id=item.owner_id,
            grading_batch_id=item.grading_batch_id,
            submission_id=item.submission_id,
            question_id=answer.question_id,
            rubric_version_id=binding.legacy_rubric_version_id,
            status="completed",
            provider="codex_local",
            provider_version="local",
            prompt_version=PROMPT_VERSION,
            config_version=CONFIG_VERSION,
            attempt=1,
            idempotency_key=job_key,
            started_at=now_utc(),
            completed_at=now_utc(),
        )
        db.add(job)
        db.flush()
        for previous in db.scalars(
            select(GradingResult)
            .where(
                GradingResult.student_answer_id == answer.id,
                GradingResult.status == "suggested",
            )
            .with_for_update()
        ):
            previous.status = "superseded"
        result = GradingResult(
            grading_job_id=job.id,
            student_answer_id=answer.id,
            question_id=answer.question_id,
            rubric_version_id=binding.legacy_rubric_version_id,
            grading_method="codex_assisted",
            provider="codex_local",
            provider_version="local",
            prompt_version=PROMPT_VERSION,
            score=output.total_suggested_points,
            max_score=question.max_score,
            confidence=None,
            recognized_answer_snapshot=(
                answer.corrected_text
                if answer.corrected_text is not None
                else answer.recognized_text
            ),
            reasoning_summary=output.teacher_summary or None,
            student_feedback=output.student_feedback or None,
            requires_review=True,
            status="suggested",
        )
        db.add(result)
        db.flush()
        suggestions = {row.criterion_stable_key: row for row in output.criteria}
        for key, (_, rubric_item) in mapped.items():
            suggestion = suggestions[key]
            db.add(
                GradingCriterionResult(
                    grading_result_id=result.id,
                    rubric_item_id=rubric_item.id,
                    status=suggestion.public_status(),
                    awarded_points=suggestion.suggested_points,
                    max_points=rubric_item.points,
                    reason=suggestion.reasoning_summary,
                    confidence=suggestion.confidence,
                )
            )
        evidence_ids = sorted(
            {ref for row in output.criteria for ref in row.evidence_refs}
        )
        evidence_regions = {
            str(region.id): region
            for region in db.scalars(
                select(StudentAnswerRegion).where(
                    StudentAnswerRegion.student_answer_id == answer.id,
                    StudentAnswerRegion.status == "confirmed",
                )
            )
        }
        effective_text = (
            answer.corrected_text
            if answer.corrected_text is not None
            else answer.recognized_text
        )
        for evidence_id in evidence_ids:
            region = evidence_regions.get(evidence_id)
            if region is None:
                _apply_contract_conflict("Suggestion references non-current evidence")
            db.add(
                GradingEvidence(
                    grading_result_id=result.id,
                    student_answer_id=answer.id,
                    submission_page_id=region.submission_page_id,
                    evidence_type="answer_region",
                    quote=effective_text[:500] if effective_text else None,
                    x=region.x,
                    y=region.y,
                    width=region.width,
                    height=region.height,
                    description="Codex-assisted suggestion confirmed evidence",
                )
            )
    item.status = "applied"
    item.grading_job_id = job.id
    item.grading_result_id = result.id
    item.applied_at = now_utc()
    item.completed_at = item.applied_at
    item.retryable = False
    answer.status = "graded"
    db.add(
        AuditLog(
            actor_id=item.owner_id,
            action="codex_local.applied",
            resource_type="codex_work_item",
            resource_id=str(item.id),
            metadata_={
                "worker_id": worker_id,
                "request_hash": request_hash,
                "response_hash": response_hash,
                "grading_job_id": str(job.id),
                "grading_result_id": str(result.id),
                "provider": "codex_local",
                "suggestion_only": True,
            },
        )
    )
    db.commit()
    return item
