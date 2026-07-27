import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.assignment_generation.answer_rubric import (
    CriterionDraftSchema,
    materialize_reference,
    materialize_rubric,
    question_version,
    validate_candidate_structure,
)
from app.assignment_generation.service import owned_revision
from app.assignment_generation.snapshot import source_snapshot_hash
from app.db.session import get_db
from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentDraftRevision,
    AssignmentRubricCriterionDraft,
    AssignmentRubricDraftCandidate,
    AssignmentRubricValidationResult,
    Question,
    now_utc,
)
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["assignment-answer-rubric-generation"])
Db = Annotated[Session, Depends(get_db)]


class AnswerTeacherValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_content: str = Field(max_length=20000)
    normalized_content: str = Field(max_length=20000)
    structured_content: dict[str, Any] = Field(default_factory=dict)
    alternative_answers: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class AnswerDispositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "modify", "reject", "mark_manual_required"]
    expected_teacher_edit_version: int = Field(ge=0)
    expected_draft_revision_edit_version: int = Field(ge=0)
    expected_question_version: str = Field(min_length=1, max_length=160)
    expected_source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    teacher_value: AnswerTeacherValue | None = None
    review_note: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def require_value_for_modify(self) -> "AnswerDispositionInput":
        if self.action == "modify" and self.teacher_value is None:
            raise ValueError("modify requires teacher_value")
        return self


class RubricTeacherValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=200)
    scoring_mode: Literal["deterministic", "ai_suggestion", "hybrid", "manual_only"] | None = None
    total_points: Decimal | None = Field(None, gt=0, le=1000000)
    allow_partial_credit: bool | None = None
    domain_requirements: dict[str, Any] | None = None
    validation_config: dict[str, Any] | None = None
    common_error_types: list[dict[str, Any]] | None = Field(None, max_length=30)
    feedback_templates: dict[str, Any] | None = None
    criteria: list[CriterionDraftSchema] | None = Field(None, min_length=1, max_length=60)


class RubricDispositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "modify", "reject", "mark_manual_only"]
    expected_teacher_edit_version: int = Field(ge=0)
    expected_draft_revision_edit_version: int = Field(ge=0)
    expected_question_version: str = Field(min_length=1, max_length=160)
    expected_source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    teacher_value: RubricTeacherValue | None = None
    review_note: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def require_value_for_modify(self) -> "RubricDispositionInput":
        if self.action == "modify" and self.teacher_value is None:
            raise ValueError("modify requires teacher_value")
        return self


class EligibleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_revision_edit_version: int = Field(ge=0)
    expected_source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")


def _criteria(db: Session, rubric_id: uuid.UUID) -> list[AssignmentRubricCriterionDraft]:
    return list(
        db.scalars(
            select(AssignmentRubricCriterionDraft)
            .where(AssignmentRubricCriterionDraft.rubric_candidate_id == rubric_id)
            .order_by(AssignmentRubricCriterionDraft.display_order)
        )
    )


def _answer_json(row: AssignmentAnswerDraftCandidate) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "question_id": str(row.question_id),
        "question_version": row.question_version,
        "candidate_version": row.candidate_version,
        "source_type": row.source_type,
        "source_file_analysis_id": str(row.source_file_analysis_id)
        if row.source_file_analysis_id
        else None,
        "source_page_id": str(row.source_page_id) if row.source_page_id else None,
        "source_region": row.source_region,
        "raw_content": row.raw_content,
        "normalized_content": row.normalized_content,
        "structured_content": row.structured_content,
        "alternative_answers": row.alternative_answers,
        "provenance": row.provenance,
        "confidence": float(row.confidence),
        "evidence": row.evidence,
        "warning_codes": row.warning_codes,
        "status": row.status,
        "manual_required": row.manual_required,
        "teacher_edit_version": row.teacher_edit_version,
        "materialized_reference_answer_id": str(row.materialized_reference_answer_id)
        if row.materialized_reference_answer_id
        else None,
        "reviewed_at": row.reviewed_at,
        "review_note": row.review_note,
    }


