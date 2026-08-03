"""Teacher-owned central review and two-phase assignment publication."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.db.session import get_db
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentClass,
    AssignmentDraftRevision,
    AssignmentExplicitConfirmation,
    AssignmentGenerationJob,
    AssignmentPageAnalysis,
    AssignmentPublishReadinessSnapshot,
    AssignmentQuestionExtractionCandidate,
    AssignmentReviewItem,
    AssignmentReviewSession,
    AssignmentRubricDraftCandidate,
    AssignmentRubricPublicationBinding,
    AssignmentSourceFileAnalysis,
    AssignmentStatus,
    ClassStudent,
    GenerationIssue,
    MembershipStatus,
    PaperPage,
    PaperPageOrganizationSuggestion,
    PaperVersion,
    Question,
    QuestionRubric,
    QuestionStatus,
    ReferenceAnswerVersion,
    RubricCriterion,
    RubricItem,
    RubricVersion,
    SchoolClass,
    StoredFile,
    StructuredRubricVersion,
    Student,
    VersionStatus,
    now_utc,
)
from app.semantic_content import reference_answer_semantic_payload, semantic_hash
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["assignment-central-review"])
Db = Annotated[Session, Depends(get_db)]
ACTIVE = {"draft", "in_review", "changes_required", "ready_for_binding", "ready_to_publish"}
CONFIRMATION_TYPES = {
    "classes",
    "due_at",
    "total_score",
    "file_roles",
    "answer_sources",
    "paper_version",
    "reference_answers",
    "structured_rubrics",
    "legacy_binding",
}
REQUIRED_CONFIRMATIONS = CONFIRMATION_TYPES - {
    "answer_sources",
    "file_roles",
    "paper_version",
}
AUTOMATIC_CONFIRMATION_TYPES = REQUIRED_CONFIRMATIONS - {"legacy_binding"}
LEGACY_PROJECTION_SCHEMA_VERSION = "structured-rubric-projection-v3"
LEGACY_PROJECTION_PROFILE = "structured-to-legacy"
CONFIRMATION_FINGERPRINT_VERSION = "confirmation-fingerprint-v2"
REVIEW_SOURCE_SCHEMA_VERSION = "publish-content-v3"
BUNDLE_SCHEMA_VERSION = "assignment-review-bundle-v1"
PROJECTION_WRITE_LOCK_ORDER = (
    "assignment",
    "snapshot",
    "session",
    "paper",
    "questions",
    "binding",
    "formal_versions",
    "criteria",
    "legacy_rubric",
    "legacy_items",
    "confirmation",
)
MANUAL_FALLBACK_WARNINGS = {
    "DEPENDENCY_NOT_LOSSLESS",
    "ALTERNATIVE_PATH_NOT_LOSSLESS",
    "VALIDATION_RULE_NOT_LOSSLESS",
    "EXPECTED_EVIDENCE_NOT_LOSSLESS",
    "MANUAL_REVIEW_POLICY_NOT_LOSSLESS",
    "PARTIAL_CREDIT_POLICY_NOT_LOSSLESS",
    "ERROR_CATEGORY_NOT_LOSSLESS",
    "CRITERION_METADATA_NOT_LOSSLESS",
    "DEDUCTION_RULE_NOT_LOSSLESS",
    "COMMON_ERROR_CODES_NOT_LOSSLESS",
    "FEEDBACK_TEMPLATE_NOT_LOSSLESS",
}
RECOVERED_GENERATION_CODES = {
    "GENERATION_PARTIAL",
    "MANUAL_REVIEW_REQUIRED",
    "PAGE_ORGANIZATION_INCOMPLETE",
    "PROVIDER_UNAVAILABLE",
    "QUESTION_CONFIRMATION_REQUIRED",
    "QUESTION_PAPER_ROLE_UNCONFIRMED",
    "VALIDATION_FAILED",
}


def canonical(value: Any) -> Any:
    if isinstance(value, (datetime, uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): canonical(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def owned_assignment(
    db: Session, actor_id: uuid.UUID, assignment_id: uuid.UUID, *, lock: bool = False
) -> Assignment:
    query = select(Assignment).where(
        Assignment.id == assignment_id, Assignment.owner_id == actor_id
    )
    if lock:
        query = query.execution_options(populate_existing=True).with_for_update()
    item = db.scalar(query)
    if item is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    return item


def owned_session(
    db: Session, actor_id: uuid.UUID, session_id: uuid.UUID, *, lock: bool = False
) -> AssignmentReviewSession:
    query = select(AssignmentReviewSession).where(
        AssignmentReviewSession.id == session_id, AssignmentReviewSession.owner_id == actor_id
    )
    if lock:
        query = query.execution_options(populate_existing=True).with_for_update()
    row = db.scalar(query)
    if row is None:
        raise ApiProblem(404, "REVIEW_SESSION_NOT_FOUND", "审查会话不存在")
    return row


def questions(db: Session, paper_id: uuid.UUID) -> list[Question]:
    return list(
        db.scalars(
            select(Question)
            .where(Question.paper_version_id == paper_id, Question.status == QuestionStatus.active)
            .order_by(Question.display_order, Question.id)
        )
    )


def current_inputs(
    db: Session, assignment: Assignment
) -> tuple[AssignmentGenerationJob, AssignmentDraftRevision, PaperVersion]:
    job = db.scalar(
        select(AssignmentGenerationJob)
        .where(AssignmentGenerationJob.assignment_id == assignment.id)
        .order_by(AssignmentGenerationJob.generation.desc())
        .limit(1)
    )
    if job is None:
        raise ApiProblem(409, "GENERATION_REQUIRED", "尚无可审查的生成任务")
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.generation_job_id == job.id)
        .order_by(AssignmentDraftRevision.revision.desc())
        .limit(1)
    )
    paper = (
        db.get(PaperVersion, assignment.active_paper_version_id)
        if assignment.active_paper_version_id
        else None
    )
    if revision is None or paper is None:
        raise ApiProblem(409, "DRAFT_INPUT_REQUIRED", "生成修订或当前试卷版本缺失")
    return job, revision, paper


FORMAL_CURRENT_STATUSES = {"confirmed", "draft"}
FORMAL_HISTORY_STATUSES = {"confirmed", "draft", "retired"}
STALE_CANDIDATE_STATUSES = {"rejected", "retired", "stale", "superseded"}


def _select_formal_version(rows: list[Any], version_field: str) -> Any | None:
    """Prefer the newest confirmed formal version; otherwise expose the newest draft.

    This is deliberately independent of extraction candidates: candidates explain how a
    question got here, but cannot replace an already materialized formal version.
    """
    confirmed = [row for row in rows if row.status == "confirmed"]
    selected = confirmed or [row for row in rows if row.status == "draft"]
    return max(selected, key=lambda row: (getattr(row, version_field), str(row.id)), default=None)


def selected_question_version(db: Session, question: Question) -> dict[str, Any]:
    """Read-only lifecycle selection shared by central review and review bundles."""
    rubrics = list(
        db.scalars(
            select(StructuredRubricVersion)
            .where(
                StructuredRubricVersion.question_id == question.id,
                StructuredRubricVersion.status.in_(FORMAL_HISTORY_STATUSES),
            )
            .order_by(StructuredRubricVersion.rubric_version.desc(), StructuredRubricVersion.id)
        )
    )
    rubric = _select_formal_version(rubrics, "rubric_version")
    answers = list(
        db.scalars(
            select(ReferenceAnswerVersion)
            .where(
                ReferenceAnswerVersion.question_id == question.id,
                ReferenceAnswerVersion.status.in_(FORMAL_HISTORY_STATUSES),
            )
            .order_by(ReferenceAnswerVersion.version.desc(), ReferenceAnswerVersion.id)
        )
    )
    # A selected rubric is an explicit immutable answer association.  When there is
    # no current rubric, fall back to the answer's own confirmed-then-draft lifecycle.
    associated_answer = (
        db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
        if rubric is not None
        else None
    )
    answer = (
        associated_answer
        if associated_answer is not None and associated_answer.status in FORMAL_CURRENT_STATUSES
        else _select_formal_version(answers, "version")
    )
    criteria = (
        list(
            db.scalars(
                select(RubricCriterion)
                .where(RubricCriterion.rubric_version_id == rubric.id)
                .order_by(RubricCriterion.display_order, RubricCriterion.id)
            )
        )
        if rubric is not None
        else []
    )
    candidates = list(
        db.scalars(
            select(AssignmentQuestionExtractionCandidate)
            .where(AssignmentQuestionExtractionCandidate.materialized_question_id == question.id)
            .order_by(
                AssignmentQuestionExtractionCandidate.candidate_version.desc(),
                AssignmentQuestionExtractionCandidate.id,
            )
        )
    )
    current_candidate = next(
        (item for item in candidates if item.status not in STALE_CANDIDATE_STATUSES), None
    )
    return {
        "question": question,
        "answer": answer,
        "rubric": rubric,
        "criteria": criteria,
        "answer_versions": answers,
        "rubric_versions": rubrics,
        "candidate": current_candidate,
        "candidate_history": candidates,
    }


def selected_versions(db: Session, paper_id: uuid.UUID) -> list[dict[str, Any]]:
    return [selected_question_version(db, question) for question in questions(db, paper_id)]


def _source(kind: str, label: str) -> dict[str, str]:
    return {"kind": kind, "label": label}


def _answer_source(source_type: str) -> dict[str, str]:
    labels = {
        "teacher_official": "教师确认答案",
        "teacher_authored": "教师编写答案",
        "ai_generated": "AI 生成答案",
        "third_party": "外部参考答案",
        "unknown": "来源待确认",
    }
    return _source(source_type, labels.get(source_type, "参考答案"))


def _question_source(source_type: str) -> dict[str, str]:
    labels = {
        "manual": "教师录入题目",
        "ocr": "试卷识别题目",
        "ai_generated": "AI 提取题目",
        "imported": "导入题目",
    }
    return _source(source_type, labels.get(source_type, "试卷题目"))


def _question_provenance_json(
    candidate: AssignmentQuestionExtractionCandidate | None,
) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "id": str(candidate.id),
        "status": candidate.status,
        "candidate_version": candidate.candidate_version,
        "source_snapshot_hash": candidate.source_snapshot_hash,
        "materialized_question_id": str(candidate.materialized_question_id)
        if candidate.materialized_question_id
        else None,
        "source": _source("question_extraction", "题目提取记录"),
        "visibility": "teacher",
    }


def _answer_candidate_json(
    candidate: AssignmentAnswerDraftCandidate | None,
) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "id": str(candidate.id),
        "candidate_version": candidate.candidate_version,
        "teacher_edit_version": candidate.teacher_edit_version,
        "status": candidate.status,
        "source_snapshot_hash": candidate.source_snapshot_hash,
        "materialized_formal_id": str(candidate.materialized_reference_answer_id)
        if candidate.materialized_reference_answer_id
        else None,
        "source": _source("answer_candidate", "参考答案候选"),
        "content": candidate.normalized_content or candidate.raw_content,
        "confidence": str(candidate.confidence),
        "visibility": "teacher",
    }


def _rubric_candidate_json(
    candidate: AssignmentRubricDraftCandidate | None,
) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "id": str(candidate.id),
        "candidate_version": candidate.candidate_version,
        "teacher_edit_version": candidate.teacher_edit_version,
        "status": candidate.status,
        "source_snapshot_hash": candidate.source_snapshot_hash,
        "materialized_formal_id": str(candidate.materialized_structured_rubric_id)
        if candidate.materialized_structured_rubric_id
        else None,
        "source": _source("rubric_candidate", "评分标准候选"),
        "title": candidate.title,
        "total_points": str(candidate.total_points) if candidate.total_points is not None else None,
        "confidence": str(candidate.confidence),
        "visibility": "teacher",
    }


def _answer_content_payload(answer: ReferenceAnswerVersion) -> dict[str, Any]:
    return {
        "source_type": answer.source_type,
        "source_file": answer.source_file,
        "source_page": answer.source_page,
        "source_region": answer.source_region,
        "raw_content": answer.raw_content,
        "normalized_content": answer.normalized_content,
        "structured_content": answer.structured_content,
        "provenance": answer.provenance,
    }


def _criterion_payload(criterion: RubricCriterion) -> dict[str, Any]:
    return {
        "id": str(criterion.id),
        "key": criterion.stable_key,
        "title": criterion.title,
        "description": criterion.description,
        "points": str(criterion.max_points),
        "display_order": criterion.display_order,
        "criterion_type": criterion.criterion_type,
        "required": criterion.required,
        "dependencies": criterion.dependencies,
        "expected_evidence": criterion.expected_evidence,
        "validation_mode": criterion.validation_mode,
        "validation_rule": criterion.validation_rule,
        "manual_review_policy": criterion.manual_review_policy,
        "partial_credit_policy": criterion.partial_credit_policy,
        "error_category": criterion.error_category,
        "metadata": criterion.metadata_,
    }


def _rubric_criteria(db: Session, rubric_id: uuid.UUID) -> list[RubricCriterion]:
    return list(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == rubric_id)
            .order_by(RubricCriterion.display_order, RubricCriterion.id)
        )
    )


def _rubric_content_payload(db: Session, rubric: StructuredRubricVersion) -> dict[str, Any]:
    return {
        "question_version": rubric.question_version,
        "title": rubric.title,
        "total_points": str(rubric.total_points),
        "reference_answer_version_id": str(rubric.reference_answer_version_id),
        "criteria": [_criterion_payload(item) for item in _rubric_criteria(db, rubric.id)],
    }


def _answer_bundle_json(answer: ReferenceAnswerVersion | None) -> dict[str, Any] | None:
    if answer is None:
        return None
    return {
        "id": str(answer.id),
        "status": answer.status,
        "version": answer.version,
        "content_hash": answer.content_hash,
        "source": _answer_source(answer.source_type),
        "content": answer.normalized_content or answer.raw_content,
        "content_payload": _answer_content_payload(answer),
        "visibility": "teacher",
    }


def _rubric_bundle_json(
    db: Session, rubric: StructuredRubricVersion | None
) -> dict[str, Any] | None:
    if rubric is None:
        return None
    return {
        "id": str(rubric.id),
        "status": rubric.status,
        "version": rubric.rubric_version,
        "content_hash": rubric.content_hash,
        "reference_answer_version_id": str(rubric.reference_answer_version_id),
        "source": _source("structured_rubric", "结构化评分标准"),
        "title": rubric.title,
        "total_points": str(rubric.total_points),
        "criteria": [_criterion_payload(item) for item in _rubric_criteria(db, rubric.id)],
        "visibility": "teacher",
    }


def _answer_lifecycle(
    db: Session,
    revision_id: uuid.UUID,
    row: dict[str, Any],
) -> dict[str, Any]:
    candidates = list(
        db.scalars(
            select(AssignmentAnswerDraftCandidate)
            .where(
                AssignmentAnswerDraftCandidate.draft_revision_id == revision_id,
                AssignmentAnswerDraftCandidate.question_id == row["question"].id,
            )
            .order_by(
                AssignmentAnswerDraftCandidate.candidate_version.desc(),
                AssignmentAnswerDraftCandidate.id,
            )
        )
    )
    candidate = next(
        (item for item in candidates if item.status not in STALE_CANDIDATE_STATUSES),
        None,
    )
    materialized_candidate = next(
        (item for item in candidates if item.materialized_reference_answer_id is not None),
        None,
    )
    materialized = (
        db.get(
            ReferenceAnswerVersion,
            materialized_candidate.materialized_reference_answer_id,
        )
        if materialized_candidate is not None
        else None
    )
    return {
        "candidate": _answer_candidate_json(candidate),
        "candidate_history": [_answer_candidate_json(item) for item in candidates],
        "materialized": _answer_bundle_json(materialized),
        "selected": _answer_bundle_json(row["answer"]),
        "history": [_answer_bundle_json(item) for item in row["answer_versions"]],
        "visibility": "teacher",
    }


def _rubric_lifecycle(
    db: Session,
    revision_id: uuid.UUID,
    row: dict[str, Any],
) -> dict[str, Any]:
    candidates = list(
        db.scalars(
            select(AssignmentRubricDraftCandidate)
            .where(
                AssignmentRubricDraftCandidate.draft_revision_id == revision_id,
                AssignmentRubricDraftCandidate.question_id == row["question"].id,
            )
            .order_by(
                AssignmentRubricDraftCandidate.candidate_version.desc(),
                AssignmentRubricDraftCandidate.id,
            )
        )
    )
    candidate = next(
        (item for item in candidates if item.status not in STALE_CANDIDATE_STATUSES),
        None,
    )
    materialized_candidate = next(
        (item for item in candidates if item.materialized_structured_rubric_id is not None),
        None,
    )
    materialized = (
        db.get(
            StructuredRubricVersion,
            materialized_candidate.materialized_structured_rubric_id,
        )
        if materialized_candidate is not None
        else None
    )
    return {
        "candidate": _rubric_candidate_json(candidate),
        "candidate_history": [_rubric_candidate_json(item) for item in candidates],
        "materialized": _rubric_bundle_json(db, materialized),
        "selected": _rubric_bundle_json(db, row["rubric"]),
        "history": [_rubric_bundle_json(db, item) for item in row["rubric_versions"]],
        "visibility": "teacher",
    }


def review_bundle(db: Session, actor_id: uuid.UUID, assignment_id: uuid.UUID) -> dict[str, Any]:
    """Return the durable review view without creating or refreshing any review state."""
    assignment = owned_assignment(db, actor_id, assignment_id)
    job, revision, paper = current_inputs(db, assignment)
    session = db.scalar(
        select(AssignmentReviewSession)
        .where(
            AssignmentReviewSession.assignment_id == assignment.id,
            AssignmentReviewSession.owner_id == actor_id,
            AssignmentReviewSession.status.in_(ACTIVE),
        )
        .order_by(AssignmentReviewSession.created_at.desc(), AssignmentReviewSession.id)
        .limit(1)
    )
    binding = (
        db.scalar(
            select(AssignmentRubricPublicationBinding)
            .where(AssignmentRubricPublicationBinding.review_session_id == session.id)
            .order_by(
                AssignmentRubricPublicationBinding.binding_version.desc(),
                AssignmentRubricPublicationBinding.id,
            )
            .limit(1)
        )
        if session is not None
        else None
    )
    rows = selected_versions(db, paper.id)
    questions_payload: list[dict[str, Any]] = []
    for row in rows:
        question = row["question"]
        questions_payload.append(
            {
                "id": str(question.id),
                "number": question.question_number,
                "content_hash": digest(
                    {"content_text": question.content_text, "content_latex": question.content_latex}
                ),
                "content": question.content_text or question.content_latex,
                "source": _question_source(question.source),
                "provenance": _question_provenance_json(row["candidate"]),
                "visibility": "teacher",
                "answer": _answer_lifecycle(db, revision.id, row),
                "rubric": _rubric_lifecycle(db, revision.id, row),
            }
        )
    confirms = valid_confirmations(db, session) if session is not None else {}
    if session is not None:
        current_issues = generated_issues(db, session)
    else:
        missing_review = {
            "code": "REVIEW_SESSION_REQUIRED",
            "section": "review",
            "message": "请开始教师审查并确认当前内容",
            "entity": "assignment",
            "entity_id": str(assignment.id),
            "evidence": {},
        }
        current_issues = [
            missing_review
            | {
                "severity": "blocking",
                "source_hash": digest(missing_review),
            }
        ]
    current_keys = {(item["code"], item["source_hash"]) for item in current_issues}
    persisted_by_key = (
        {
            (item.issue_code, item.source_hash): item
            for item in db.scalars(
                select(AssignmentReviewItem)
                .where(
                    AssignmentReviewItem.review_session_id == session.id,
                    AssignmentReviewItem.status.not_in(["stale", "superseded"]),
                )
                .order_by(AssignmentReviewItem.updated_at.desc(), AssignmentReviewItem.id)
            )
            if (item.issue_code, item.source_hash) in current_keys
        }
        if session is not None
        else {}
    )
    blockers: list[dict[str, Any]] = []
    for issue in current_issues:
        persisted = persisted_by_key.get((issue["code"], issue["source_hash"]))
        disposition = persisted.status if persisted is not None else "open"
        if not _issue_blocks_review_bundle(issue["severity"], disposition):
            continue
        # Current blocking facts are never hidden by a disposition saved for an
        # earlier projection with the same issue code.
        blockers.append(
            {
                "id": str(persisted.id) if persisted is not None else None,
                "code": issue["code"],
                "section": issue["section"],
                "message": issue["message"],
                "entity": issue["entity"],
                "entity_id": issue["entity_id"],
                "severity": issue["severity"],
                "source_hash": issue["source_hash"],
                "status": disposition,
                "visibility": "teacher",
            }
        )
    expected_binding_hash = binding_source_hash(db, session) if session is not None else None
    projection_validation = (
        validate_current_projection_under_locks(
            db,
            session,
            binding_id=binding.id if binding is not None else None,
            lock=False,
            require_confirmed=False,
        )
        if session is not None and binding is not None
        else ProjectionValidation(None, None, False, "BINDING_NOT_CURRENT")
    )
    binding_projection_current = projection_validation.current
    confirmations = [
        {
            "id": str(item.id),
            "type": kind,
            "status": "confirmed",
            "source_hash": item.source_hash,
            "origin": item.confirmation_origin or "origin",
            "inherited": item.confirmation_origin == "inherited",
            "fingerprint_schema_version": item.fingerprint_schema_version,
            "binding_id": (
                str(item.confirmed_value["binding_id"])
                if kind == "legacy_binding"
                and projection_validation.current
                and projection_validation.confirmation is not None
                and projection_validation.confirmation.id == item.id
                else None
            ),
            "source_binding_hash": (
                item.confirmed_value["source_binding_hash"]
                if kind == "legacy_binding"
                and projection_validation.current
                and projection_validation.confirmation is not None
                and projection_validation.confirmation.id == item.id
                else None
            ),
            "confirmed_at": item.confirmed_at,
            "visibility": "teacher",
        }
        for kind, item in sorted(confirms.items())
    ]
    binding_is_current = (
        binding is not None
        and binding.status == "confirmed"
        and expected_binding_hash is not None
        and binding.source_binding_hash == expected_binding_hash
        and binding_projection_current
    )
    binding_payload = (
        {
            "id": str(binding.id),
            "status": (
                "stale"
                if expected_binding_hash is None
                or binding.source_binding_hash != expected_binding_hash
                or not binding_projection_current
                else binding.status
            ),
            "binding_version": binding.binding_version,
            "source_binding_hash": binding.source_binding_hash,
            "source_semantic_hash": binding.source_semantic_hash,
            "target_legacy_hash": binding.target_legacy_hash,
            "projection_profile": binding.projection_profile,
            "projection_version": binding.projection_version,
            "mapping": binding.mapping,
            "loss_report": binding.loss_report or [],
            "loss_report_hash": binding.loss_report_hash,
            "manual_review_required": bool(binding.loss_report),
            "projection_current": projection_validation.current,
            "projection_reason": projection_validation.reason,
            "expected_source_binding_hash": expected_binding_hash,
            "visibility": "teacher",
        }
        if binding is not None
        else None
    )
    payload: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "assignment_id": str(assignment.id),
        "version": {
            "generation": job.generation,
            "draft_revision_id": str(revision.id),
            "paper_version_id": str(paper.id),
            "source_snapshot_hash": job.source_snapshot_hash,
            "bundle_hash": None,
        },
        "status": (
            "missing_review"
            if session is None
            else (
                "ready_to_publish"
                if binding_is_current and not blockers and REQUIRED_CONFIRMATIONS <= set(confirms)
                else "action_required"
            )
        ),
        "questions": questions_payload,
        "blockers": sorted(
            blockers,
            key=lambda item: (
                item["code"],
                item.get("question_id") or "",
                item.get("id") or "",
            ),
        ),
        "confirmations": confirmations,
        "binding": binding_payload,
    }
    hash_payload = payload | {"version": payload["version"] | {"bundle_hash": None}}
    payload["version"]["bundle_hash"] = digest(hash_payload)
    return payload


def _issue_blocks_review_bundle(severity: str, status: str) -> bool:
    if severity == "blocking":
        return status not in {"resolved", "rejected"}
    return False


@router.get("/assignments/{assignment_id}/review-bundle")
def get_review_bundle(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return review_bundle(db, actor.id, assignment_id)


def state_payload(db: Session, assignment: Assignment, paper: PaperVersion) -> dict[str, Any]:
    class_ids = sorted(
        str(x)
        for x in db.scalars(
            select(AssignmentClass.class_id).where(AssignmentClass.assignment_id == assignment.id)
        )
    )
    participants = (
        [
            {
                "class_id": class_id,
                "student_id": student_id,
                "student_number": student_number,
                "student_name": student_name,
                "joined_at": joined_at,
            }
            for class_id, student_id, student_number, student_name, joined_at in db.execute(
                select(
                    ClassStudent.class_id,
                    Student.id,
                    Student.student_number,
                    Student.name,
                    ClassStudent.joined_at,
                )
                .join(Student, Student.id == ClassStudent.student_id)
                .where(
                    ClassStudent.class_id.in_([uuid.UUID(class_id) for class_id in class_ids]),
                    ClassStudent.status == MembershipStatus.active,
                    Student.owner_id == assignment.owner_id,
                    Student.status == ArchiveStatus.active,
                )
                .order_by(ClassStudent.class_id, Student.student_number, Student.id)
            )
        ]
        if assignment.delivery_mode == "joint_exam" and class_ids
        else []
    )
    rows = selected_versions(db, paper.id)
    files = list(
        db.scalars(
            select(AssignmentSourceFileAnalysis)
            .where(AssignmentSourceFileAnalysis.assignment_id == assignment.id)
            .order_by(AssignmentSourceFileAnalysis.stored_file_id)
        )
    )
    pages = list(
        db.scalars(
            select(PaperPage)
            .where(PaperPage.paper_version_id == paper.id)
            .order_by(PaperPage.page_number, PaperPage.id)
        )
    )
    page_analysis = list(
        db.scalars(
            select(AssignmentPageAnalysis)
            .where(AssignmentPageAnalysis.assignment_id == assignment.id)
            .order_by(AssignmentPageAnalysis.paper_page_id)
        )
    )
    organization = list(
        db.scalars(
            select(PaperPageOrganizationSuggestion)
            .where(PaperPageOrganizationSuggestion.paper_version_id == paper.id)
            .order_by(PaperPageOrganizationSuggestion.paper_page_id)
        )
    )
    candidates = list(
        db.scalars(
            select(AssignmentQuestionExtractionCandidate)
            .where(AssignmentQuestionExtractionCandidate.paper_version_id == paper.id)
            .order_by(AssignmentQuestionExtractionCandidate.id)
        )
    )
    return {
        "schema_version": REVIEW_SOURCE_SCHEMA_VERSION,
        "assignment": {
            "id": assignment.id,
            "title": assignment.title,
            "delivery_mode": assignment.delivery_mode,
            "subject": assignment.subject,
            "grade": assignment.grade,
            "description": assignment.description,
            "instructions": assignment.instructions,
            "due_at": assignment.due_at,
            "total_score": assignment.total_score,
            "class_ids": class_ids,
            "participants": participants,
        },
        "paper": {"id": paper.id, "version": paper.version},
        "files": [
            {
                "id": row.stored_file_id,
                "checksum": row.checksum,
                "stored_checksum": (
                    stored.checksum
                    if (stored := db.get(StoredFile, row.stored_file_id)) is not None
                    else None
                ),
                "role": row.teacher_confirmed_role,
                "answer_source": row.teacher_confirmed_answer_source,
            }
            for row in files
        ],
        "pages": [
            {
                "id": row.id,
                "number": row.page_number,
                "rotation": row.rotation,
            }
            for row in pages
        ],
        "page_analysis": [
            {
                "id": row.id,
                "missing": row.missing_page_suspected,
                "corrupted": row.corrupted,
                "variant": row.variant_label,
            }
            for row in page_analysis
        ],
        "page_organization": [
            {
                "id": row.id,
                "source_hash": row.source_snapshot_hash,
            }
            for row in organization
        ],
        "question_candidates": [
            {
                "id": row.id,
                "materialized": row.materialized_question_id,
                "source_hash": row.source_snapshot_hash,
            }
            for row in candidates
        ],
        "versions": [
            {
                "question_id": x["question"].id,
                "parent_question_id": x["question"].parent_question_id,
                "question_number": x["question"].question_number,
                "display_order": x["question"].display_order,
                "question_type": x["question"].question_type,
                "content_text": x["question"].content_text,
                "content_latex": x["question"].content_latex,
                "max_score": x["question"].max_score,
                "difficulty": x["question"].difficulty,
                "source": x["question"].source,
                "answer_id": x["answer"].id if x["answer"] else None,
                "answer_hash": x["answer"].content_hash if x["answer"] else None,
                "rubric_id": x["rubric"].id if x["rubric"] else None,
                "rubric_hash": x["rubric"].content_hash if x["rubric"] else None,
            }
            for x in rows
        ],
    }


def review_source_hash(
    db: Session,
    assignment: Assignment,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision,
    paper: PaperVersion,
) -> str:
    return digest(
        state_payload(db, assignment, paper)
        | {
            "job_source": job.source_snapshot_hash,
            "revision_source": revision.source_snapshot_hash,
        }
    )


def confirmation_value(db: Session, session: AssignmentReviewSession, kind: str) -> dict[str, Any]:
    assignment = db.get(Assignment, session.assignment_id)
    assert assignment is not None
    if kind == "classes":
        return {
            "class_ids": sorted(
                str(x)
                for x in db.scalars(
                    select(AssignmentClass.class_id).where(
                        AssignmentClass.assignment_id == assignment.id
                    )
                )
            )
        }
    if kind == "due_at":
        return {"due_at": assignment.due_at}
    if kind == "total_score":
        return {
            "total_score": assignment.total_score,
            "question_scores": [
                {"id": q.id, "score": q.max_score} for q in questions(db, session.paper_version_id)
            ],
        }
    if kind in {"file_roles", "answer_sources"}:
        files = list(
            db.scalars(
                select(AssignmentSourceFileAnalysis)
                .where(AssignmentSourceFileAnalysis.draft_revision_id == session.draft_revision_id)
                .order_by(AssignmentSourceFileAnalysis.stored_file_id)
            )
        )
        return {
            "files": [
                {
                    "id": f.stored_file_id,
                    "value": (
                        f.teacher_confirmed_role or f.suggested_role
                        if kind == "file_roles"
                        else f.teacher_confirmed_answer_source or f.suggested_answer_source
                    ),
                    "checksum": f.checksum,
                }
                for f in files
            ]
        }
    if kind == "paper_version":
        return {"paper_version_id": session.paper_version_id}
    rows = selected_versions(db, session.paper_version_id)
    if kind == "reference_answers":
        return {
            "versions": [
                {
                    "question_id": x["question"].id,
                    "id": x["answer"].id if x["answer"] else None,
                    "hash": x["answer"].content_hash if x["answer"] else None,
                    "source": x["answer"].source_type if x["answer"] else None,
                    "content": (
                        reference_answer_semantic_payload(
                            source_type=x["answer"].source_type,
                            source_region=x["answer"].source_region,
                            raw_content=x["answer"].raw_content,
                            normalized_content=x["answer"].normalized_content,
                            structured_content=x["answer"].structured_content,
                            alternative_answers=x["answer"].structured_content.get(
                                "alternative_answers", []
                            ),
                            provenance=x["answer"].provenance,
                        )
                        if x["answer"]
                        else None
                    ),
                }
                for x in rows
            ]
        }
    if kind == "structured_rubrics":
        return {
            "versions": [
                {
                    "question_id": x["question"].id,
                    "id": x["rubric"].id if x["rubric"] else None,
                    "hash": x["rubric"].content_hash if x["rubric"] else None,
                    "content": (_rubric_content_payload(db, x["rubric"]) if x["rubric"] else None),
                }
                for x in rows
            ]
        }
    binding = db.scalar(
        select(AssignmentRubricPublicationBinding)
        .where(
            AssignmentRubricPublicationBinding.review_session_id == session.id,
            AssignmentRubricPublicationBinding.status == "confirmed",
        )
        .order_by(AssignmentRubricPublicationBinding.binding_version.desc())
        .limit(1)
    )
    return {
        "binding_id": binding.id if binding else None,
        "source_binding_hash": binding.source_binding_hash if binding else None,
    }


def confirmation_fingerprint(
    db: Session, session: AssignmentReviewSession, kind: str
) -> tuple[dict[str, Any], str, str]:
    value = confirmation_value(db, session, kind)
    semantic_value = (
        {
            "versions": [
                {
                    "content": item.get("content"),
                    "source": item.get("source"),
                }
                for item in value["versions"]
            ]
        }
        if kind in {"reference_answers", "structured_rubrics"}
        else value
    )
    source_hash = (
        semantic_hash(semantic_value)
        if kind in {"reference_answers", "structured_rubrics"}
        else digest(value)
    )
    scope_hash = digest(
        {
            "assignment_id": session.assignment_id,
            "paper_version_id": session.paper_version_id,
            "question_ids": [q.id for q in questions(db, session.paper_version_id)],
        }
    )
    return value, source_hash, scope_hash


def binding_source_hash(db: Session, session: AssignmentReviewSession) -> str:
    _, answer_hash, _ = confirmation_fingerprint(db, session, "reference_answers")
    _, rubric_hash, _ = confirmation_fingerprint(db, session, "structured_rubrics")
    return semantic_hash(
        {
            "structured_rubrics": rubric_hash,
            "reference_answers": answer_hash,
            "projection_profile": LEGACY_PROJECTION_PROFILE,
        }
    )


def projection_loss_report(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = {
        "DEPENDENCY_NOT_LOSSLESS": "评分项依赖关系无法由旧版评分标准完整表达",
        "ALTERNATIVE_PATH_NOT_LOSSLESS": "备选评分路径无法由旧版评分标准完整表达",
        "VALIDATION_RULE_NOT_LOSSLESS": "结构化校验规则无法由旧版评分标准执行",
        "EXPECTED_EVIDENCE_NOT_LOSSLESS": "证据要求无法由旧版评分标准完整表达",
        "MANUAL_REVIEW_POLICY_NOT_LOSSLESS": "人工复核策略无法由旧版评分标准完整表达",
        "PARTIAL_CREDIT_POLICY_NOT_LOSSLESS": "部分分策略无法由旧版评分标准完整表达",
        "ERROR_CATEGORY_NOT_LOSSLESS": "错误分类无法由旧版评分标准完整表达",
        "CRITERION_METADATA_NOT_LOSSLESS": "扩展评分元数据无法由旧版评分标准完整表达",
        "DEDUCTION_RULE_NOT_LOSSLESS": "结构化扣分规则无法由旧版评分标准完整执行",
        "COMMON_ERROR_CODES_NOT_LOSSLESS": "多项错误代码无法由旧版评分标准完整表达",
        "FEEDBACK_TEMPLATE_NOT_LOSSLESS": "反馈模板无法由旧版评分标准完整表达",
    }
    report: list[dict[str, Any]] = []
    for row in rows:
        question = row["question"]
        for criterion in row["criteria"]:
            codes: list[str] = []
            if criterion.dependencies:
                codes.append("DEPENDENCY_NOT_LOSSLESS")
            if criterion.metadata_.get("alternative_group"):
                codes.append("ALTERNATIVE_PATH_NOT_LOSSLESS")
            if _validation_rule_requires_loss(criterion.validation_rule):
                codes.append("VALIDATION_RULE_NOT_LOSSLESS")
            if _meaningful_projection_value(criterion.expected_evidence):
                codes.append("EXPECTED_EVIDENCE_NOT_LOSSLESS")
            if _meaningful_projection_value(criterion.manual_review_policy):
                codes.append("MANUAL_REVIEW_POLICY_NOT_LOSSLESS")
            if _meaningful_projection_value(criterion.partial_credit_policy):
                codes.append("PARTIAL_CREDIT_POLICY_NOT_LOSSLESS")
            if criterion.error_category:
                codes.append("ERROR_CATEGORY_NOT_LOSSLESS")
            metadata = criterion.metadata_ or {}
            if _meaningful_projection_value(metadata.get("deduction_rule")):
                codes.append("DEDUCTION_RULE_NOT_LOSSLESS")
            if _meaningful_projection_value(metadata.get("common_error_codes")):
                codes.append("COMMON_ERROR_CODES_NOT_LOSSLESS")
            if _meaningful_projection_value(metadata.get("feedback_template")):
                codes.append("FEEDBACK_TEMPLATE_NOT_LOSSLESS")
            metadata_extra = set(metadata) - {
                "domain_requirements",
                "alternative_group",
                "deduction_rule",
                "common_error_codes",
                "feedback_template",
                "scoring_mode",
            }
            if any(_meaningful_projection_value(metadata[key]) for key in metadata_extra):
                codes.append("CRITERION_METADATA_NOT_LOSSLESS")
            report.extend(
                {
                    "code": code,
                    "question_id": str(question.id),
                    "question_number": question.question_number,
                    "criterion_key": criterion.stable_key,
                    "teacher_message": messages[code],
                    "technical": {
                        "projection_profile": LEGACY_PROJECTION_PROFILE,
                        "projection_version": LEGACY_PROJECTION_SCHEMA_VERSION,
                    },
                }
                for code in codes
            )
    return sorted(
        report,
        key=lambda item: (
            item["question_number"],
            item["criterion_key"],
            item["code"],
        ),
    )


def _meaningful_projection_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_meaningful_projection_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_meaningful_projection_value(item) for item in value)
    return True


def _validation_rule_requires_loss(value: Any) -> bool:
    if not _meaningful_projection_value(value):
        return False
    return bool(value != {"answer_type": "manual_only"})


def target_legacy_hash(db: Session, legacy_id: uuid.UUID) -> str:
    question_rubrics = list(
        db.scalars(
            select(QuestionRubric)
            .where(QuestionRubric.rubric_version_id == legacy_id)
            .order_by(QuestionRubric.question_id)
        )
    )
    return semantic_hash(
        [
            {
                "question_id": row.question_id,
                "standard_answer": row.standard_answer,
                "alternative_answers": row.alternative_answers,
                "scoring_notes": row.scoring_notes,
                "allow_step_score": row.allow_step_score,
                "unit_requirement": row.unit_requirement,
                "format_requirement": row.format_requirement,
                "precision_requirement": row.precision_requirement,
                "items": [
                    {
                        "display_order": item.display_order,
                        "title": item.title,
                        "description": item.description,
                        "points": item.points,
                        "item_type": item.item_type,
                        "required": item.required,
                        "deduction_rule": item.deduction_rule,
                    }
                    for item in db.scalars(
                        select(RubricItem)
                        .where(RubricItem.question_rubric_id == row.id)
                        .order_by(RubricItem.display_order, RubricItem.id)
                    )
                ],
            }
            for row in question_rubrics
        ]
    )


@dataclass(frozen=True)
class ProjectionValidation:
    binding: AssignmentRubricPublicationBinding | None
    confirmation: AssignmentExplicitConfirmation | None
    current: bool
    reason: str | None


def validate_current_projection_under_locks(
    db: Session,
    session: AssignmentReviewSession,
    *,
    binding_id: uuid.UUID | None = None,
    lock: bool,
    require_confirmed: bool = True,
) -> ProjectionValidation:
    """Validate one exact projection after acquiring the global publication lock order."""
    paper_query = (
        select(PaperVersion)
        .where(PaperVersion.id == session.paper_version_id)
        .execution_options(populate_existing=True)
    )
    question_query = (
        select(Question)
        .where(Question.paper_version_id == session.paper_version_id)
        .order_by(Question.display_order, Question.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        paper_query = paper_query.with_for_update()
        question_query = question_query.with_for_update()
    paper = db.scalar(paper_query)
    locked_questions = list(db.scalars(question_query))
    if paper is None:
        return ProjectionValidation(None, None, False, "PAPER_NOT_CURRENT")

    binding_query = (
        select(AssignmentRubricPublicationBinding)
        .where(
            AssignmentRubricPublicationBinding.review_session_id == session.id,
            AssignmentRubricPublicationBinding.invalidated_at.is_(None),
        )
        .order_by(
            AssignmentRubricPublicationBinding.binding_version.desc(),
            AssignmentRubricPublicationBinding.id,
        )
        .execution_options(populate_existing=True)
    )
    if binding_id is not None:
        binding_query = binding_query.where(AssignmentRubricPublicationBinding.id == binding_id)
    if lock:
        binding_query = binding_query.with_for_update()
    bindings = list(db.scalars(binding_query))

    question_ids = [
        question.id for question in locked_questions if question.status == QuestionStatus.active
    ]
    answer_query = (
        select(ReferenceAnswerVersion)
        .where(ReferenceAnswerVersion.question_id.in_(question_ids))
        .order_by(ReferenceAnswerVersion.question_id, ReferenceAnswerVersion.version)
        .execution_options(populate_existing=True)
    )
    rubric_query = (
        select(StructuredRubricVersion)
        .where(StructuredRubricVersion.question_id.in_(question_ids))
        .order_by(
            StructuredRubricVersion.question_id,
            StructuredRubricVersion.rubric_version,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        answer_query = answer_query.with_for_update()
        rubric_query = rubric_query.with_for_update()
    list(db.scalars(answer_query))
    rubric_rows = list(db.scalars(rubric_query))
    rubric_ids = [rubric.id for rubric in rubric_rows]
    criterion_query = (
        select(RubricCriterion)
        .where(RubricCriterion.rubric_version_id.in_(rubric_ids))
        .order_by(
            RubricCriterion.rubric_version_id,
            RubricCriterion.display_order,
            RubricCriterion.id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        criterion_query = criterion_query.with_for_update()
    list(db.scalars(criterion_query))

    current_source = binding_source_hash(db, session)
    binding = next(
        (
            row
            for row in bindings
            if row.source_binding_hash == current_source
            and (binding_id is None or row.id == binding_id)
        ),
        None,
    )
    if binding is None:
        return ProjectionValidation(None, None, False, "BINDING_NOT_CURRENT")

    legacy_query = (
        select(RubricVersion)
        .where(RubricVersion.id == binding.legacy_rubric_version_id)
        .execution_options(populate_existing=True)
    )
    question_rubric_query = (
        select(QuestionRubric)
        .where(QuestionRubric.rubric_version_id == binding.legacy_rubric_version_id)
        .order_by(QuestionRubric.question_id, QuestionRubric.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        legacy_query = legacy_query.with_for_update()
        question_rubric_query = question_rubric_query.with_for_update()
    legacy = db.scalar(legacy_query)
    legacy_question_rows = list(db.scalars(question_rubric_query))
    legacy_question_ids = [row.id for row in legacy_question_rows]
    item_query = (
        select(RubricItem)
        .where(RubricItem.question_rubric_id.in_(legacy_question_ids))
        .order_by(RubricItem.question_rubric_id, RubricItem.display_order, RubricItem.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        item_query = item_query.with_for_update()
    list(db.scalars(item_query))

    current_loss = projection_loss_report(selected_versions(db, session.paper_version_id))
    current_loss_hash = semantic_hash(current_loss)
    current_target = target_legacy_hash(db, binding.legacy_rubric_version_id)
    evidence_current = (
        legacy is not None
        and binding.source_semantic_hash == current_source
        and binding.target_legacy_hash == current_target
        and binding.projection_profile == LEGACY_PROJECTION_PROFILE
        and binding.projection_version == LEGACY_PROJECTION_SCHEMA_VERSION
        and binding.loss_report_hash == current_loss_hash
        and semantic_hash(binding.loss_report or []) == current_loss_hash
    )
    if not evidence_current:
        return ProjectionValidation(binding, None, False, "BINDING_PROJECTION_STALE")
    assert legacy is not None
    if require_confirmed and (
        binding.status != "confirmed" or legacy.status != VersionStatus.confirmed
    ):
        return ProjectionValidation(binding, None, False, "BINDING_NOT_CONFIRMED")

    confirmation_query = (
        select(AssignmentExplicitConfirmation)
        .where(
            AssignmentExplicitConfirmation.review_session_id == session.id,
            AssignmentExplicitConfirmation.confirmation_type == "legacy_binding",
            AssignmentExplicitConfirmation.invalidated_at.is_(None),
        )
        .order_by(
            AssignmentExplicitConfirmation.confirmation_version.desc(),
            AssignmentExplicitConfirmation.id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        confirmation_query = confirmation_query.with_for_update()
    confirmation = db.scalar(confirmation_query)
    if not require_confirmed:
        return ProjectionValidation(binding, confirmation, True, None)
    _, confirmation_hash, scope_hash = confirmation_fingerprint(db, session, "legacy_binding")
    confirmation_current = (
        confirmation is not None
        and confirmation.fingerprint_schema_version == CONFIRMATION_FINGERPRINT_VERSION
        and confirmation.confirmation_origin in {"origin", "system_auto"}
        and confirmation.paper_version_id == session.paper_version_id
        and confirmation.question_scope_hash == scope_hash
        and confirmation.source_hash == confirmation_hash
        and str(confirmation.confirmed_value.get("binding_id")) == str(binding.id)
        and confirmation.confirmed_value.get("source_binding_hash") == binding.source_binding_hash
    )
    if not confirmation_current:
        return ProjectionValidation(binding, confirmation, False, "LEGACY_CONFIRMATION_STALE")
    return ProjectionValidation(binding, confirmation, True, None)


def valid_confirmations(
    db: Session, session: AssignmentReviewSession
) -> dict[str, AssignmentExplicitConfirmation]:
    found: dict[str, AssignmentExplicitConfirmation] = {}
    rows = db.scalars(
        select(AssignmentExplicitConfirmation)
        .where(
            AssignmentExplicitConfirmation.review_session_id == session.id,
            AssignmentExplicitConfirmation.invalidated_at.is_(None),
        )
        .order_by(AssignmentExplicitConfirmation.confirmation_version.desc())
    ).all()
    for row in rows:
        _, source_hash, scope_hash = confirmation_fingerprint(db, session, row.confirmation_type)
        is_v2 = (
            row.fingerprint_schema_version == CONFIRMATION_FINGERPRINT_VERSION
            and row.paper_version_id == session.paper_version_id
            and row.question_scope_hash == scope_hash
            and row.source_hash == source_hash
            and (
                row.confirmation_type != "legacy_binding"
                or row.confirmation_origin in {"origin", "system_auto"}
            )
        )
        is_same_session_legacy = (
            row.fingerprint_schema_version is None
            and row.confirmation_type != "legacy_binding"
            and row.source_hash == digest(confirmation_value(db, session, row.confirmation_type))
        )
        if row.confirmation_type not in found and (is_v2 or is_same_session_legacy):
            found[row.confirmation_type] = row
    return found


def generated_issues(db: Session, session: AssignmentReviewSession) -> list[dict[str, Any]]:
    assignment = db.get(Assignment, session.assignment_id)
    paper = db.get(PaperVersion, session.paper_version_id)
    assert assignment and paper
    out: list[dict[str, Any]] = []

    def add(
        code: str,
        section: str,
        message: str,
        severity: str = "blocking",
        entity: str = "assignment",
        entity_id: Any | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "code": code,
            "section": section,
            "message": message,
            "entity": entity,
            "entity_id": str(entity_id or assignment.id),
            "evidence": evidence or {},
        }
        out.append(payload | {"severity": severity, "source_hash": digest(payload)})

    job, revision, current_paper = current_inputs(db, assignment)
    confirms = valid_confirmations(db, session)
    if (
        job.id != session.generation_job_id
        or revision.id != session.draft_revision_id
        or current_paper.id != session.paper_version_id
        or job.source_snapshot_hash != session.source_snapshot_hash
        or review_source_hash(db, assignment, job, revision, current_paper)
        != session.structured_binding_hash
    ):
        add("SOURCE_STALE", "validation", "生成、修订或试卷版本已变化")
    links = list(
        db.scalars(select(AssignmentClass).where(AssignmentClass.assignment_id == assignment.id))
    )
    if not links:
        add("NO_CLASSES", "classes", "必须选择并确认班级")
    elif any(
        (c := db.get(SchoolClass, x.class_id)) is None
        or c.owner_id != session.owner_id
        or c.status != ArchiveStatus.active
        for x in links
    ):
        add("CLASS_NOT_ACTIVE", "classes", "班级不存在、越权或已归档")
    qs = questions(db, paper.id)
    if not qs:
        add("QUESTIONS_REQUIRED", "questions", "当前试卷没有已物化题目")
    if any(q.max_score is None for q in qs):
        add("QUESTION_SCORE_REQUIRED", "total_score", "所有题目必须设置分值")
    elif assignment.total_score is None or sum(
        (Decimal(q.max_score) for q in qs if q.max_score is not None), Decimal()
    ) != Decimal(assignment.total_score):
        add("TOTAL_SCORE_MISMATCH", "total_score", "题目分值合计必须等于总分")
    for f in db.scalars(
        select(AssignmentSourceFileAnalysis).where(
            AssignmentSourceFileAnalysis.draft_revision_id == session.draft_revision_id
        )
    ):
        if f.analysis_status in {"failed", "corrupted"}:
            add(
                "FILE_CORRUPTED",
                "files",
                "文件分析失败或损坏",
                entity="file",
                entity_id=f.stored_file_id,
            )
        effective_role = f.teacher_confirmed_role or f.suggested_role
        role_needs_review = (
            effective_role in {None, "unknown"}
            or (
                not f.teacher_confirmed_role
                and (
                    float(f.role_confidence or 0) < 0.7
                    or "FILE_ROLE_CONFLICT_REVIEW_REQUIRED" in (f.warning_codes or [])
                )
            )
        )
        if role_needs_review:
            add(
                "FILE_ROLE_UNCONFIRMED",
                "files",
                "文件用途无法可靠判断或存在冲突",
                entity="file",
                entity_id=f.stored_file_id,
            )
    for page in db.scalars(
        select(AssignmentPageAnalysis).where(
            AssignmentPageAnalysis.draft_revision_id == session.draft_revision_id
        )
    ):
        if page.corrupted:
            add("PAGE_CORRUPTED", "pages", "页面损坏", entity="page", entity_id=page.paper_page_id)
        if page.missing_page_suspected:
            add(
                "MISSING_PAGE_SUSPECTED",
                "pages",
                "疑似缺页尚未解决",
                entity="page",
                entity_id=page.paper_page_id,
            )
        if page.mixed_document_suspected or page.variant_label not in {None, "", "unknown"}:
            add(
                "PAPER_VARIANT_REVIEW",
                "pages",
                "疑似混合文档或 A/B 卷",
                entity="page",
                entity_id=page.paper_page_id,
            )
        if page.low_quality:
            add(
                "PAGE_LOW_QUALITY",
                "pages",
                "页面质量较低，必须查看",
                "warning",
                "page",
                page.paper_page_id,
            )
    generated_rows = selected_versions(db, paper.id)
    complete_generated_content = bool(generated_rows) and all(
        row["answer"] is not None
        and row["answer"].status == "confirmed"
        and row["rubric"] is not None
        and row["rubric"].status == "confirmed"
        for row in generated_rows
    )
    if not complete_generated_content and db.scalar(
        select(PaperPageOrganizationSuggestion.id).where(
            PaperPageOrganizationSuggestion.draft_revision_id == session.draft_revision_id,
            PaperPageOrganizationSuggestion.status.in_(["suggested", "stale"]),
        )
    ):
        add("PAGE_ORGANIZATION_INCOMPLETE", "pages", "页面整理建议尚未完成")
    for candidate in db.scalars(
        select(AssignmentQuestionExtractionCandidate).where(
            AssignmentQuestionExtractionCandidate.draft_revision_id == session.draft_revision_id,
            AssignmentQuestionExtractionCandidate.status.not_in(STALE_CANDIDATE_STATUSES),
        )
    ):
        if candidate.materialized_question_id is None:
            add(
                "QUESTION_NOT_MATERIALIZED",
                "questions",
                "题目候选尚未物化",
                entity="question_candidate",
                entity_id=candidate.id,
            )
        elif candidate.warning_codes or candidate.manual_required:
            add(
                "QUESTION_EXTRACTION_REVIEW",
                "questions",
                "题目候选包含冲突或人工风险",
                "warning",
                "question_candidate",
                candidate.id,
                {"warning_codes": candidate.warning_codes},
            )
    for issue in db.scalars(
        select(GenerationIssue).where(
            GenerationIssue.job_id == session.generation_job_id,
            GenerationIssue.resolution_status.not_in(["resolved", "rejected"]),
        )
    ):
        generation_recovered = (
            issue.code in RECOVERED_GENERATION_CODES and complete_generated_content
        )
        add(
            issue.code,
            "validation",
            (
                "生成阶段曾报告异常，但题目、参考答案和评分标准现已完整并确认，无需再处理"
                if generation_recovered
                else issue.message
            ),
            (
                "info"
                if generation_recovered
                else ("blocking" if issue.severity == "blocking" else "warning")
            ),
            issue.entity_type or "generation",
            issue.entity_id or issue.id,
            issue.evidence,
        )
    for row in selected_versions(db, paper.id):
        q, answer, rubric, criteria = row["question"], row["answer"], row["rubric"], row["criteria"]
        if answer is None or answer.status != "confirmed":
            add(
                "REFERENCE_ANSWER_UNCONFIRMED",
                "answers",
                f"第 {q.question_number} 题缺少已确认答案",
                entity="question",
                entity_id=q.id,
            )
        elif (
            answer.source_type in {"ai_generated", "third_party"}
            and "reference_answers" not in confirms
        ):
            add(
                "ANSWER_SOURCE_REVIEW",
                "answers",
                f"第 {q.question_number} 题答案来自 {answer.source_type}",
                "warning",
                "question",
                q.id,
            )
        elif answer.source_type == "unknown":
            add(
                "ANSWER_SOURCE_UNKNOWN",
                "answers",
                f"第 {q.question_number} 题答案来源未知",
                entity="question",
                entity_id=q.id,
            )
        if rubric is None or rubric.status != "confirmed":
            add(
                "STRUCTURED_RUBRIC_UNCONFIRMED",
                "rubrics",
                f"第 {q.question_number} 题缺少已确认 Structured Rubric",
                entity="question",
                entity_id=q.id,
            )
        elif (
            q.max_score is None
            or Decimal(rubric.total_points) != Decimal(q.max_score)
            or sum((Decimal(c.max_points) for c in criteria), Decimal()) != Decimal(q.max_score)
        ):
            add(
                "RUBRIC_POINTS_MISMATCH",
                "rubrics",
                f"第 {q.question_number} 题 Rubric 分值不一致",
                entity="question",
                entity_id=q.id,
            )
        elif any(
            c.dependencies or c.metadata_.get("alternative_group") or c.validation_rule
            for c in criteria
        ):
            add(
                "LEGACY_CONVERSION_REVIEW",
                "rubrics",
                f"第 {q.question_number} 题包含 legacy 无法无损表达的规则",
                "warning",
                "question",
                q.id,
            )
    for kind in sorted(REQUIRED_CONFIRMATIONS - {"legacy_binding"} - set(confirms)):
        add(
            f"CONFIRM_{kind.upper()}_REQUIRED",
            {
                "reference_answers": "answers",
                "structured_rubrics": "rubrics",
                "paper_version": "pages",
            }.get(kind, kind),
            f"必须由教师明确确认 {kind}",
        )
    binding = db.scalar(
        select(AssignmentRubricPublicationBinding)
        .where(
            AssignmentRubricPublicationBinding.review_session_id == session.id,
            AssignmentRubricPublicationBinding.status == "confirmed",
        )
        .order_by(AssignmentRubricPublicationBinding.binding_version.desc())
        .limit(1)
    )
    if binding is None:
        add("LEGACY_BINDING_REQUIRED", "publication", "必须准备并确认 legacy Rubric 绑定")
    elif not validate_current_projection_under_locks(
        db, session, binding_id=binding.id, lock=False
    ).current:
        add("LEGACY_BINDING_STALE", "publication", "legacy Rubric 绑定已过期")
    if "legacy_binding" not in confirms:
        add("CONFIRM_LEGACY_BINDING_REQUIRED", "publication", "必须明确确认 legacy Rubric 绑定")
    return sorted(out, key=lambda x: (x["section"], x["severity"], x["code"], x["entity_id"]))


def refresh(db: Session, session: AssignmentReviewSession) -> None:
    if session.status == "published":
        raise ApiProblem(409, "REVIEW_PUBLISHED_IMMUTABLE", "已发布审查不可修改")
    issues = generated_issues(db, session)
    active_keys = {(x["code"], x["source_hash"]) for x in issues}
    existing = list(
        db.scalars(
            select(AssignmentReviewItem).where(AssignmentReviewItem.review_session_id == session.id)
        )
    )
    for item in existing:
        if (item.issue_code, item.source_hash) not in active_keys and item.status not in {
            "stale",
            "superseded",
        }:
            item.status = "stale"
    by_key = {(x.issue_code, x.source_hash): x for x in existing}
    for x in issues:
        if (x["code"], x["source_hash"]) not in by_key:
            db.add(
                AssignmentReviewItem(
                    review_session_id=session.id,
                    section=x["section"],
                    entity_type=x["entity"],
                    entity_id=x["entity_id"],
                    severity=x["severity"],
                    issue_code=x["code"],
                    title=x["code"].replace("_", " "),
                    message=x["message"],
                    evidence=x["evidence"],
                    source_hash=x["source_hash"],
                    status="open",
                    eligibility=x["severity"] == "info",
                )
            )
    db.flush()
    current = list(
        db.scalars(
            select(AssignmentReviewItem).where(
                AssignmentReviewItem.review_session_id == session.id,
                AssignmentReviewItem.status.not_in(["stale", "superseded", "resolved", "rejected"]),
            )
        )
    )
    session.blocking_count = sum(x.severity == "blocking" for x in current)
    session.warning_count = sum(
        x.severity == "warning" and x.status != "acknowledged" for x in current
    )
    session.info_count = sum(x.severity == "info" for x in current)
    session.risk_ledger_hash = digest(
        [
            {"code": x.issue_code, "source_hash": x.source_hash, "status": x.status}
            for x in sorted(current, key=lambda y: (y.issue_code, y.source_hash))
        ]
    )
    session.status = (
        "changes_required"
        if session.blocking_count
        else (
            "in_review"
            if session.warning_count
            else ("ready_to_publish" if session.legacy_rubric_version_id else "ready_for_binding")
        )
    )


def session_json(db: Session, s: AssignmentReviewSession) -> dict[str, Any]:
    confirmed_types = valid_confirmations(db, s)
    return {
        "id": str(s.id),
        "assignment_id": str(s.assignment_id),
        "generation_job_id": str(s.generation_job_id),
        "draft_revision_id": str(s.draft_revision_id),
        "generation": s.generation,
        "source_snapshot_hash": s.source_snapshot_hash,
        "review_version": s.review_version,
        "status": s.status,
        "risk_ledger_hash": s.risk_ledger_hash,
        "counts": {"blocking": s.blocking_count, "warning": s.warning_count, "info": s.info_count},
        "confirmations": sorted(confirmed_types),
        "paper_version_id": str(s.paper_version_id),
        "legacy_rubric_version_id": str(s.legacy_rubric_version_id)
        if s.legacy_rubric_version_id
        else None,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


class VersionedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_review_version: int = Field(gt=0)
    explicit_confirmation: Literal[True] = True


class Disposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_review_version: int = Field(gt=0)
    action: Literal["confirm", "modify", "reject", "acknowledge", "resolve_manual", "reopen"]
    note: str | None = Field(None, max_length=2000)


@router.post("/assignments/{assignment_id}/review-sessions", status_code=201)
def create_review_session(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    assignment = owned_assignment(db, actor.id, assignment_id, lock=True)
    job, revision, paper = current_inputs(db, assignment)
    old = db.scalar(
        select(AssignmentReviewSession)
        .where(
            AssignmentReviewSession.assignment_id == assignment.id,
            AssignmentReviewSession.status.in_(ACTIVE),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    source = review_source_hash(db, assignment, job, revision, paper)
    if (
        old
        and old.generation_job_id == job.id
        and old.draft_revision_id == revision.id
        and old.paper_version_id == paper.id
        and old.structured_binding_hash == source
    ):
        refresh(db, old)
        db.commit()
        return session_json(db, old)
    inherited = valid_confirmations(db, old) if old else {}
    if old:
        old.status, old.invalidated_at = "stale", now_utc()
    row = AssignmentReviewSession(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        generation=job.generation,
        source_snapshot_hash=job.source_snapshot_hash,
        review_version=1,
        status="draft",
        risk_ledger_hash=digest([]),
        expected_assignment_updated_at=assignment.updated_at,
        paper_version_id=paper.id,
        structured_binding_hash=source,
        created_by=actor.id,
    )
    db.add(row)
    db.flush()
    inherited_types: list[str] = []
    for kind, previous in inherited.items():
        if (
            kind == "legacy_binding"
            or previous.fingerprint_schema_version != CONFIRMATION_FINGERPRINT_VERSION
            or previous.paper_version_id != paper.id
        ):
            continue
        value, source_hash, scope_hash = confirmation_fingerprint(db, row, kind)
        if source_hash != previous.source_hash or scope_hash != previous.question_scope_hash:
            continue
        db.add(
            AssignmentExplicitConfirmation(
                review_session_id=row.id,
                assignment_id=assignment.id,
                confirmation_type=kind,
                confirmed_value=canonical(value),
                source_hash=source_hash,
                fingerprint_schema_version=CONFIRMATION_FINGERPRINT_VERSION,
                paper_version_id=row.paper_version_id,
                question_scope_hash=scope_hash,
                confirmation_origin="inherited",
                confirmation_version=1,
                confirmed_by=previous.confirmed_by,
                confirmed_at=previous.confirmed_at,
            )
        )
        inherited_types.append(kind)
    db.flush()
    refresh(db, row)
    inherited_dispositions: list[str] = []
    if old:
        prior_items = db.scalars(
            select(AssignmentReviewItem)
            .join(AssignmentReviewSession)
            .where(
                AssignmentReviewSession.assignment_id == assignment.id,
                AssignmentReviewSession.id != row.id,
                AssignmentReviewItem.status.in_(["acknowledged", "resolved"]),
            )
            .order_by(AssignmentReviewItem.reviewed_at.desc(), AssignmentReviewItem.id)
        ).all()
        prior_by_key: dict[tuple[str, str], AssignmentReviewItem] = {}
        for item in prior_items:
            prior_by_key.setdefault((item.issue_code, item.source_hash), item)
        for item in db.scalars(
            select(AssignmentReviewItem).where(
                AssignmentReviewItem.review_session_id == row.id,
                AssignmentReviewItem.status == "open",
            )
        ).all():
            prior_item = prior_by_key.get((item.issue_code, item.source_hash))
            if prior_item is None:
                continue
            item.status = prior_item.status
            item.teacher_action = prior_item.teacher_action
            item.teacher_note = prior_item.teacher_note
            item.reviewed_by = prior_item.reviewed_by
            item.reviewed_at = prior_item.reviewed_at
            inherited_dispositions.append(item.issue_code)
        if inherited_dispositions:
            db.flush()
            refresh(db, row)
    audit(
        db,
        actor.id,
        "assignment_review.create",
        "assignment_review_session",
        row.id,
        {
            "generation": job.generation,
            "inherited_confirmations": sorted(inherited_types),
            "inherited_dispositions": sorted(inherited_dispositions),
        },
    )
    db.commit()
    return session_json(db, row)


@router.get("/assignments/{assignment_id}/review-sessions")
def list_review_sessions(
    assignment_id: uuid.UUID, db: Db, actor: Actor, limit: int = Query(20, ge=1, le=100)
) -> dict[str, Any]:
    owned_assignment(db, actor.id, assignment_id)
    rows = db.scalars(
        select(AssignmentReviewSession)
        .where(
            AssignmentReviewSession.assignment_id == assignment_id,
            AssignmentReviewSession.owner_id == actor.id,
        )
        .order_by(AssignmentReviewSession.created_at.desc(), AssignmentReviewSession.id)
        .limit(limit)
    ).all()
    return {"items": [session_json(db, x) for x in rows]}


@router.get("/assignment-review-sessions/{session_id}")
def get_review_session(session_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return session_json(db, owned_session(db, actor.id, session_id))


@router.post("/assignment-review-sessions/{session_id}/refresh")
def refresh_review_session(
    session_id: uuid.UUID, data: VersionedAction, db: Db, actor: Actor
) -> dict[str, Any]:
    row = owned_session(db, actor.id, session_id, lock=True)
    if row.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    row.review_version += 1
    refresh(db, row)
    audit(db, actor.id, "assignment_review.refresh", "assignment_review_session", row.id)
    db.commit()
    return session_json(db, row)


@router.get("/assignment-review-sessions/{session_id}/items")
def list_review_items(
    session_id: uuid.UUID, db: Db, actor: Actor, limit: int = Query(100, ge=1, le=100)
) -> dict[str, Any]:
    row = owned_session(db, actor.id, session_id)
    items = db.scalars(
        select(AssignmentReviewItem)
        .where(AssignmentReviewItem.review_session_id == row.id)
        .order_by(
            AssignmentReviewItem.section,
            AssignmentReviewItem.severity,
            AssignmentReviewItem.issue_code,
            AssignmentReviewItem.id,
        )
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": str(x.id),
                "section": x.section,
                "entity_type": x.entity_type,
                "entity_id": x.entity_id,
                "severity": x.severity,
                "issue_code": x.issue_code,
                "title": x.title,
                "message": x.message,
                "evidence": x.evidence,
                "source_hash": x.source_hash,
                "status": x.status,
                "eligibility": x.eligibility,
                "teacher_action": x.teacher_action,
                "teacher_note": x.teacher_note,
                "reviewed_by": str(x.reviewed_by) if x.reviewed_by else None,
                "reviewed_at": x.reviewed_at,
            }
            for x in items
        ]
    }


@router.patch("/assignment-review-items/{item_id}/disposition")
def disposition(item_id: uuid.UUID, data: Disposition, db: Db, actor: Actor) -> dict[str, Any]:
    item_hint = db.scalar(
        select(AssignmentReviewItem)
        .join(AssignmentReviewSession)
        .where(AssignmentReviewItem.id == item_id, AssignmentReviewSession.owner_id == actor.id)
    )
    if item_hint is None:
        raise ApiProblem(404, "REVIEW_ITEM_NOT_FOUND", "审查项不存在")
    session = owned_session(db, actor.id, item_hint.review_session_id, lock=True)
    item = db.scalar(
        select(AssignmentReviewItem)
        .where(
            AssignmentReviewItem.id == item_id,
            AssignmentReviewItem.review_session_id == session.id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "REVIEW_ITEM_NOT_FOUND", "审查项不存在")
    if session.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    if item.severity == "blocking" and data.action in {"acknowledge", "confirm"}:
        raise ApiProblem(422, "BLOCKING_ITEM_REQUIRES_RESOLUTION", "红色问题必须真正解决")
    item.teacher_action, item.teacher_note, item.reviewed_by, item.reviewed_at = (
        data.action,
        data.note,
        actor.id,
        now_utc(),
    )
    item.status = {
        "acknowledge": "acknowledged",
        "resolve_manual": "resolved",
        "reject": "rejected",
        "reopen": "open",
        "confirm": "resolved",
        "modify": "resolved",
    }[data.action]
    session.review_version += 1
    refresh(db, session)
    audit(
        db,
        actor.id,
        "assignment_review_item.disposition",
        "assignment_review_item",
        item.id,
        {"action": data.action},
    )
    db.commit()
    return {"id": str(item.id), "status": item.status, "review_version": session.review_version}


def automatic_confirmation_blocker(
    db: Session,
    session: AssignmentReviewSession,
    confirmation_type: str,
    value: dict[str, Any],
) -> str | None:
    if confirmation_type == "classes" and not value["class_ids"]:
        return "CLASSES_REQUIRED"
    if confirmation_type == "total_score":
        if value["total_score"] is None or any(
            item["score"] is None for item in value["question_scores"]
        ):
            return "TOTAL_SCORE_INCOMPLETE"
        if sum(
            (Decimal(item["score"]) for item in value["question_scores"]),
            Decimal(),
        ) != Decimal(value["total_score"]):
            return "TOTAL_SCORE_MISMATCH"
    if confirmation_type == "file_roles" and any(
        item["value"] in {None, "unknown"} for item in value["files"]
    ):
        return "FILE_ROLE_UNCONFIRMED"
    if confirmation_type == "answer_sources" and any(
        item["value"] in {None, "unknown"} for item in value["files"]
    ):
        return "ANSWER_SOURCE_UNCONFIRMED"
    if confirmation_type in {"reference_answers", "structured_rubrics"} and any(
        item["id"] is None for item in value["versions"]
    ):
        return "VERSION_CONFIRMATION_INCOMPLETE"
    if confirmation_type == "paper_version":
        risky_page = db.scalar(
            select(AssignmentPageAnalysis.id).where(
                AssignmentPageAnalysis.draft_revision_id == session.draft_revision_id,
                (
                    AssignmentPageAnalysis.corrupted.is_(True)
                    | AssignmentPageAnalysis.missing_page_suspected.is_(True)
                    | AssignmentPageAnalysis.low_quality.is_(True)
                    | AssignmentPageAnalysis.mixed_document_suspected.is_(True)
                    | AssignmentPageAnalysis.variant_label.not_in(["", "unknown"])
                ),
            )
        )
        pending_organization = db.scalar(
            select(PaperPageOrganizationSuggestion.id).where(
                PaperPageOrganizationSuggestion.draft_revision_id == session.draft_revision_id,
                PaperPageOrganizationSuggestion.status.in_(["suggested", "stale"]),
            )
        )
        if risky_page or pending_organization:
            return "PAPER_REVIEW_REQUIRED"
    return None


def add_confirmation(
    db: Session,
    session: AssignmentReviewSession,
    actor: Actor,
    confirmation_type: str,
    *,
    origin: str,
) -> AssignmentExplicitConfirmation:
    version = (
        db.scalar(
            select(func.max(AssignmentExplicitConfirmation.confirmation_version)).where(
                AssignmentExplicitConfirmation.review_session_id == session.id,
                AssignmentExplicitConfirmation.confirmation_type == confirmation_type,
            )
        )
        or 0
    ) + 1
    value, source_hash, scope_hash = confirmation_fingerprint(db, session, confirmation_type)
    row = AssignmentExplicitConfirmation(
        review_session_id=session.id,
        assignment_id=session.assignment_id,
        confirmation_type=confirmation_type,
        confirmed_value=canonical(value),
        source_hash=source_hash,
        fingerprint_schema_version=CONFIRMATION_FINGERPRINT_VERSION,
        paper_version_id=session.paper_version_id,
        question_scope_hash=scope_hash,
        confirmation_origin=origin,
        confirmation_version=version,
        confirmed_by=actor.id,
    )
    db.add(row)
    return row


@router.post("/assignment-review-sessions/{session_id}/confirm/{confirmation_type}")
def confirm(
    session_id: uuid.UUID, confirmation_type: str, data: VersionedAction, db: Db, actor: Actor
) -> dict[str, Any]:
    confirmation_type = confirmation_type.replace("-", "_")
    if confirmation_type not in CONFIRMATION_TYPES - {"legacy_binding"}:
        raise ApiProblem(404, "CONFIRMATION_TYPE_NOT_FOUND", "确认类型不存在")
    session = owned_session(db, actor.id, session_id, lock=True)
    if session.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    value = confirmation_value(db, session, confirmation_type)
    if confirmation_type == "classes" and not value["class_ids"]:
        raise ApiProblem(422, "CLASSES_REQUIRED", "班级不能为空")
    if confirmation_type == "total_score" and (
        value["total_score"] is None or any(x["score"] is None for x in value["question_scores"])
    ):
        raise ApiProblem(422, "TOTAL_SCORE_INCOMPLETE", "总分或题目分值不完整")
    if confirmation_type in {"reference_answers", "structured_rubrics"} and any(
        x["id"] is None for x in value["versions"]
    ):
        raise ApiProblem(422, "VERSION_CONFIRMATION_INCOMPLETE", "每题必须有当前已确认版本")
    if confirmation_type == "answer_sources" and any(
        x["value"] in {None, "unknown"} for x in value["files"]
    ):
        raise ApiProblem(422, "ANSWER_SOURCE_UNKNOWN", "未知答案来源不能确认")
    row = add_confirmation(db, session, actor, confirmation_type, origin="origin")
    if confirmation_type == "paper_version":
        for review_item in db.scalars(
            select(AssignmentReviewItem)
            .where(
                AssignmentReviewItem.review_session_id == session.id,
                AssignmentReviewItem.issue_code == "PAPER_VARIANT_REVIEW",
                AssignmentReviewItem.status == "open",
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all():
            review_item.status = "resolved"
            review_item.teacher_action = "confirm"
            review_item.teacher_note = "教师已逐页核对并确认当前试卷版本"
            review_item.reviewed_by = actor.id
            review_item.reviewed_at = now_utc()
    session.review_version += 1
    db.flush()
    refresh(db, session)
    audit(
        db,
        actor.id,
        f"assignment_review.confirm.{confirmation_type}",
        "assignment_explicit_confirmation",
        row.id,
    )
    db.commit()
    return {
        "id": str(row.id),
        "confirmation_type": confirmation_type,
        "confirmation_version": row.confirmation_version,
        "review_version": session.review_version,
    }


@router.post("/assignment-review-sessions/{session_id}/auto-confirm")
def auto_confirm_review_inputs(
    session_id: uuid.UUID, data: VersionedAction, db: Db, actor: Actor
) -> dict[str, Any]:
    session = owned_session(db, actor.id, session_id, lock=True)
    if session.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    existing = valid_confirmations(db, session)
    confirmed: list[str] = []
    skipped: dict[str, str] = {}
    rows: list[AssignmentExplicitConfirmation] = []
    for confirmation_type in sorted(AUTOMATIC_CONFIRMATION_TYPES - set(existing)):
        value = confirmation_value(db, session, confirmation_type)
        blocker = automatic_confirmation_blocker(db, session, confirmation_type, value)
        if blocker:
            skipped[confirmation_type] = blocker
            continue
        row = add_confirmation(
            db,
            session,
            actor,
            confirmation_type,
            origin="system_auto",
        )
        rows.append(row)
        confirmed.append(confirmation_type)
    if rows:
        session.review_version += 1
        db.flush()
        for row in rows:
            audit(
                db,
                actor.id,
                f"assignment_review.auto_confirm.{row.confirmation_type}",
                "assignment_explicit_confirmation",
                row.id,
            )
    refresh(db, session)
    audit(
        db,
        actor.id,
        "assignment_review.auto_confirm",
        "assignment_review_session",
        session.id,
        {"confirmed": confirmed, "skipped": skipped},
    )
    db.commit()
    return {
        "confirmed": confirmed,
        "skipped": skipped,
        "review_version": session.review_version,
    }


@router.post("/assignment-review-sessions/{session_id}/rubric-binding")
def create_binding(
    session_id: uuid.UUID, data: VersionedAction, db: Db, actor: Actor
) -> dict[str, Any]:
    session = owned_session(db, actor.id, session_id, lock=True)
    if session.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    confirms = valid_confirmations(db, session)
    required = REQUIRED_CONFIRMATIONS - {"legacy_binding"}
    if required - set(confirms):
        raise ApiProblem(
            422,
            "CONFIRMATIONS_INCOMPLETE",
            "绑定前必须完成全部显式确认",
            {"missing": sorted(required - set(confirms))},
        )
    source_hash = binding_source_hash(db, session)
    rows = selected_versions(db, session.paper_version_id)
    loss_report = projection_loss_report(rows)
    loss_hash = semantic_hash(loss_report)
    old = db.scalar(
        select(AssignmentRubricPublicationBinding).where(
            AssignmentRubricPublicationBinding.review_session_id == session.id,
            AssignmentRubricPublicationBinding.source_binding_hash == source_hash,
        )
    )
    if old:
        validation = validate_current_projection_under_locks(
            db,
            session,
            binding_id=old.id,
            lock=True,
            require_confirmed=False,
        )
        if validation.current and old.status in {"draft", "validated", "confirmed"}:
            return binding_json(old)
        old.status = "stale"
        old.invalidated_at = now_utc()
        old.source_binding_hash = digest(
            {"stale_binding_id": old.id, "previous_source_hash": source_hash}
        )
        db.flush()
    if any(
        row["answer"] is None
        or row["answer"].status != "confirmed"
        or row["rubric"] is None
        or row["rubric"].status != "confirmed"
        for row in rows
    ):
        raise ApiProblem(
            422,
            "BINDING_SOURCE_UNCONFIRMED",
            "发布绑定只能使用已确认的答案和 Structured Rubric",
        )
    warnings: list[str] = [item["code"] for item in loss_report]
    legacy_version = (
        db.scalar(
            select(func.max(RubricVersion.version)).where(
                RubricVersion.assignment_id == session.assignment_id
            )
        )
        or 0
    ) + 1
    legacy = RubricVersion(
        assignment_id=session.assignment_id,
        version=legacy_version,
        status=VersionStatus.draft,
        created_by=actor.id,
        notes="由教师触发的 Structured Rubric 确定性发布投影",
    )
    db.add(legacy)
    db.flush()
    mapping: list[dict[str, Any]] = []
    for row in rows:
        q, answer, rubric, criteria = row["question"], row["answer"], row["rubric"], row["criteria"]
        if answer is None or rubric is None or not answer.normalized_content.strip():
            raise ApiProblem(422, "BINDING_SOURCE_INCOMPLETE", "答案或 Structured Rubric 不完整")
        domain = criteria[0].metadata_.get("domain_requirements", {}) if criteria else {}
        qr = QuestionRubric(
            rubric_version_id=legacy.id,
            question_id=q.id,
            standard_answer=answer.normalized_content or answer.raw_content,
            alternative_answers=list(answer.structured_content.get("alternative_answers", [])),
            scoring_notes=rubric.title,
            allow_step_score=any(bool(c.partial_credit_policy) for c in criteria),
            unit_requirement=domain.get("unit"),
            format_requirement=domain.get("format"),
            precision_requirement=domain.get("precision"),
        )
        db.add(qr)
        db.flush()
        criterion_map = []
        question_warnings: list[str] = []
        for c in criteria:
            conversion_warnings = [
                item["code"]
                for item in loss_report
                if item["question_id"] == str(q.id) and item["criterion_key"] == c.stable_key
            ]
            question_warnings.extend(conversion_warnings)
            fallback_payload = {
                "projection_mode": "manual_review" if conversion_warnings else "legacy_native",
                "structured_criterion_id": str(c.id),
                "dependencies": list(c.dependencies),
                "validation_rule": c.validation_rule,
                "alternative_group": c.metadata_.get("alternative_group"),
                "manual_review_policy": c.manual_review_policy,
                "conversion_warnings": conversion_warnings,
            }
            item = RubricItem(
                question_rubric_id=qr.id,
                display_order=c.display_order,
                title=c.title,
                description=c.description,
                points=c.max_points,
                item_type=c.criterion_type,
                required=c.required,
                deduction_rule=json.dumps(fallback_payload, ensure_ascii=False, sort_keys=True),
            )
            db.add(item)
            db.flush()
            criterion_map.append(
                {
                    "criterion_id": str(c.id),
                    "rubric_item_id": str(item.id),
                    "points": str(c.max_points),
                    "warnings": conversion_warnings,
                    "manual_review_required": bool(conversion_warnings),
                }
            )
        if question_warnings:
            qr.allow_step_score = True
            qr.scoring_notes = (
                f"{rubric.title}；高级结构化规则保留在 Structured Rubric，"
                "旧版评分项须由教师人工核查"
            )
        if q.max_score is None or sum(
            (Decimal(c.max_points) for c in criteria), Decimal()
        ) != Decimal(q.max_score):
            raise ApiProblem(422, "BINDING_POINTS_MISMATCH", "RubricItem 分值必须等于题目分值")
        mapping.append(
            {
                "question_id": str(q.id),
                "reference_answer_version_id": str(answer.id),
                "answer_source": answer.source_type,
                "structured_rubric_version_id": str(rubric.id),
                "structured_rubric_hash": rubric.content_hash,
                "legacy_question_rubric_id": str(qr.id),
                "criteria": criterion_map,
                "points": str(q.max_score),
                "conversion_warnings": sorted(set(question_warnings)),
                "manual_review_required": bool(question_warnings),
            }
        )
    binding_version = (
        db.scalar(
            select(func.max(AssignmentRubricPublicationBinding.binding_version)).where(
                AssignmentRubricPublicationBinding.assignment_id == session.assignment_id
            )
        )
        or 0
    ) + 1
    known_compatibility_fallback = all(
        item["code"] in MANUAL_FALLBACK_WARNINGS for item in loss_report
    )
    binding = AssignmentRubricPublicationBinding(
        owner_id=actor.id,
        assignment_id=session.assignment_id,
        review_session_id=session.id,
        paper_version_id=session.paper_version_id,
        legacy_rubric_version_id=legacy.id,
        binding_version=binding_version,
        status="validated" if known_compatibility_fallback else "draft",
        source_binding_hash=source_hash,
        source_semantic_hash=source_hash,
        target_legacy_hash=target_legacy_hash(db, legacy.id),
        projection_profile=LEGACY_PROJECTION_PROFILE,
        projection_version=LEGACY_PROJECTION_SCHEMA_VERSION,
        loss_report=loss_report,
        loss_report_hash=loss_hash,
        mapping=mapping,
        created_by=actor.id,
    )
    db.add(binding)
    session.legacy_rubric_version_id = legacy.id
    db.flush()
    if known_compatibility_fallback:
        binding.status = "confirmed"
        binding.confirmed_by = actor.id
        binding.confirmed_at = now_utc()
        legacy.status = VersionStatus.confirmed
        legacy.confirmed_at = binding.confirmed_at
        db.flush()
        value, legacy_hash, scope_hash = confirmation_fingerprint(db, session, "legacy_binding")
        confirmation_version = (
            db.scalar(
                select(func.max(AssignmentExplicitConfirmation.confirmation_version)).where(
                    AssignmentExplicitConfirmation.review_session_id == session.id,
                    AssignmentExplicitConfirmation.confirmation_type == "legacy_binding",
                )
            )
            or 0
        ) + 1
        db.add(
            AssignmentExplicitConfirmation(
                review_session_id=session.id,
                assignment_id=session.assignment_id,
                confirmation_type="legacy_binding",
                confirmed_value=canonical(value),
                source_hash=legacy_hash,
                fingerprint_schema_version=CONFIRMATION_FINGERPRINT_VERSION,
                paper_version_id=session.paper_version_id,
                question_scope_hash=scope_hash,
                confirmation_origin="system_auto",
                confirmation_version=confirmation_version,
                confirmed_by=actor.id,
            )
        )
        db.flush()
    session.review_version += 1
    refresh(db, session)
    audit(
        db,
        actor.id,
        "assignment_rubric_binding.create",
        "assignment_rubric_publication_binding",
        binding.id,
        {"warnings": sorted(set(warnings))},
    )
    db.commit()
    return binding_json(binding)


def binding_json(x: AssignmentRubricPublicationBinding) -> dict[str, Any]:
    warnings = sorted({w for row in x.mapping for w in row.get("conversion_warnings", [])})
    return {
        "id": str(x.id),
        "assignment_id": str(x.assignment_id),
        "review_session_id": str(x.review_session_id),
        "paper_version_id": str(x.paper_version_id),
        "legacy_rubric_version_id": str(x.legacy_rubric_version_id),
        "binding_version": x.binding_version,
        "status": x.status,
        "source_binding_hash": x.source_binding_hash,
        "source_semantic_hash": x.source_semantic_hash,
        "target_legacy_hash": x.target_legacy_hash,
        "projection_profile": x.projection_profile,
        "projection_version": x.projection_version,
        "loss_report": x.loss_report or [],
        "loss_report_hash": x.loss_report_hash,
        "mapping": x.mapping,
        "conversion_warnings": warnings,
        "manual_review_required": bool(x.loss_report),
    }


@router.get("/assignment-review-sessions/{session_id}/rubric-binding")
def get_binding(session_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    session = owned_session(db, actor.id, session_id)
    row = db.scalar(
        select(AssignmentRubricPublicationBinding)
        .where(AssignmentRubricPublicationBinding.review_session_id == session.id)
        .order_by(AssignmentRubricPublicationBinding.binding_version.desc())
        .limit(1)
    )
    if row is None:
        raise ApiProblem(404, "BINDING_NOT_FOUND", "绑定不存在")
    return binding_json(row)


@router.post("/assignment-rubric-publication-bindings/{binding_id}/confirm")
def confirm_binding(
    binding_id: uuid.UUID, data: VersionedAction, db: Db, actor: Actor
) -> dict[str, Any]:
    binding_hint = db.scalar(
        select(AssignmentRubricPublicationBinding).where(
            AssignmentRubricPublicationBinding.id == binding_id,
            AssignmentRubricPublicationBinding.owner_id == actor.id,
        )
    )
    if binding_hint is None:
        raise ApiProblem(404, "BINDING_NOT_FOUND", "绑定不存在")
    session = owned_session(db, actor.id, binding_hint.review_session_id, lock=True)
    if session.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    validation = validate_current_projection_under_locks(
        db,
        session,
        binding_id=binding_id,
        lock=True,
        require_confirmed=False,
    )
    if not validation.current or validation.binding is None:
        raise ApiProblem(409, "BINDING_PROJECTION_STALE", "发布投影已漂移，请重新审查")
    binding = validation.binding
    if binding.status not in {"validated", "draft"}:
        raise ApiProblem(409, "BINDING_NOT_CONFIRMABLE", "绑定不可确认")
    warnings = sorted({item["code"] for item in binding.loss_report or []})
    unsupported_warnings = sorted(set(warnings) - MANUAL_FALLBACK_WARNINGS)
    if unsupported_warnings:
        raise ApiProblem(
            422,
            "BINDING_NOT_LOSSLESS",
            "存在尚未支持降级处理的规则，需教师先修改 Structured Rubric",
            {"warnings": unsupported_warnings},
        )
    binding.status, binding.confirmed_by, binding.confirmed_at = "confirmed", actor.id, now_utc()
    legacy = db.get(RubricVersion, binding.legacy_rubric_version_id)
    assert legacy
    legacy.status, legacy.confirmed_at = VersionStatus.confirmed, now_utc()
    value = confirmation_value(db, session, "legacy_binding") | {
        "binding_id": binding.id,
        "source_binding_hash": binding.source_binding_hash,
    }
    version = (
        db.scalar(
            select(func.max(AssignmentExplicitConfirmation.confirmation_version)).where(
                AssignmentExplicitConfirmation.review_session_id == session.id,
                AssignmentExplicitConfirmation.confirmation_type == "legacy_binding",
            )
        )
        or 0
    ) + 1
    db.add(
        AssignmentExplicitConfirmation(
            review_session_id=session.id,
            assignment_id=session.assignment_id,
            confirmation_type="legacy_binding",
            confirmed_value=canonical(value),
            source_hash=digest(value),
            fingerprint_schema_version=CONFIRMATION_FINGERPRINT_VERSION,
            paper_version_id=session.paper_version_id,
            question_scope_hash=confirmation_fingerprint(db, session, "legacy_binding")[2],
            confirmation_origin="origin",
            confirmation_version=version,
            confirmed_by=actor.id,
        )
    )
    for review_item in db.scalars(
        select(AssignmentReviewItem)
        .where(
            AssignmentReviewItem.review_session_id == session.id,
            AssignmentReviewItem.issue_code == "LEGACY_CONVERSION_REVIEW",
            AssignmentReviewItem.status == "open",
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    ).all():
        review_item.status = "acknowledged"
        review_item.teacher_action = "acknowledge"
        review_item.teacher_note = (
            "教师已确认保留 Structured Rubric，并将旧版无法表达的规则转为人工核查"
        )
        review_item.reviewed_by = actor.id
        review_item.reviewed_at = now_utc()
    session.review_version += 1
    db.flush()
    refresh(db, session)
    audit(
        db,
        actor.id,
        "assignment_rubric_binding.confirm",
        "assignment_rubric_publication_binding",
        binding.id,
        {
            "conversion_warnings": warnings,
            "degradation_mode": "manual_review" if warnings else "lossless",
        },
    )
    db.commit()
    return binding_json(binding) | {"review_version": session.review_version}


@router.post("/assignment-review-sessions/{session_id}/prepare-publication")
def prepare_publication(
    session_id: uuid.UUID, data: VersionedAction, db: Db, actor: Actor
) -> dict[str, Any]:
    session_hint = owned_session(db, actor.id, session_id)
    assignment = owned_assignment(db, actor.id, session_hint.assignment_id, lock=True)
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_NOT_DRAFT", "只有草稿可准备发布")
    session = owned_session(db, actor.id, session_id, lock=True)
    if session.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    job, revision, paper_now = current_inputs(db, assignment)
    if (
        job.id != session.generation_job_id
        or revision.id != session.draft_revision_id
        or paper_now.id != session.paper_version_id
        or job.source_snapshot_hash != session.source_snapshot_hash
        or review_source_hash(db, assignment, job, revision, paper_now)
        != session.structured_binding_hash
    ):
        raise ApiProblem(409, "REVIEW_SOURCE_STALE", "生成、修订或试卷来源已变化")
    projection = validate_current_projection_under_locks(
        db, session, lock=True, require_confirmed=True
    )
    if not projection.current or projection.binding is None:
        raise ApiProblem(
            409,
            "BINDING_PROJECTION_STALE",
            "发布投影或 legacy 确认已漂移",
            {"reason": projection.reason},
        )
    refresh(db, session)
    if session.blocking_count:
        raise ApiProblem(
            422,
            "REVIEW_NOT_READY",
            "仍有影响发布的问题需要处理",
            {"blocking": session.blocking_count, "warning": session.warning_count},
        )
    confirms = valid_confirmations(db, session)
    if REQUIRED_CONFIRMATIONS - set(confirms):
        raise ApiProblem(
            422,
            "CONFIRMATIONS_INCOMPLETE",
            "显式确认不完整",
            {"missing": sorted(REQUIRED_CONFIRMATIONS - set(confirms))},
        )
    binding = projection.binding
    if binding is None or assignment.total_score is None:
        raise ApiProblem(422, "PUBLICATION_INPUT_INCOMPLETE", "发布输入不完整")
    paper = db.get(PaperVersion, session.paper_version_id)
    legacy = db.get(RubricVersion, binding.legacy_rubric_version_id)
    assert paper and legacy
    payload = state_payload(db, assignment, paper)
    state_hash = digest(payload)
    class_ids = payload["assignment"]["class_ids"]
    ready_payload = {
        "session_id": session.id,
        "review_version": session.review_version,
        "state_hash": state_hash,
        "risk_hash": session.risk_ledger_hash,
        "binding_id": binding.id,
        "source_hash": session.source_snapshot_hash,
    }
    ready_hash = digest(ready_payload)
    old = db.scalar(
        select(AssignmentPublishReadinessSnapshot)
        .where(
            AssignmentPublishReadinessSnapshot.review_session_id == session.id,
            AssignmentPublishReadinessSnapshot.readiness_hash == ready_hash,
        )
        .with_for_update()
    )
    if old:
        now = now_utc()
        expiry = (
            old.expires_at.replace(tzinfo=UTC) if old.expires_at.tzinfo is None else old.expires_at
        )
        if old.status == "ready" and expiry > now:
            return readiness_json(old)
        if old.consumed_at is not None or old.status == "consumed":
            raise ApiProblem(409, "READINESS_ALREADY_CONSUMED", "发布准备已消费")
        old.status = "ready"
        old.expires_at = now + timedelta(minutes=15)
        old.invalidated_at = None
        audit(
            db,
            actor.id,
            "assignment_publication.renew",
            "assignment_publish_readiness",
            old.id,
        )
        db.commit()
        return readiness_json(old)
    snapshot = AssignmentPublishReadinessSnapshot(
        owner_id=actor.id,
        assignment_id=assignment.id,
        review_session_id=session.id,
        paper_version_id=paper.id,
        legacy_rubric_version_id=legacy.id,
        binding_id=binding.id,
        generation=session.generation,
        draft_revision_id=session.draft_revision_id,
        risk_ledger_hash=session.risk_ledger_hash,
        source_snapshot_hash=session.source_snapshot_hash,
        assignment_state_hash=state_hash,
        class_ids=class_ids,
        due_at=assignment.due_at,
        total_score=assignment.total_score,
        issue_counts={"blocking": 0, "warning": 0, "info": session.info_count},
        readiness_hash=ready_hash,
        status="ready",
        expires_at=now_utc() + timedelta(minutes=15),
        created_by=actor.id,
    )
    db.add(snapshot)
    audit(
        db, actor.id, "assignment_publication.prepare", "assignment_publish_readiness", snapshot.id
    )
    db.commit()
    return readiness_json(snapshot)


def readiness_json(x: AssignmentPublishReadinessSnapshot) -> dict[str, Any]:
    return {
        "id": str(x.id),
        "assignment_id": str(x.assignment_id),
        "review_session_id": str(x.review_session_id),
        "readiness_hash": x.readiness_hash,
        "status": x.status,
        "expires_at": x.expires_at,
        "consumed_at": x.consumed_at,
        "paper_version_id": str(x.paper_version_id),
        "legacy_rubric_version_id": str(x.legacy_rubric_version_id),
        "binding_id": str(x.binding_id),
        "class_ids": x.class_ids,
        "due_at": x.due_at,
        "total_score": str(x.total_score),
    }


@router.get("/assignment-publish-readiness/{snapshot_id}")
def get_readiness(snapshot_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    row = db.scalar(
        select(AssignmentPublishReadinessSnapshot).where(
            AssignmentPublishReadinessSnapshot.id == snapshot_id,
            AssignmentPublishReadinessSnapshot.owner_id == actor.id,
        )
    )
    if row is None:
        raise ApiProblem(404, "READINESS_NOT_FOUND", "发布准备快照不存在")
    return readiness_json(row)


class PublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    readiness_snapshot_id: uuid.UUID
    readiness_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_assignment_updated_at: datetime
    explicit_confirmation: Literal[True]


def teacher_publish(
    db: Session, actor_id: uuid.UUID, assignment_id: uuid.UUID, data: PublishInput
) -> Assignment:
    assignment = owned_assignment(db, actor_id, assignment_id, lock=True)
    snapshot = db.scalar(
        select(AssignmentPublishReadinessSnapshot)
        .where(
            AssignmentPublishReadinessSnapshot.id == data.readiness_snapshot_id,
            AssignmentPublishReadinessSnapshot.owner_id == actor_id,
            AssignmentPublishReadinessSnapshot.assignment_id == assignment.id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if snapshot is None:
        raise ApiProblem(404, "READINESS_NOT_FOUND", "发布准备快照不存在")
    if assignment.status == AssignmentStatus.published and snapshot.status == "consumed":
        return assignment
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_NOT_DRAFT", "只有草稿可发布")
    if snapshot.readiness_hash != data.readiness_hash:
        raise ApiProblem(409, "READINESS_HASH_MISMATCH", "发布准备哈希不匹配")
    now = now_utc()
    expiry = (
        snapshot.expires_at.replace(tzinfo=UTC)
        if snapshot.expires_at.tzinfo is None
        else snapshot.expires_at
    )
    if snapshot.status != "ready" or snapshot.consumed_at is not None:
        raise ApiProblem(409, "READINESS_ALREADY_CONSUMED", "发布准备已消费或失效")
    if expiry <= now:
        snapshot.status, snapshot.invalidated_at = "expired", now
        raise ApiProblem(410, "READINESS_EXPIRED", "发布准备已过期")
    session = owned_session(db, actor_id, snapshot.review_session_id, lock=True)
    job, revision, paper_now = current_inputs(db, assignment)
    if (
        job.id != session.generation_job_id
        or revision.id != session.draft_revision_id
        or paper_now.id != session.paper_version_id
        or job.source_snapshot_hash != session.source_snapshot_hash
        or review_source_hash(db, assignment, job, revision, paper_now)
        != session.structured_binding_hash
    ):
        snapshot.status, snapshot.invalidated_at = "invalidated", now
        raise ApiProblem(409, "READINESS_STALE", "生成、修订或试卷来源已变化")
    projection = validate_current_projection_under_locks(
        db,
        session,
        binding_id=snapshot.binding_id,
        lock=True,
        require_confirmed=True,
    )
    if not projection.current or projection.binding is None:
        snapshot.status, snapshot.invalidated_at = "invalidated", now
        raise ApiProblem(
            409,
            "READINESS_STALE",
            "发布投影或 legacy 确认已漂移",
            {"reason": projection.reason},
        )
    refresh(db, session)
    paper = db.get(PaperVersion, snapshot.paper_version_id)
    binding = projection.binding
    legacy = db.get(RubricVersion, snapshot.legacy_rubric_version_id)
    if (
        not paper
        or not binding
        or not legacy
        or binding.status != "confirmed"
        or session.blocking_count
        or REQUIRED_CONFIRMATIONS - set(valid_confirmations(db, session))
    ):
        raise ApiProblem(409, "READINESS_STALE", "发布门禁已变化")
    if (
        assignment.updated_at != data.expected_assignment_updated_at
        or digest(state_payload(db, assignment, paper)) != snapshot.assignment_state_hash
        or session.risk_ledger_hash != snapshot.risk_ledger_hash
        or session.generation != snapshot.generation
        or session.draft_revision_id != snapshot.draft_revision_id
    ):
        snapshot.status, snapshot.invalidated_at = "invalidated", now
        raise ApiProblem(409, "READINESS_STALE", "作业或审查状态已变化")
    from app.api.assignments import freeze_participant_roster, publish_issues

    assignment.active_paper_version_id, assignment.active_rubric_version_id = paper.id, legacy.id
    issues = publish_issues(db, assignment)
    if issues:
        raise ApiProblem(422, "ASSIGNMENT_INCOMPLETE", "现有发布门禁未通过", {"issues": issues})
    participant_count = freeze_participant_roster(db, assignment)
    paper.status, paper.confirmed_at = VersionStatus.confirmed, paper.confirmed_at or now
    legacy.status, legacy.confirmed_at = VersionStatus.confirmed, legacy.confirmed_at or now
    assignment.status, assignment.published_at = AssignmentStatus.published, now
    snapshot.status, snapshot.consumed_at = "consumed", now
    session.status, session.completed_at = "published", now
    audit(
        db,
        actor_id,
        "assignment.publish",
        "assignment",
        assignment.id,
        {
            "readiness_snapshot_id": str(snapshot.id),
            "review_session_id": str(session.id),
            "binding_id": str(binding.id),
            "paper_version_id": str(paper.id),
            "rubric_version_id": str(legacy.id),
            "participant_count": participant_count,
        },
    )
    db.commit()
    return assignment
