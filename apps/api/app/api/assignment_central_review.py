"""Teacher-owned central review and two-phase assignment publication."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.db.session import get_db
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentClass,
    AssignmentDraftRevision,
    AssignmentExplicitConfirmation,
    AssignmentGenerationJob,
    AssignmentPageAnalysis,
    AssignmentPublishReadinessSnapshot,
    AssignmentQuestionExtractionCandidate,
    AssignmentReviewItem,
    AssignmentReviewSession,
    AssignmentRubricPublicationBinding,
    AssignmentSourceFileAnalysis,
    AssignmentStatus,
    GenerationIssue,
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
    VersionStatus,
    now_utc,
)
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
REQUIRED_CONFIRMATIONS = CONFIRMATION_TYPES


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
        query = query.with_for_update()
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
        query = query.with_for_update()
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


def selected_versions(db: Session, paper_id: uuid.UUID) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for q in questions(db, paper_id):
        rubric = db.scalar(
            select(StructuredRubricVersion)
            .where(
                StructuredRubricVersion.question_id == q.id,
                StructuredRubricVersion.status == "confirmed",
            )
            .order_by(StructuredRubricVersion.rubric_version.desc())
            .limit(1)
        )
        answer = (
            db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id) if rubric else None
        )
        criteria = (
            list(
                db.scalars(
                    select(RubricCriterion)
                    .where(RubricCriterion.rubric_version_id == rubric.id)
                    .order_by(RubricCriterion.display_order, RubricCriterion.id)
                )
            )
            if rubric
            else []
        )
        result.append({"question": q, "answer": answer, "rubric": rubric, "criteria": criteria})
    return result


def state_payload(db: Session, assignment: Assignment, paper: PaperVersion) -> dict[str, Any]:
    class_ids = sorted(
        str(x)
        for x in db.scalars(
            select(AssignmentClass.class_id).where(AssignmentClass.assignment_id == assignment.id)
        )
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
    generation_issues = list(
        db.scalars(
            select(GenerationIssue)
            .where(GenerationIssue.assignment_id == assignment.id)
            .order_by(GenerationIssue.id)
        )
    )
    return {
        "assignment": {
            "id": assignment.id,
            "title": assignment.title,
            "subject": assignment.subject,
            "grade": assignment.grade,
            "description": assignment.description,
            "instructions": assignment.instructions,
            "due_at": assignment.due_at,
            "total_score": assignment.total_score,
            "updated_at": assignment.updated_at,
            "class_ids": class_ids,
        },
        "paper": {"id": paper.id, "version": paper.version, "status": paper.status},
        "files": [
            {
                "id": row.stored_file_id,
                "checksum": row.checksum,
                "stored_checksum": (
                    stored.checksum
                    if (stored := db.get(StoredFile, row.stored_file_id)) is not None
                    else None
                ),
                "status": row.analysis_status,
                "role": row.teacher_confirmed_role,
                "answer_source": row.teacher_confirmed_answer_source,
                "updated_at": row.updated_at,
            }
            for row in files
        ],
        "pages": [
            {
                "id": row.id,
                "number": row.page_number,
                "rotation": row.rotation,
                "status": row.status,
                "updated_at": row.updated_at,
            }
            for row in pages
        ],
        "page_analysis": [
            {
                "id": row.id,
                "status": row.status,
                "missing": row.missing_page_suspected,
                "corrupted": row.corrupted,
                "variant": row.variant_label,
                "updated_at": row.updated_at,
            }
            for row in page_analysis
        ],
        "page_organization": [
            {
                "id": row.id,
                "status": row.status,
                "source_hash": row.source_snapshot_hash,
                "updated_at": row.updated_at,
            }
            for row in organization
        ],
        "question_candidates": [
            {
                "id": row.id,
                "status": row.status,
                "materialized": row.materialized_question_id,
                "source_hash": row.source_snapshot_hash,
                "updated_at": row.updated_at,
            }
            for row in candidates
        ],
        "generation_issues": [
            {
                "id": row.id,
                "severity": row.severity,
                "code": row.code,
                "resolution": row.resolution_status,
                "updated_at": row.updated_at,
            }
            for row in generation_issues
        ],
        "versions": [
            {
                "question_id": x["question"].id,
                "question_updated_at": x["question"].updated_at,
                "max_score": x["question"].max_score,
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
        key = (
            "teacher_confirmed_role" if kind == "file_roles" else "teacher_confirmed_answer_source"
        )
        return {
            "files": [
                {"id": f.stored_file_id, "value": getattr(f, key), "checksum": f.checksum}
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
        if row.confirmation_type not in found and row.source_hash == digest(
            confirmation_value(db, session, row.confirmation_type)
        ):
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
    if assignment.due_at is None:
        add("DUE_AT_REQUIRED", "due_at", "截止时间不能为空")
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
        if not f.teacher_confirmed_role:
            add(
                "FILE_ROLE_UNCONFIRMED",
                "files",
                "文件角色尚未确认",
                entity="file",
                entity_id=f.stored_file_id,
            )
        if not f.teacher_confirmed_answer_source or f.teacher_confirmed_answer_source == "unknown":
            add(
                "ANSWER_SOURCE_UNCONFIRMED",
                "answers",
                "答案来源未知或尚未确认",
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
        if page.mixed_document_suspected or page.variant_label:
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
    if db.scalar(
        select(PaperPageOrganizationSuggestion.id).where(
            PaperPageOrganizationSuggestion.draft_revision_id == session.draft_revision_id,
            PaperPageOrganizationSuggestion.status.in_(["suggested", "stale"]),
        )
    ):
        add("PAGE_ORGANIZATION_INCOMPLETE", "pages", "页面整理建议尚未完成")
    for candidate in db.scalars(
        select(AssignmentQuestionExtractionCandidate).where(
            AssignmentQuestionExtractionCandidate.draft_revision_id == session.draft_revision_id,
            AssignmentQuestionExtractionCandidate.status.not_in(["rejected", "superseded"]),
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
        add(
            issue.code,
            "validation",
            issue.message,
            "blocking" if issue.severity == "blocking" else "warning",
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
        elif answer.source_type in {"ai_generated", "third_party"}:
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
    confirms = valid_confirmations(db, session)
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
    elif binding.source_binding_hash != digest(
        confirmation_value(db, session, "structured_rubrics")
        | confirmation_value(db, session, "reference_answers")
    ):
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
    refresh(db, row)
    audit(
        db,
        actor.id,
        "assignment_review.create",
        "assignment_review_session",
        row.id,
        {"generation": job.generation},
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
            }
            for x in items
        ]
    }


@router.patch("/assignment-review-items/{item_id}/disposition")
def disposition(item_id: uuid.UUID, data: Disposition, db: Db, actor: Actor) -> dict[str, Any]:
    item = db.scalar(
        select(AssignmentReviewItem)
        .join(AssignmentReviewSession)
        .where(AssignmentReviewItem.id == item_id, AssignmentReviewSession.owner_id == actor.id)
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "REVIEW_ITEM_NOT_FOUND", "审查项不存在")
    session = owned_session(db, actor.id, item.review_session_id, lock=True)
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
    if confirmation_type == "due_at" and value["due_at"] is None:
        raise ApiProblem(422, "DUE_AT_REQUIRED", "截止时间不能为空")
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
    version = (
        db.scalar(
            select(func.max(AssignmentExplicitConfirmation.confirmation_version)).where(
                AssignmentExplicitConfirmation.review_session_id == session.id,
                AssignmentExplicitConfirmation.confirmation_type == confirmation_type,
            )
        )
        or 0
    ) + 1
    row = AssignmentExplicitConfirmation(
        review_session_id=session.id,
        assignment_id=session.assignment_id,
        confirmation_type=confirmation_type,
        confirmed_value=canonical(value),
        source_hash=digest(value),
        confirmation_version=version,
        confirmed_by=actor.id,
    )
    db.add(row)
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
        "confirmation_version": version,
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
    source_hash = digest(
        confirmation_value(db, session, "structured_rubrics")
        | confirmation_value(db, session, "reference_answers")
    )
    old = db.scalar(
        select(AssignmentRubricPublicationBinding).where(
            AssignmentRubricPublicationBinding.review_session_id == session.id,
            AssignmentRubricPublicationBinding.source_binding_hash == source_hash,
        )
    )
    if old:
        return binding_json(old)
    rows = selected_versions(db, session.paper_version_id)
    warnings: list[str] = []
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
        for c in criteria:
            conversion_warnings = []
            if c.dependencies:
                conversion_warnings.append("DEPENDENCY_NOT_LOSSLESS")
            if c.metadata_.get("alternative_group"):
                conversion_warnings.append("ALTERNATIVE_PATH_NOT_LOSSLESS")
            if c.validation_rule:
                conversion_warnings.append("VALIDATION_RULE_NOT_LOSSLESS")
            warnings.extend(conversion_warnings)
            item = RubricItem(
                question_rubric_id=qr.id,
                display_order=c.display_order,
                title=c.title,
                description=c.description,
                points=c.max_points,
                item_type=c.criterion_type,
                required=c.required,
                deduction_rule=json.dumps(
                    c.manual_review_policy, ensure_ascii=False, sort_keys=True
                )
                if c.manual_review_policy
                else None,
            )
            db.add(item)
            db.flush()
            criterion_map.append(
                {
                    "criterion_id": str(c.id),
                    "rubric_item_id": str(item.id),
                    "points": str(c.max_points),
                    "warnings": conversion_warnings,
                }
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
                "conversion_warnings": sorted(set(warnings)),
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
    binding = AssignmentRubricPublicationBinding(
        owner_id=actor.id,
        assignment_id=session.assignment_id,
        review_session_id=session.id,
        paper_version_id=session.paper_version_id,
        legacy_rubric_version_id=legacy.id,
        binding_version=binding_version,
        status="validated" if not warnings else "draft",
        source_binding_hash=source_hash,
        mapping=mapping,
        created_by=actor.id,
    )
    db.add(binding)
    session.legacy_rubric_version_id = legacy.id
    session.review_version += 1
    db.flush()
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
    return {
        "id": str(x.id),
        "assignment_id": str(x.assignment_id),
        "review_session_id": str(x.review_session_id),
        "paper_version_id": str(x.paper_version_id),
        "legacy_rubric_version_id": str(x.legacy_rubric_version_id),
        "binding_version": x.binding_version,
        "status": x.status,
        "source_binding_hash": x.source_binding_hash,
        "mapping": x.mapping,
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
    binding = db.scalar(
        select(AssignmentRubricPublicationBinding)
        .where(
            AssignmentRubricPublicationBinding.id == binding_id,
            AssignmentRubricPublicationBinding.owner_id == actor.id,
        )
        .with_for_update()
    )
    if binding is None:
        raise ApiProblem(404, "BINDING_NOT_FOUND", "绑定不存在")
    session = owned_session(db, actor.id, binding.review_session_id, lock=True)
    if session.review_version != data.expected_review_version:
        raise ApiProblem(409, "REVIEW_VERSION_CONFLICT", "审查版本已变化")
    if binding.status not in {"validated", "draft"}:
        raise ApiProblem(409, "BINDING_NOT_CONFIRMABLE", "绑定不可确认")
    warnings = sorted({w for row in binding.mapping for w in row.get("conversion_warnings", [])})
    if warnings:
        raise ApiProblem(
            422,
            "BINDING_NOT_LOSSLESS",
            "存在无法无损映射的规则，需教师先修改 Structured Rubric",
            {"warnings": warnings},
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
            confirmation_version=version,
            confirmed_by=actor.id,
        )
    )
    session.review_version += 1
    db.flush()
    refresh(db, session)
    audit(
        db,
        actor.id,
        "assignment_rubric_binding.confirm",
        "assignment_rubric_publication_binding",
        binding.id,
    )
    db.commit()
    return binding_json(binding) | {"review_version": session.review_version}


@router.post("/assignment-review-sessions/{session_id}/prepare-publication")
def prepare_publication(
    session_id: uuid.UUID, data: VersionedAction, db: Db, actor: Actor
) -> dict[str, Any]:
    session = owned_session(db, actor.id, session_id, lock=True)
    assignment = owned_assignment(db, actor.id, session.assignment_id, lock=True)
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
    refresh(db, session)
    if session.blocking_count or session.warning_count:
        raise ApiProblem(
            422,
            "REVIEW_NOT_READY",
            "红色问题必须清零且黄色问题必须处理",
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
    binding = db.scalar(
        select(AssignmentRubricPublicationBinding)
        .where(
            AssignmentRubricPublicationBinding.review_session_id == session.id,
            AssignmentRubricPublicationBinding.status == "confirmed",
        )
        .with_for_update()
    )
    if binding is None or assignment.due_at is None or assignment.total_score is None:
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
        select(AssignmentPublishReadinessSnapshot).where(
            AssignmentPublishReadinessSnapshot.review_session_id == session.id,
            AssignmentPublishReadinessSnapshot.readiness_hash == ready_hash,
            AssignmentPublishReadinessSnapshot.status == "ready",
            AssignmentPublishReadinessSnapshot.expires_at > now_utc(),
        )
    )
    if old:
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
    refresh(db, session)
    paper = db.get(PaperVersion, snapshot.paper_version_id)
    binding = db.get(AssignmentRubricPublicationBinding, snapshot.binding_id)
    legacy = db.get(RubricVersion, snapshot.legacy_rubric_version_id)
    if (
        not paper
        or not binding
        or not legacy
        or binding.status != "confirmed"
        or session.blocking_count
        or session.warning_count
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
    from app.api.assignments import publish_issues

    assignment.active_paper_version_id, assignment.active_rubric_version_id = paper.id, legacy.id
    issues = publish_issues(db, assignment)
    if issues:
        raise ApiProblem(422, "ASSIGNMENT_INCOMPLETE", "现有发布门禁未通过", {"issues": issues})
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
        },
    )
    db.commit()
    return assignment
