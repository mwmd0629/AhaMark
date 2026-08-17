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
    AssignmentSourceFileAnalysis,
    AssignmentStatus,
    ClassStudent,
    MembershipStatus,
    PaperPage,
    PaperPageOrganizationSuggestion,
    PaperVersion,
    Question,
    QuestionStatus,
    ReferenceAnswerVersion,
    RubricCriterion,
    SchoolClass,
    StoredFile,
    StructuredRubricSet,
    StructuredRubricSetItem,
    StructuredRubricVersion,
    Student,
    VersionStatus,
    now_utc,
)
from app.question_versions import question_version_token
from app.semantic_content import reference_answer_semantic_payload, semantic_hash
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["assignment-central-review"])
Db = Annotated[Session, Depends(get_db)]
ACTIVE = {"draft", "in_review", "changes_required", "ready_for_set", "ready_to_publish"}
CONFIRMATION_TYPES = {
    "classes",
    "due_at",
    "total_score",
    "file_roles",
    "answer_sources",
    "paper_version",
    "reference_answers",
    "structured_rubrics",
}
REQUIRED_CONFIRMATIONS = CONFIRMATION_TYPES - {
    "answer_sources",
    "file_roles",
    "paper_version",
}
AUTOMATIC_CONFIRMATION_TYPES = REQUIRED_CONFIRMATIONS
CONFIRMATION_FINGERPRINT_VERSION = "confirmation-fingerprint-v2"
REVIEW_SOURCE_SCHEMA_VERSION = "publish-content-v3"
BUNDLE_SCHEMA_VERSION = "assignment-review-bundle-v2"
STRUCTURED_SET_WRITE_LOCK_ORDER = (
    "assignment",
    "snapshot",
    "session",
    "paper",
    "questions",
    "structured_set",
    "formal_versions",
    "criteria",
    "structured_set_items",
)
SECTION_GUIDANCE: dict[str, tuple[int, str, str]] = {
    "classes": (1, "assignment-basics", "去第 1 步选择可发布的有效班级"),
    "due_at": (1, "assignment-basics", "去第 1 步设置截止时间或选择无截止时间"),
    "files": (2, "generation-file-analysis", "去第 2 步处理对应文件"),
    "pages": (3, "paper-pages", "去第 3 步检查对应页面"),
    "questions": (4, "question-editor", "去第 4 步修改对应题目"),
    "total_score": (4, "question-editor", "去第 4 步校正题目分值或作业总分"),
    "answers": (5, "answer-rubric-editor", "去第 5 步补全对应题目的参考答案"),
    "rubrics": (5, "answer-rubric-editor", "去第 5 步修改对应题目的评分标准"),
    "publication": (6, "assignment-central-review", "留在第 6 步重新准备发布版本"),
    "validation": (6, "assignment-central-review", "留在第 6 步基于最新内容重新核查"),
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
        "structured_rubric_versions": rubrics,
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
        "history": [_rubric_bundle_json(db, item) for item in row["structured_rubric_versions"]],
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
    rubric_set = (
        db.get(StructuredRubricSet, session.structured_rubric_set_id)
        if session is not None and session.structured_rubric_set_id is not None
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
    set_validation = (
        validate_current_structured_set_under_locks(
            db,
            session,
            rubric_set_id=rubric_set.id if rubric_set is not None else None,
            lock=False,
            require_confirmed=False,
        )
        if session is not None and rubric_set is not None
        else StructuredSetValidation(None, False, "STRUCTURED_SET_REQUIRED")
    )
    confirmations = [
        {
            "id": str(item.id),
            "type": kind,
            "status": "confirmed",
            "source_hash": item.source_hash,
            "origin": item.confirmation_origin or "origin",
            "inherited": item.confirmation_origin == "inherited",
            "fingerprint_schema_version": item.fingerprint_schema_version,
            "confirmed_at": item.confirmed_at,
            "visibility": "teacher",
        }
        for kind, item in sorted(confirms.items())
    ]
    set_payload = (
        {
            "id": str(rubric_set.id),
            "status": rubric_set.status if set_validation.current else "stale",
            "version": rubric_set.version,
            "content_hash": rubric_set.content_hash,
            "source_snapshot_hash": rubric_set.source_snapshot_hash,
            "total_points": str(rubric_set.total_points),
            "current": set_validation.current,
            "reason": set_validation.reason,
            "visibility": "teacher",
        }
        if rubric_set is not None
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
                if set_validation.current
                and not blockers
                and REQUIRED_CONFIRMATIONS <= set(confirms)
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
        "structured_rubric_set": set_payload,
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
    raise ApiProblem(422, "CONFIRMATION_TYPE_INVALID", "不支持的确认类型")


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


def _structured_set_entries(db: Session, session: AssignmentReviewSession) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    # Historical extraction drafts may contain duplicate Question.display_order values.
    # A release set requires a unique, contiguous order, so snapshot the already
    # deterministic selected_versions order instead of copying unsafe legacy values.
    for display_order, row in enumerate(selected_versions(db, session.paper_version_id), start=1):
        question = row["question"]
        answer = row["answer"]
        rubric = row["rubric"]
        if answer is None or rubric is None or question.max_score is None:
            raise ApiProblem(
                422,
                "STRUCTURED_SET_INCOMPLETE",
                "题目、参考答案、结构化评分标准或分值不完整",
                {"question_id": str(question.id)},
            )
        criteria = _rubric_criteria(db, rubric.id)
        entries.append(
            {
                "question_id": str(question.id),
                "question_version": rubric.question_version,
                "reference_answer_version_id": str(answer.id),
                "structured_rubric_version_id": str(rubric.id),
                "answer_content_hash": semantic_hash(_answer_content_payload(answer)),
                "rubric_content_hash": semantic_hash(_rubric_content_payload(db, rubric)),
                "criteria_hash": semantic_hash([_criterion_payload(item) for item in criteria]),
                "display_order": display_order,
                "max_points": str(question.max_score),
            }
        )
    return entries


def _structured_set_payload(db: Session, session: AssignmentReviewSession) -> dict[str, Any]:
    entries = _structured_set_entries(db, session)
    return {
        "assignment_id": str(session.assignment_id),
        "paper_version_id": str(session.paper_version_id),
        "source_snapshot_hash": session.source_snapshot_hash,
        "total_points": str(sum(Decimal(item["max_points"]) for item in entries)),
        "items": entries,
    }


@dataclass(frozen=True)
class StructuredSetValidation:
    rubric_set: StructuredRubricSet | None
    current: bool
    reason: str | None


def validate_current_structured_set_under_locks(
    db: Session,
    session: AssignmentReviewSession,
    *,
    rubric_set_id: uuid.UUID | None = None,
    lock: bool,
    require_confirmed: bool = True,
    require_current_selection: bool = True,
) -> StructuredSetValidation:
    """Validate one immutable Structured Rubric Set under the publication lock order."""
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
        return StructuredSetValidation(None, False, "PAPER_NOT_CURRENT")

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

    target_id = rubric_set_id or session.structured_rubric_set_id
    if target_id is None:
        return StructuredSetValidation(None, False, "STRUCTURED_SET_REQUIRED")
    set_query = (
        select(StructuredRubricSet)
        .where(StructuredRubricSet.id == target_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        set_query = set_query.with_for_update()
    rubric_set = db.scalar(set_query)
    if rubric_set is None or rubric_set.assignment_id != session.assignment_id:
        return StructuredSetValidation(None, False, "STRUCTURED_SET_NOT_FOUND")
    items_query = (
        select(StructuredRubricSetItem)
        .where(StructuredRubricSetItem.rubric_set_id == rubric_set.id)
        .order_by(StructuredRubricSetItem.display_order, StructuredRubricSetItem.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        items_query = items_query.with_for_update()
    stored_items = list(db.scalars(items_query))
    stored_payload = [
        {
            "question_id": str(item.question_id),
            "question_version": item.question_version,
            "reference_answer_version_id": str(item.reference_answer_version_id),
            "structured_rubric_version_id": str(item.structured_rubric_version_id),
            "answer_content_hash": item.answer_content_hash,
            "rubric_content_hash": item.rubric_content_hash,
            "criteria_hash": item.criteria_hash,
            "display_order": item.display_order,
            "max_points": str(item.max_points),
        }
        for item in stored_items
    ]
    payload = {
        "assignment_id": str(rubric_set.assignment_id),
        "paper_version_id": str(rubric_set.paper_version_id),
        "source_snapshot_hash": rubric_set.source_snapshot_hash,
        "total_points": str(rubric_set.total_points),
        "items": stored_payload,
    }
    if (
        rubric_set.paper_version_id != session.paper_version_id
        or rubric_set.source_snapshot_hash != session.source_snapshot_hash
        or rubric_set.content_hash != semantic_hash(payload)
    ):
        return StructuredSetValidation(rubric_set, False, "STRUCTURED_SET_STALE")
    for item in stored_items:
        question = db.get(Question, item.question_id)
        answer = db.get(ReferenceAnswerVersion, item.reference_answer_version_id)
        rubric = db.get(StructuredRubricVersion, item.structured_rubric_version_id)
        if (
            question is None
            or answer is None
            or rubric is None
            or item.question_version != question_version_token(question)
            or answer.question_id != item.question_id
            or rubric.question_id != item.question_id
            or rubric.reference_answer_version_id != answer.id
            or rubric.question_version != item.question_version
            or semantic_hash(_answer_content_payload(answer)) != item.answer_content_hash
            or semantic_hash(_rubric_content_payload(db, rubric)) != item.rubric_content_hash
            or semantic_hash([_criterion_payload(row) for row in _rubric_criteria(db, rubric.id)])
            != item.criteria_hash
        ):
            return StructuredSetValidation(rubric_set, False, "STRUCTURED_SET_STALE")
        if require_confirmed and (answer.status != "confirmed" or rubric.status != "confirmed"):
            return StructuredSetValidation(rubric_set, False, "STRUCTURED_SET_NOT_CONFIRMED")
    if require_current_selection and _structured_set_entries(db, session) != stored_payload:
        return StructuredSetValidation(rubric_set, False, "STRUCTURED_SET_STALE")
    if require_confirmed and rubric_set.status not in {"confirmed", "active"}:
        return StructuredSetValidation(rubric_set, False, "STRUCTURED_SET_NOT_CONFIRMED")
    return StructuredSetValidation(rubric_set, True, None)


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
        )
        is_same_session_v1 = row.fingerprint_schema_version is None and row.source_hash == digest(
            confirmation_value(db, session, row.confirmation_type)
        )
        if row.confirmation_type not in found and (is_v2 or is_same_session_v1):
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
        action: str | None = None,
    ) -> None:
        step, anchor, default_action = SECTION_GUIDANCE.get(
            section,
            (6, "assignment-central-review", "在中央核查中处理该问题"),
        )
        teacher_action = action or default_action
        impact = "未修复前不能发布" if severity == "blocking" else "不会单独阻塞发布"
        teacher_message = f"{message}；{impact}；{teacher_action}。"
        payload = {
            "code": code,
            "section": section,
            "message": teacher_message,
            "entity": entity,
            "entity_id": str(entity_id or assignment.id),
            "evidence": (evidence or {})
            | {
                "teacher_guidance": {
                    "object": f"{entity}:{entity_id or assignment.id}",
                    "reason": message,
                    "impact": impact,
                    "action": teacher_action,
                    "step": step,
                    "anchor": anchor,
                }
            },
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
        != session.structured_set_hash
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
        missing_numbers = "、".join(q.question_number for q in qs if q.max_score is None)
        add(
            "QUESTION_SCORE_REQUIRED",
            "total_score",
            f"第 {missing_numbers} 题尚未设置满分",
        )
    elif assignment.total_score is None or sum(
        (Decimal(q.max_score) for q in qs if q.max_score is not None), Decimal()
    ) != Decimal(assignment.total_score):
        question_total = sum(
            (Decimal(q.max_score) for q in qs if q.max_score is not None), Decimal()
        )
        mismatch_message = (
            f"题目分值合计为 {question_total} 分，"
            f"作业总分为 {assignment.total_score} 分，二者不一致"
        )
        add(
            "TOTAL_SCORE_MISMATCH",
            "total_score",
            mismatch_message,
        )
    for f in db.scalars(
        select(AssignmentSourceFileAnalysis).where(
            AssignmentSourceFileAnalysis.draft_revision_id == session.draft_revision_id
        )
    ):
        stored_file = db.get(StoredFile, f.stored_file_id)
        file_label = (
            f"文件“{stored_file.original_name}”"
            if stored_file is not None
            else f"文件 {f.stored_file_id}"
        )
        if f.analysis_status in {"failed", "corrupted"}:
            add(
                "FILE_CORRUPTED",
                "files",
                f"{file_label}分析失败或文件已损坏",
                entity="file",
                entity_id=f.stored_file_id,
            )
        effective_role = f.teacher_confirmed_role or f.suggested_role
        role_needs_review = effective_role in {None, "unknown"} or (
            not f.teacher_confirmed_role
            and (
                float(f.role_confidence or 0) < 0.7
                or "FILE_ROLE_CONFLICT_REVIEW_REQUIRED" in (f.warning_codes or [])
            )
        )
        if role_needs_review:
            add(
                "FILE_ROLE_UNCONFIRMED",
                "files",
                f"{file_label}的用途置信度不足或与其他文件角色冲突",
                entity="file",
                entity_id=f.stored_file_id,
            )
    for page in db.scalars(
        select(AssignmentPageAnalysis).where(
            AssignmentPageAnalysis.draft_revision_id == session.draft_revision_id
        )
    ):
        paper_page = db.get(PaperPage, page.paper_page_id)
        page_label = (
            f"第 {paper_page.page_number} 页"
            if paper_page is not None
            else f"页面 {page.paper_page_id}"
        )
        if page.corrupted:
            add(
                "PAGE_CORRUPTED",
                "pages",
                f"{page_label}无法读取或已损坏",
                entity="page",
                entity_id=page.paper_page_id,
            )
        if page.missing_page_suspected:
            add(
                "MISSING_PAGE_SUSPECTED",
                "pages",
                f"{page_label}附近的页码或内容连续性异常，系统怀疑缺页",
                entity="page",
                entity_id=page.paper_page_id,
            )
        if page.mixed_document_suspected or page.variant_label not in {None, "", "unknown"}:
            add(
                "PAPER_VARIANT_REVIEW",
                "pages",
                f"{page_label}的版式或版本标记与当前试卷不一致，可能混入其他试卷或 A/B 卷",
                entity="page",
                entity_id=page.paper_page_id,
            )
        if page.low_quality:
            add(
                "PAGE_LOW_QUALITY",
                "pages",
                f"{page_label}清晰度较低，部分内容可能无法可靠识别",
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
    if not complete_generated_content:
        for candidate in db.scalars(
            select(AssignmentQuestionExtractionCandidate).where(
                AssignmentQuestionExtractionCandidate.draft_revision_id
                == session.draft_revision_id,
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
    # GenerationIssue and revision.risk_summary are append-only generation audit.
    # They are intentionally not projected into the live teacher queue: every
    # publication issue below is recomputed from the current persisted content.
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
    for kind in sorted(REQUIRED_CONFIRMATIONS - set(confirms)):
        add(
            f"CONFIRM_{kind.upper()}_REQUIRED",
            {
                "reference_answers": "answers",
                "structured_rubrics": "rubrics",
                "paper_version": "pages",
            }.get(kind, kind),
            f"必须由教师明确确认 {kind}",
        )
    if session.structured_rubric_set_id is not None:
        validation = validate_current_structured_set_under_locks(
            db,
            session,
            rubric_set_id=session.structured_rubric_set_id,
            lock=False,
            require_confirmed=False,
        )
        if not validation.current:
            add(
                "STRUCTURED_SET_STALE",
                "publication",
                "待发布的结构化评分标准集合已过期，请重新准备",
            )
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
            else ("ready_to_publish" if session.structured_rubric_set_id else "ready_for_set")
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
        "structured_rubric_set_id": str(s.structured_rubric_set_id)
        if s.structured_rubric_set_id
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
        and old.structured_set_hash == source
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
        structured_set_hash=source,
        created_by=actor.id,
    )
    db.add(row)
    db.flush()
    inherited_types: list[str] = []
    for kind, previous in inherited.items():
        if (
            previous.fingerprint_schema_version != CONFIRMATION_FINGERPRINT_VERSION
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


def _safe_prepared_versions(db: Session, rows: list[dict[str, Any]]) -> bool:
    from app.api.assignment_answer_rubric import (
        _answer_ineligibility_reasons,
        _rubric_ineligibility_reasons,
    )

    for row in rows:
        answer = row["answer"]
        rubric = row["rubric"]
        if answer is None or rubric is None:
            return False
        if answer.status == "draft":
            answer_candidate = (
                db.get(AssignmentAnswerDraftCandidate, answer.origin_answer_candidate_id)
                if answer.origin_answer_candidate_id
                else None
            )
            if answer_candidate is None:
                return False
            if (
                answer_candidate.status in {"accepted", "modified"}
                and answer_candidate.reviewed_by is not None
            ):
                pass
            elif answer_candidate.status == "system_prepared":
                reasons = set(_answer_ineligibility_reasons(answer_candidate)) - {
                    "CANDIDATE_NOT_SUGGESTED"
                }
                if reasons:
                    return False
            else:
                return False
        elif answer.status != "confirmed":
            return False
        if rubric.status == "draft":
            rubric_candidate = (
                db.get(AssignmentRubricDraftCandidate, rubric.origin_rubric_candidate_id)
                if rubric.origin_rubric_candidate_id
                else None
            )
            if rubric_candidate is None:
                return False
            if (
                rubric_candidate.status in {"accepted", "modified"}
                and rubric_candidate.reviewed_by is not None
            ):
                pass
            elif rubric_candidate.status == "system_prepared":
                reasons = set(_rubric_ineligibility_reasons(db, rubric_candidate)) - {
                    "CANDIDATE_NOT_SUGGESTED",
                    "ANSWER_CANDIDATE_NOT_ACCEPTED",
                }
                if reasons:
                    return False
            else:
                return False
        elif rubric.status != "confirmed":
            return False
    return True


def _system_prepare_eligible_candidates(
    db: Session,
    revision: AssignmentDraftRevision,
    actor: Actor,
) -> dict[str, int]:
    from app.api.assignment_answer_rubric import (
        _answer_ineligibility_reasons,
        _rubric_ineligibility_reasons,
    )
    from app.assignment_generation.answer_rubric import materialize_reference, materialize_rubric

    prepared_answers = 0
    prepared_rubrics = 0
    answers = list(
        db.scalars(
            select(AssignmentAnswerDraftCandidate)
            .where(
                AssignmentAnswerDraftCandidate.draft_revision_id == revision.id,
                AssignmentAnswerDraftCandidate.owner_id == actor.id,
                AssignmentAnswerDraftCandidate.status == "suggested",
            )
            .with_for_update()
        )
    )
    for answer_candidate in answers:
        if _answer_ineligibility_reasons(answer_candidate):
            continue
        answer_candidate.status = "system_prepared"
        answer_candidate.teacher_edit_version += 1
        materialize_reference(db, answer_candidate, actor.id)
        prepared_answers += 1
        audit(
            db,
            actor.id,
            "assignment_candidate.system_prepare",
            "assignment_answer_draft_candidate",
            answer_candidate.id,
            {"teacher_reviewed": False, "source_type": answer_candidate.source_type},
        )
    db.flush()
    rubrics = list(
        db.scalars(
            select(AssignmentRubricDraftCandidate)
            .where(
                AssignmentRubricDraftCandidate.draft_revision_id == revision.id,
                AssignmentRubricDraftCandidate.owner_id == actor.id,
                AssignmentRubricDraftCandidate.status == "suggested",
            )
            .with_for_update()
        )
    )
    for rubric_candidate in rubrics:
        if _rubric_ineligibility_reasons(db, rubric_candidate):
            continue
        rubric_candidate.status = "system_prepared"
        rubric_candidate.teacher_edit_version += 1
        materialize_rubric(db, rubric_candidate, actor.id)
        prepared_rubrics += 1
        audit(
            db,
            actor.id,
            "assignment_candidate.system_prepare",
            "assignment_rubric_draft_candidate",
            rubric_candidate.id,
            {"teacher_reviewed": False, "scoring_mode": rubric_candidate.scoring_mode},
        )
    revision.teacher_edit_version += prepared_answers + prepared_rubrics
    return {"answers": prepared_answers, "rubrics": prepared_rubrics}


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
    if confirmation_type not in CONFIRMATION_TYPES:
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


@router.post("/assignment-review-sessions/{session_id}/structured-rubric-set")
def create_structured_rubric_set(
    session_id: uuid.UUID, data: VersionedAction, db: Db, actor: Actor
) -> dict[str, Any]:
    session = owned_session(db, actor.id, session_id, lock=True)
    assignment = owned_assignment(db, actor.id, session.assignment_id, lock=True)
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_NOT_DRAFT", "只有草稿可准备发布")
    if session.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    job, revision, paper = current_inputs(db, assignment)
    if (
        job.id != session.generation_job_id
        or revision.id != session.draft_revision_id
        or paper.id != session.paper_version_id
        or job.source_snapshot_hash != session.source_snapshot_hash
        or review_source_hash(db, assignment, job, revision, paper) != session.structured_set_hash
    ):
        raise ApiProblem(409, "REVIEW_SOURCE_STALE", "生成、修订或试卷来源已变化")
    missing = REQUIRED_CONFIRMATIONS - set(valid_confirmations(db, session))
    if missing:
        raise ApiProblem(
            422,
            "STRUCTURED_SET_CONFIRMATIONS_REQUIRED",
            "当前发布内容尚未完成安全确认",
            {"missing": sorted(missing)},
        )
    prepared_rows = selected_versions(db, session.paper_version_id)
    if not _safe_prepared_versions(db, prepared_rows):
        raise ApiProblem(422, "STRUCTURED_SET_INELIGIBLE", "答案或结构化评分标准尚未达到待发布资格")
    payload = _structured_set_payload(db, session)
    content_hash = semantic_hash(payload)
    rubric_set = db.scalar(
        select(StructuredRubricSet)
        .where(
            StructuredRubricSet.assignment_id == assignment.id,
            StructuredRubricSet.content_hash == content_hash,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    created = False
    if rubric_set is None:
        version = (
            db.scalar(
                select(func.max(StructuredRubricSet.version)).where(
                    StructuredRubricSet.assignment_id == assignment.id
                )
            )
            or 0
        ) + 1
        rubric_set = StructuredRubricSet(
            owner_id=actor.id,
            assignment_id=assignment.id,
            paper_version_id=session.paper_version_id,
            version=version,
            status="draft",
            content_hash=content_hash,
            source_snapshot_hash=session.source_snapshot_hash,
            total_points=Decimal(payload["total_points"]),
            created_by=actor.id,
        )
        db.add(rubric_set)
        db.flush()
        for item in payload["items"]:
            db.add(
                StructuredRubricSetItem(
                    rubric_set_id=rubric_set.id,
                    question_id=uuid.UUID(item["question_id"]),
                    question_version=item["question_version"],
                    reference_answer_version_id=uuid.UUID(item["reference_answer_version_id"]),
                    structured_rubric_version_id=uuid.UUID(item["structured_rubric_version_id"]),
                    answer_content_hash=item["answer_content_hash"],
                    rubric_content_hash=item["rubric_content_hash"],
                    criteria_hash=item["criteria_hash"],
                    display_order=item["display_order"],
                    max_points=Decimal(item["max_points"]),
                )
            )
        created = True
    if session.structured_rubric_set_id != rubric_set.id:
        session.structured_rubric_set_id = rubric_set.id
        session.review_version += 1
    db.flush()
    refresh(db, session)
    audit(
        db,
        actor.id,
        "assignment.structured_rubric_set.prepare",
        "structured_rubric_set",
        rubric_set.id,
        {"created": created, "content_hash": content_hash, "version": rubric_set.version},
    )
    db.commit()
    return structured_rubric_set_json(db, rubric_set) | {
        "created": created,
        "review_version": session.review_version,
    }


def structured_rubric_set_json(db: Session, rubric_set: StructuredRubricSet) -> dict[str, Any]:
    items = list(
        db.scalars(
            select(StructuredRubricSetItem)
            .where(StructuredRubricSetItem.rubric_set_id == rubric_set.id)
            .order_by(StructuredRubricSetItem.display_order, StructuredRubricSetItem.id)
        )
    )
    return {
        "id": str(rubric_set.id),
        "assignment_id": str(rubric_set.assignment_id),
        "paper_version_id": str(rubric_set.paper_version_id),
        "version": rubric_set.version,
        "status": rubric_set.status,
        "content_hash": rubric_set.content_hash,
        "source_snapshot_hash": rubric_set.source_snapshot_hash,
        "total_points": str(rubric_set.total_points),
        "confirmed_by": str(rubric_set.confirmed_by) if rubric_set.confirmed_by else None,
        "confirmed_at": rubric_set.confirmed_at,
        "activated_at": rubric_set.activated_at,
        "items": [
            {
                "id": str(item.id),
                "question_id": str(item.question_id),
                "question_version": item.question_version,
                "reference_answer_version_id": str(item.reference_answer_version_id),
                "structured_rubric_version_id": str(item.structured_rubric_version_id),
                "answer_content_hash": item.answer_content_hash,
                "rubric_content_hash": item.rubric_content_hash,
                "criteria_hash": item.criteria_hash,
                "display_order": item.display_order,
                "max_points": str(item.max_points),
            }
            for item in items
        ],
    }


@router.get("/assignment-review-sessions/{session_id}/structured-rubric-set")
def get_structured_rubric_set(session_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    session = owned_session(db, actor.id, session_id)
    if session.structured_rubric_set_id is None:
        raise ApiProblem(404, "STRUCTURED_SET_NOT_FOUND", "尚未准备结构化评分标准集合")
    rubric_set = db.get(StructuredRubricSet, session.structured_rubric_set_id)
    if rubric_set is None or rubric_set.assignment_id != session.assignment_id:
        raise ApiProblem(404, "STRUCTURED_SET_NOT_FOUND", "结构化评分标准集合不存在")
    validation = validate_current_structured_set_under_locks(
        db,
        session,
        rubric_set_id=rubric_set.id,
        lock=False,
        require_confirmed=False,
    )
    return structured_rubric_set_json(db, rubric_set) | {
        "current": validation.current,
        "reason": validation.reason,
        "review_version": session.review_version,
    }


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
        != session.structured_set_hash
    ):
        raise ApiProblem(409, "REVIEW_SOURCE_STALE", "生成、修订或试卷来源已变化")
    set_validation = validate_current_structured_set_under_locks(
        db, session, lock=True, require_confirmed=False
    )
    if not set_validation.current or set_validation.rubric_set is None:
        raise ApiProblem(
            409,
            "STRUCTURED_SET_STALE",
            "待发布的结构化评分标准集合已漂移",
            {"reason": set_validation.reason},
        )
    refresh(db, session)
    prepared_rows = selected_versions(db, session.paper_version_id)
    blocking_codes = set(
        db.scalars(
            select(AssignmentReviewItem.issue_code).where(
                AssignmentReviewItem.review_session_id == session.id,
                AssignmentReviewItem.severity == "blocking",
                AssignmentReviewItem.status == "open",
            )
        )
    )
    prepared_draft_blockers = {
        "REFERENCE_ANSWER_UNCONFIRMED",
        "STRUCTURED_RUBRIC_UNCONFIRMED",
    }
    safe_bundle_approval = bool(blocking_codes) and blocking_codes <= prepared_draft_blockers
    safe_bundle_approval = safe_bundle_approval and _safe_prepared_versions(db, prepared_rows)
    if session.blocking_count and not safe_bundle_approval:
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
    rubric_set = set_validation.rubric_set
    if rubric_set is None or assignment.total_score is None:
        raise ApiProblem(422, "PUBLICATION_INPUT_INCOMPLETE", "发布输入不完整")
    paper = db.get(PaperVersion, session.paper_version_id)
    assert paper
    payload = state_payload(db, assignment, paper)
    state_hash = digest(payload)
    class_ids = payload["assignment"]["class_ids"]
    ready_payload = {
        "session_id": session.id,
        "review_version": session.review_version,
        "state_hash": state_hash,
        "risk_hash": session.risk_ledger_hash,
        "structured_rubric_set_id": rubric_set.id,
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
        structured_rubric_set_id=rubric_set.id,
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
        "preparation_status": "ready",
        "id": str(x.id),
        "assignment_id": str(x.assignment_id),
        "review_session_id": str(x.review_session_id),
        "readiness_hash": x.readiness_hash,
        "status": x.status,
        "expires_at": x.expires_at,
        "consumed_at": x.consumed_at,
        "paper_version_id": str(x.paper_version_id),
        "structured_rubric_set_id": str(x.structured_rubric_set_id),
        "class_ids": x.class_ids,
        "due_at": x.due_at,
        "total_score": str(x.total_score),
        "bundle": {
            "assignment_state_hash": x.assignment_state_hash,
            "risk_ledger_hash": x.risk_ledger_hash,
            "source_snapshot_hash": x.source_snapshot_hash,
            "generation": x.generation,
            "draft_revision_id": str(x.draft_revision_id),
            "issue_counts": x.issue_counts,
        },
    }


@router.post("/assignments/{assignment_id}/prepare-publication")
def prepare_assignment_publication(
    assignment_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    """Idempotently drive safe review preparation and report a pollable state."""

    session_payload = create_review_session(assignment_id, db, actor)
    session_id = uuid.UUID(session_payload["id"])
    session = owned_session(db, actor.id, session_id)
    job = db.get(AssignmentGenerationJob, session.generation_job_id)
    if job is not None and job.status in {
        "queued",
        "analyzing",
        "processing_pages",
        "extracting_questions",
        "generating_rubrics",
        "validating",
    }:
        return {
            "preparation_status": "preparing",
            "assignment_id": str(assignment_id),
            "review_session_id": str(session.id),
            "review_version": session.review_version,
            "stage": job.current_stage or job.status,
            "progress": job.progress,
            "retryable": True,
        }

    revision = db.get(AssignmentDraftRevision, session.draft_revision_id)
    assert revision is not None
    prepared = _system_prepare_eligible_candidates(db, revision, actor)
    if prepared["answers"] or prepared["rubrics"]:
        db.commit()
        session_payload = create_review_session(assignment_id, db, actor)
        session = owned_session(db, actor.id, uuid.UUID(session_payload["id"]))

    auto_confirm_review_inputs(
        session.id,
        VersionedAction(expected_review_version=session.review_version),
        db,
        actor,
    )
    session = owned_session(db, actor.id, session.id)
    confirms = valid_confirmations(db, session)
    set_error: ApiProblem | None = None
    if not (REQUIRED_CONFIRMATIONS - set(confirms)):
        try:
            create_structured_rubric_set(
                session.id,
                VersionedAction(expected_review_version=session.review_version),
                db,
                actor,
            )
        except ApiProblem as exc:
            set_error = exc
    session = owned_session(db, actor.id, session.id)
    try:
        return prepare_publication(
            session.id,
            VersionedAction(expected_review_version=session.review_version),
            db,
            actor,
        )
    except ApiProblem as exc:
        if exc.status not in {409, 422}:
            raise
        bundle = review_bundle(db, actor.id, assignment_id)
        exceptions = [
            {
                "code": item["code"],
                "severity": item["severity"],
                "message": item["message"],
                "entity_type": item.get("entity_type"),
                "entity_id": item.get("entity_id"),
            }
            for item in bundle["blockers"]
            if item["severity"] in {"blocking", "warning"}
        ]
        if set_error is not None and not any(item["code"] == set_error.code for item in exceptions):
            exceptions.append(
                {
                    "code": set_error.code,
                    "severity": "blocking",
                    "message": set_error.message,
                    "entity_type": "structured_rubric_set",
                    "entity_id": None,
                }
            )
        return {
            "preparation_status": "exception_required",
            "assignment_id": str(assignment_id),
            "review_session_id": str(session.id),
            "review_version": session.review_version,
            "retryable": exc.status == 409,
            "exceptions": exceptions,
            "bundle_hash": bundle["version"]["bundle_hash"],
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
        != session.structured_set_hash
    ):
        snapshot.status, snapshot.invalidated_at = "invalidated", now
        raise ApiProblem(409, "READINESS_STALE", "生成、修订或试卷来源已变化")
    set_validation = validate_current_structured_set_under_locks(
        db,
        session,
        rubric_set_id=snapshot.structured_rubric_set_id,
        lock=True,
        require_confirmed=False,
    )
    if not set_validation.current or set_validation.rubric_set is None:
        snapshot.status, snapshot.invalidated_at = "invalidated", now
        raise ApiProblem(
            409,
            "READINESS_STALE",
            "结构化评分标准集合已漂移",
            {"reason": set_validation.reason},
        )
    paper = db.get(PaperVersion, snapshot.paper_version_id)
    if (
        paper is None
        or digest(state_payload(db, assignment, paper)) != snapshot.assignment_state_hash
        or session.risk_ledger_hash != snapshot.risk_ledger_hash
        or session.generation != snapshot.generation
        or session.draft_revision_id != snapshot.draft_revision_id
    ):
        snapshot.status, snapshot.invalidated_at = "invalidated", now
        raise ApiProblem(409, "READINESS_STALE", "作业、Bundle 或审查状态已变化")
    selected = selected_versions(db, session.paper_version_id)
    if not _safe_prepared_versions(db, selected):
        snapshot.status, snapshot.invalidated_at = "invalidated", now
        raise ApiProblem(409, "READINESS_STALE", "Bundle 中的答案或评分标准已失去资格")
    for selected_row in selected:
        answer = selected_row["answer"]
        rubric = selected_row["rubric"]
        assert answer is not None and rubric is not None
        if answer.status == "draft":
            answer.status = "confirmed"
            answer.teacher_confirmed_at = now
            audit(
                db,
                actor_id,
                "assignment_bundle.confirm_reference",
                "reference_answer_version",
                answer.id,
                {"readiness_snapshot_id": str(snapshot.id)},
            )
        if rubric.status == "draft":
            rubric.status, rubric.confirmed_by, rubric.confirmed_at = "confirmed", actor_id, now
            audit(
                db,
                actor_id,
                "assignment_bundle.confirm_rubric",
                "structured_rubric_version",
                rubric.id,
                {"readiness_snapshot_id": str(snapshot.id)},
            )
    db.flush()
    refresh(db, session)
    rubric_set = set_validation.rubric_set
    final_set_validation = validate_current_structured_set_under_locks(
        db,
        session,
        rubric_set_id=snapshot.structured_rubric_set_id,
        lock=True,
        require_confirmed=False,
    )
    if (
        not paper
        or not rubric_set
        or not final_set_validation.current
        or session.blocking_count
        or REQUIRED_CONFIRMATIONS - set(valid_confirmations(db, session))
    ):
        raise ApiProblem(409, "READINESS_STALE", "发布门禁已变化")
    from app.api.assignments import freeze_participant_roster, publish_issues

    assignment.active_paper_version_id = paper.id
    assignment.active_structured_rubric_set_id = rubric_set.id
    issues = publish_issues(db, assignment)
    if issues:
        raise ApiProblem(422, "ASSIGNMENT_INCOMPLETE", "现有发布门禁未通过", {"issues": issues})
    participant_count = freeze_participant_roster(db, assignment)
    paper.status, paper.confirmed_at = VersionStatus.confirmed, paper.confirmed_at or now
    rubric_set.status = "active"
    rubric_set.confirmed_by = actor_id
    rubric_set.confirmed_at = rubric_set.confirmed_at or now
    rubric_set.activated_at = now
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
            "structured_rubric_set_id": str(rubric_set.id),
            "paper_version_id": str(paper.id),
            "participant_count": participant_count,
        },
    )
    db.commit()
    return assignment