def _criterion_json(row: AssignmentRubricCriterionDraft) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "criterion_key": row.criterion_key,
        "display_order": row.display_order,
        "title": row.title,
        "description": row.description,
        "points": str(row.points) if row.points is not None else None,
        "criterion_type": row.criterion_type,
        "required": row.required,
        "dependency_keys": row.dependency_keys,
        "alternative_group": row.alternative_group,
        "partial_credit_rule": row.partial_credit_rule,
        "deduction_rule": row.deduction_rule,
        "validation_rule": row.validation_rule,
        "common_error_codes": row.common_error_codes,
        "feedback_template": row.feedback_template,
        "confidence": float(row.confidence),
        "evidence": row.evidence,
        "manual_required": row.manual_required,
    }


def _rubric_json(db: Session, row: AssignmentRubricDraftCandidate) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "question_id": str(row.question_id),
        "question_version": row.question_version,
        "answer_candidate_id": str(row.answer_candidate_id),
        "candidate_version": row.candidate_version,
        "title": row.title,
        "scoring_mode": row.scoring_mode,
        "total_points": str(row.total_points) if row.total_points is not None else None,
        "allow_partial_credit": row.allow_partial_credit,
        "domain_requirements": row.domain_requirements,
        "validation_config": row.validation_config,
        "common_error_types": row.common_error_types,
        "feedback_templates": row.feedback_templates,
        "confidence": float(row.confidence),
        "evidence": row.evidence,
        "warning_codes": row.warning_codes,
        "status": row.status,
        "manual_required": row.manual_required,
        "teacher_edit_version": row.teacher_edit_version,
        "materialized_structured_rubric_id": str(row.materialized_structured_rubric_id)
        if row.materialized_structured_rubric_id
        else None,
        "reviewed_at": row.reviewed_at,
        "review_note": row.review_note,
        "criteria": [_criterion_json(item) for item in _criteria(db, row.id)],
    }


def _owned_answer(
    db: Session, actor_id: uuid.UUID, candidate_id: uuid.UUID, lock: bool = False
) -> AssignmentAnswerDraftCandidate:
    query = select(AssignmentAnswerDraftCandidate).where(
        AssignmentAnswerDraftCandidate.id == candidate_id,
        AssignmentAnswerDraftCandidate.owner_id == actor_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiProblem(404, "ANSWER_CANDIDATE_NOT_FOUND", "答案候选不存在")
    return row


def _owned_rubric(
    db: Session, actor_id: uuid.UUID, candidate_id: uuid.UUID, lock: bool = False
) -> AssignmentRubricDraftCandidate:
    query = select(AssignmentRubricDraftCandidate).where(
        AssignmentRubricDraftCandidate.id == candidate_id,
        AssignmentRubricDraftCandidate.owner_id == actor_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiProblem(404, "RUBRIC_CANDIDATE_NOT_FOUND", "Rubric 候选不存在")
    return row


def _ensure_current(
    db: Session,
    row: AssignmentAnswerDraftCandidate | AssignmentRubricDraftCandidate,
    expected_draft_version: int,
    expected_question_version: str,
    expected_snapshot: str,
) -> tuple[AssignmentDraftRevision, Question]:
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.id == row.draft_revision_id)
        .with_for_update()
    )
    question = db.get(Question, row.question_id)
    assignment = db.get(Assignment, row.assignment_id)
    assert revision is not None and question is not None and assignment is not None
    if revision.teacher_edit_version != expected_draft_version:
        raise ApiProblem(409, "CANDIDATE_EDIT_CONFLICT", "草稿已被其他教师修改")
    if (
        row.question_version != expected_question_version
        or question_version(question) != expected_question_version
    ):
        row.status = "stale"
        db.commit()
        raise ApiProblem(409, "QUESTION_VERSION_STALE", "题目版本已变化")
    if (
        row.source_snapshot_hash != expected_snapshot
        or revision.source_snapshot_hash != expected_snapshot
        or source_snapshot_hash(db, assignment) != expected_snapshot
    ):
        row.status = "stale"
        db.commit()
        raise ApiProblem(409, "CANDIDATE_STALE", "来源快照已变化")
    if assignment.status.value != "draft":
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "只能物化到草稿作业")
    return revision, question


@router.get("/assignment-draft-revisions/{revision_id}/answer-draft-candidates")
def list_answers(
    revision_id: uuid.UUID,
    db: Db,
    actor: Actor,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    owned_revision(db, actor.id, revision_id)
    rows = db.scalars(
        select(AssignmentAnswerDraftCandidate)
        .where(
            AssignmentAnswerDraftCandidate.draft_revision_id == revision_id,
            AssignmentAnswerDraftCandidate.owner_id == actor.id,
        )
        .order_by(
            AssignmentAnswerDraftCandidate.question_id,
            AssignmentAnswerDraftCandidate.candidate_version.desc(),
            AssignmentAnswerDraftCandidate.id,
        )
        .offset(offset)
        .limit(limit)
    )
    return [_answer_json(row) for row in rows]


@router.get("/answer-draft-candidates/{candidate_id}")
def get_answer(candidate_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return _answer_json(_owned_answer(db, actor.id, candidate_id))


@router.get("/answer-draft-candidates/{candidate_id}/evidence")
def get_answer_evidence(candidate_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    row = _owned_answer(db, actor.id, candidate_id)
    return {
        "candidate_id": str(row.id),
        "evidence": row.evidence,
        "provenance": row.provenance,
        "source_region": row.source_region,
    }


@router.patch("/answer-draft-candidates/{candidate_id}/disposition")
def disposition_answer(
    candidate_id: uuid.UUID, data: AnswerDispositionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    row = _owned_answer(db, actor.id, candidate_id, lock=True)
    if data.action in {"accept", "modify"} and row.materialized_reference_answer_id:
        return _answer_json(row)
    if row.teacher_edit_version != data.expected_teacher_edit_version:
        raise ApiProblem(409, "CANDIDATE_EDIT_CONFLICT", "答案候选已被其他教师修改")
    revision, _ = _ensure_current(
        db,
        row,
        data.expected_draft_revision_edit_version,
        data.expected_question_version,
        data.expected_source_snapshot,
    )
    if data.action in {"accept", "modify"} and row.source_type == "unknown":
        raise ApiProblem(422, "ANSWER_SOURCE_UNCONFIRMED", "未知来源答案不能接受为标准答案草稿")
    if data.action == "modify":
        assert data.teacher_value is not None
        row.teacher_value = data.teacher_value.model_dump(mode="json")
        row.alternative_answers = data.teacher_value.alternative_answers
    row.status = {
        "accept": "accepted",
        "modify": "modified",
        "reject": "rejected",
        "mark_manual_required": "manual_required",
    }[data.action]
    row.manual_required = data.action == "mark_manual_required"
    row.reviewed_by, row.reviewed_at, row.review_note = actor.id, now_utc(), data.review_note
    row.teacher_edit_version += 1
    revision.teacher_edit_version += 1
    if data.action in {"accept", "modify"}:
        materialize_reference(db, row, actor.id)
    audit(
        db,
        actor.id,
        data.action,
        "assignment_answer_draft_candidate",
        row.id,
        {
            "source_type": row.source_type,
            "official": row.source_type in {"teacher_official", "publisher_official"},
        },
    )
    db.commit()
    return _answer_json(row)


@router.get("/assignment-draft-revisions/{revision_id}/rubric-draft-candidates")
def list_rubrics(
    revision_id: uuid.UUID,
    db: Db,
    actor: Actor,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    owned_revision(db, actor.id, revision_id)
    rows = db.scalars(
        select(AssignmentRubricDraftCandidate)
        .where(
            AssignmentRubricDraftCandidate.draft_revision_id == revision_id,
            AssignmentRubricDraftCandidate.owner_id == actor.id,
        )
        .order_by(
            AssignmentRubricDraftCandidate.question_id,
            AssignmentRubricDraftCandidate.candidate_version.desc(),
            AssignmentRubricDraftCandidate.id,
        )
        .offset(offset)
        .limit(limit)
    )
    return [_rubric_json(db, row) for row in rows]


@router.get("/rubric-draft-candidates/{candidate_id}")
def get_rubric(candidate_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return _rubric_json(db, _owned_rubric(db, actor.id, candidate_id))


@router.get("/rubric-draft-candidates/{candidate_id}/validation")
def get_validation(candidate_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    row = _owned_rubric(db, actor.id, candidate_id)
    results = db.scalars(
        select(AssignmentRubricValidationResult)
        .where(AssignmentRubricValidationResult.rubric_candidate_id == row.id)
        .order_by(AssignmentRubricValidationResult.created_at.desc())
    )
    return [
        {
            "id": str(item.id),
            "status": item.status,
            "validation_mode": item.validation_mode,
            "deterministic_result": item.deterministic_result,
            "structural_result": item.structural_result,
            "issue_codes": item.issue_codes,
            "validator_version": item.validator_version,
            "completed_at": item.completed_at,
        }
        for item in results
    ]


def _apply_rubric_value(
    db: Session, row: AssignmentRubricDraftCandidate, value: RubricTeacherValue
) -> None:
    for name in (
        "title",
        "scoring_mode",
        "total_points",
        "allow_partial_credit",
        "domain_requirements",
        "validation_config",
        "common_error_types",
        "feedback_templates",
    ):
        selected = getattr(value, name)
        if selected is not None:
            setattr(row, name, selected)
    if value.criteria is not None:
        for old in _criteria(db, row.id):
            db.delete(old)
        db.flush()
        for order, criterion in enumerate(value.criteria):
            data = criterion.model_dump(exclude={"evidence"})
            db.add(
                AssignmentRubricCriterionDraft(
                    rubric_candidate_id=row.id,
                    display_order=order,
                    **data,
                    evidence=[item.model_dump() for item in criterion.evidence],
                )
            )


@router.patch("/rubric-draft-candidates/{candidate_id}/disposition")
def disposition_rubric(
    candidate_id: uuid.UUID, data: RubricDispositionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    row = _owned_rubric(db, actor.id, candidate_id, lock=True)
    if data.action in {"accept", "modify"} and row.materialized_structured_rubric_id:
        return _rubric_json(db, row)
    if row.teacher_edit_version != data.expected_teacher_edit_version:
        raise ApiProblem(409, "CANDIDATE_EDIT_CONFLICT", "Rubric 候选已被其他教师修改")
    revision, _ = _ensure_current(
        db,
        row,
        data.expected_draft_revision_edit_version,
        data.expected_question_version,
        data.expected_source_snapshot,
    )
    if data.action == "modify":
        assert data.teacher_value is not None
        row.teacher_value = data.teacher_value.model_dump(mode="json")
        _apply_rubric_value(db, row, data.teacher_value)
    if data.action == "mark_manual_only":
        row.scoring_mode, row.manual_required = "manual_only", True
    row.status = {
        "accept": "accepted",
        "modify": "modified",
        "reject": "rejected",
        "mark_manual_only": "manual_required",
    }[data.action]
    row.reviewed_by, row.reviewed_at, row.review_note = actor.id, now_utc(), data.review_note
    row.teacher_edit_version += 1
    revision.teacher_edit_version += 1
    if data.action in {"accept", "modify"}:
        answer = db.get(AssignmentAnswerDraftCandidate, row.answer_candidate_id)
        if answer is None or answer.status not in {"accepted", "modified"}:
            raise ApiProblem(422, "ANSWER_CANDIDATE_NOT_ACCEPTED", "必须先接受标准答案草稿")
        try:
            materialize_rubric(db, row, actor.id)
        except ValueError as exc:
            raise ApiProblem(422, str(exc), "Rubric 结构或分值仍需修正") from exc
    audit(
        db,
        actor.id,
        data.action,
        "assignment_rubric_draft_candidate",
        row.id,
        {"scoring_mode": row.scoring_mode, "draft_only": True},
    )
    db.commit()
    return _rubric_json(db, row)


def _eligible_answer(row: AssignmentAnswerDraftCandidate) -> bool:
    blocked = {
        "PROMPT_INJECTION_CONTENT_DETECTED",
        "FORMULA_ANSWER_REVIEW_REQUIRED",
        "MANUAL_ANSWER_REQUIRED",
        "ANSWER_SCHEMA_INVALID",
    }
    return (
        row.status == "suggested"
        and row.source_type != "unknown"
        and float(row.confidence) >= 0.8
        and not row.manual_required
        and bool(row.evidence)
        and not blocked.intersection(row.warning_codes)
    )


@router.post("/assignment-draft-revisions/{revision_id}/answer-draft-candidates/accept-eligible")
def accept_eligible_answers(
    revision_id: uuid.UUID, data: EligibleInput, db: Db, actor: Actor
) -> dict[str, Any]:
    revision = owned_revision(db, actor.id, revision_id, for_update=True)
    if (
        revision.teacher_edit_version != data.expected_draft_revision_edit_version
        or revision.source_snapshot_hash != data.expected_source_snapshot
    ):
        raise ApiProblem(409, "CANDIDATE_EDIT_CONFLICT", "草稿版本已变化")
    accepted: list[str] = []
    for row in db.scalars(
        select(AssignmentAnswerDraftCandidate)
        .where(
            AssignmentAnswerDraftCandidate.draft_revision_id == revision.id,
            AssignmentAnswerDraftCandidate.owner_id == actor.id,
        )
        .with_for_update()
    ).all():
        if _eligible_answer(row):
            row.status, row.reviewed_by, row.reviewed_at = "accepted", actor.id, now_utc()
            row.teacher_edit_version += 1
            materialize_reference(db, row, actor.id)
            accepted.append(str(row.id))
            audit(
                db,
                actor.id,
                "accept_eligible",
                "assignment_answer_draft_candidate",
                row.id,
                {"source_type": row.source_type},
            )
    revision.teacher_edit_version += len(accepted)
    db.commit()
    return {
        "accepted_ids": accepted,
        "accepted_count": len(accepted),
        "source_labels_unchanged": True,
    }


@router.post("/assignment-draft-revisions/{revision_id}/rubric-draft-candidates/accept-eligible")
def accept_eligible_rubrics(
    revision_id: uuid.UUID, data: EligibleInput, db: Db, actor: Actor
) -> dict[str, Any]:
    revision = owned_revision(db, actor.id, revision_id, for_update=True)
    if (
        revision.teacher_edit_version != data.expected_draft_revision_edit_version
        or revision.source_snapshot_hash != data.expected_source_snapshot
    ):
        raise ApiProblem(409, "CANDIDATE_EDIT_CONFLICT", "草稿版本已变化")
    accepted: list[str] = []
    rows = db.scalars(
        select(AssignmentRubricDraftCandidate)
        .where(
            AssignmentRubricDraftCandidate.draft_revision_id == revision.id,
            AssignmentRubricDraftCandidate.owner_id == actor.id,
            AssignmentRubricDraftCandidate.status == "suggested",
        )
        .with_for_update()
    ).all()
    for row in rows:
        answer = db.get(AssignmentAnswerDraftCandidate, row.answer_candidate_id)
        criteria = _criteria(db, row.id)
        structural = validate_candidate_structure(
            Decimal(row.total_points) if row.total_points is not None else None,
            row.scoring_mode,
            criteria,
        )
        validations = list(
            db.scalars(
                select(AssignmentRubricValidationResult.status).where(
                    AssignmentRubricValidationResult.rubric_candidate_id == row.id
                )
            )
        )
        if (
            answer
            and answer.status in {"accepted", "modified"}
            and structural.valid
            and not row.manual_required
            and row.scoring_mode == "deterministic"
            and "indeterminate" not in validations
        ):
            row.status, row.reviewed_by, row.reviewed_at = "accepted", actor.id, now_utc()
            row.teacher_edit_version += 1
            materialize_rubric(db, row, actor.id)
            accepted.append(str(row.id))
            audit(
                db,
                actor.id,
                "accept_eligible",
                "assignment_rubric_draft_candidate",
                row.id,
                {"scoring_mode": row.scoring_mode},
            )
    revision.teacher_edit_version += len(accepted)
    db.commit()
    return {"accepted_ids": accepted, "accepted_count": len(accepted)}
