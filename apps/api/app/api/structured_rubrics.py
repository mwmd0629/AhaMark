import hashlib
import json
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.db.session import get_db
from app.math_validation.stale import stale_for_question
from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentDraftRevision,
    AssignmentRubricDraftCandidate,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    now_utc,
)
from app.question_versions import question_version_token
from app.rubrics.validation import validate_rubric
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["structured-rubrics"])
Db = Annotated[Session, Depends(get_db)]


class ReferenceInput(BaseModel):
    source_type: Literal[
        "teacher_official",
        "publisher_official",
        "teacher_provided",
        "third_party",
        "ai_generated",
        "unknown",
        "teacher_authored",
        "official_solution",
        "imported_reference",
        "ai_draft",
        "other",
    ]
    source_file: str | None = Field(None, max_length=512)
    source_page: int | None = Field(None, ge=1)
    source_region: dict[str, Any] = Field(default_factory=dict)
    raw_content: str = Field(max_length=20000)
    normalized_content: str = Field(max_length=20000)
    structured_content: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class CriterionInput(BaseModel):
    stable_key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    description: str | None = None
    max_points: Decimal = Field(ge=0)
    criterion_type: str
    required: bool = True
    dependencies: list[str] = Field(default_factory=list)
    expected_evidence: dict[str, Any] = Field(default_factory=dict)
    validation_mode: str
    manual_review_policy: dict[str, Any] = Field(default_factory=dict)
    partial_credit_policy: dict[str, Any] = Field(default_factory=dict)
    error_category: str | None = None
    validation_rule: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RubricInput(BaseModel):
    reference_answer_version_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    total_points: Decimal = Field(gt=0)
    criteria: list[CriterionInput] = Field(min_length=1)


class ConfirmQuestionPackageInput(BaseModel):
    expected_bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_question_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_answer_version_id: uuid.UUID
    expected_reference_answer_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    structured_rubric_version_id: uuid.UUID
    expected_structured_rubric_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    explicit_confirmation: Literal[True]


class ConfirmAllQuestionPackageItem(BaseModel):
    question_id: uuid.UUID
    expected_question_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_answer_version_id: uuid.UUID
    expected_reference_answer_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    structured_rubric_version_id: uuid.UUID
    expected_structured_rubric_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConfirmAllQuestionPackagesInput(BaseModel):
    expected_bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    packages: list[ConfirmAllQuestionPackageItem] = Field(min_length=1)
    explicit_confirmation: Literal[True]


class ConfirmAllCandidatePackageItem(BaseModel):
    question_id: uuid.UUID
    expected_question_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_candidate_id: uuid.UUID
    expected_answer_candidate_edit_version: int = Field(ge=0)
    expected_answer_question_version: str = Field(min_length=1, max_length=160)
    rubric_candidate_id: uuid.UUID
    expected_rubric_candidate_edit_version: int = Field(ge=0)
    expected_rubric_question_version: str = Field(min_length=1, max_length=160)


class ConfirmAllCandidatePackagesInput(BaseModel):
    expected_bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_draft_revision_id: uuid.UUID
    expected_draft_revision_edit_version: int = Field(ge=0)
    expected_source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    packages: list[ConfirmAllCandidatePackageItem] = Field(min_length=1)
    explicit_confirmation: Literal[True]


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _owned_question(db: Session, owner_id: uuid.UUID, question_id: uuid.UUID) -> Question:
    item = db.scalar(
        select(Question)
        .join(PaperVersion, PaperVersion.id == Question.paper_version_id)
        .join(Assignment, Assignment.id == PaperVersion.assignment_id)
        .where(Question.id == question_id, Assignment.owner_id == owner_id)
    )
    if item is None:
        raise ApiProblem(404, "QUESTION_NOT_FOUND", "题目不存在")
    return item


def _reference_json(item: ReferenceAnswerVersion) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "question_id": str(item.question_id),
        "version": item.version,
        "status": item.status,
        "source_type": item.source_type,
        "source_file": item.source_file,
        "source_page": item.source_page,
        "source_region": item.source_region,
        "raw_content": item.raw_content,
        "normalized_content": item.normalized_content,
        "structured_content": item.structured_content,
        "provenance": item.provenance,
        "content_hash": item.content_hash,
        "teacher_confirmed_at": item.teacher_confirmed_at,
    }


@router.get("/questions/{question_id}/reference-answers")
def list_references(question_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    _owned_question(db, actor.id, question_id)
    return [
        _reference_json(item)
        for item in db.scalars(
            select(ReferenceAnswerVersion)
            .where(ReferenceAnswerVersion.question_id == question_id)
            .order_by(ReferenceAnswerVersion.version.desc())
        )
    ]


@router.post("/questions/{question_id}/reference-answers", status_code=201)
def create_reference(
    question_id: uuid.UUID, payload: ReferenceInput, db: Db, actor: Actor
) -> dict[str, Any]:
    _owned_question(db, actor.id, question_id)
    # Serialize per-question version allocation. Locking an aggregate query does
    # not protect the empty-set case on PostgreSQL.
    db.scalar(select(Question.id).where(Question.id == question_id).with_for_update())
    version = (
        db.scalar(
            select(func.coalesce(func.max(ReferenceAnswerVersion.version), 0)).where(
                ReferenceAnswerVersion.question_id == question_id
            )
        )
        or 0
    ) + 1
    data = payload.model_dump(mode="json")
    item = ReferenceAnswerVersion(
        question_id=question_id,
        version=version,
        created_by=actor.id,
        content_hash=_hash(data),
        **data,
    )
    db.add(item)
    db.flush()
    audit(db, actor.id, "create", "reference_answer_version", item.id, {"version": version})
    db.commit()
    return _reference_json(item)


@router.put("/reference-answers/{reference_id}")
def update_reference(
    reference_id: uuid.UUID, payload: ReferenceInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = db.scalar(
        select(ReferenceAnswerVersion)
        .where(ReferenceAnswerVersion.id == reference_id)
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "REFERENCE_NOT_FOUND", "标准答案不存在")
    _owned_question(db, actor.id, item.question_id)
    if item.status != "draft":
        raise ApiProblem(409, "CONFIRMED_IMMUTABLE", "已确认标准答案不可原地修改")
    data = payload.model_dump(mode="json")
    for key, value in data.items():
        setattr(item, key, value)
    item.content_hash = _hash(data)
    audit(db, actor.id, "update", "reference_answer_version", item.id)
    db.commit()
    return _reference_json(item)


@router.post("/reference-answers/{reference_id}/confirm")
def confirm_reference(reference_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = db.scalar(
        select(ReferenceAnswerVersion)
        .where(ReferenceAnswerVersion.id == reference_id)
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "REFERENCE_NOT_FOUND", "标准答案不存在")
    _owned_question(db, actor.id, item.question_id)
    if item.status != "draft":
        raise ApiProblem(409, "REFERENCE_NOT_DRAFT", "只有草稿可确认")
    if item.source_type == "unknown":
        raise ApiProblem(422, "ANSWER_SOURCE_UNCONFIRMED", "未知来源答案不能确认")
    item.status = "confirmed"
    item.teacher_confirmed_at = now_utc()
    stale_for_question(db, item.question_id, "REFERENCE_ANSWER_CONFIRMED")
    audit(db, actor.id, "confirm", "reference_answer_version", item.id)
    db.commit()
    return _reference_json(item)


def _criteria(db: Session, rubric_id: uuid.UUID) -> list[RubricCriterion]:
    return list(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == rubric_id)
            .order_by(RubricCriterion.display_order)
        )
    )


def _rubric_json(db: Session, item: StructuredRubricVersion) -> dict[str, Any]:
    criteria = _criteria(db, item.id)
    return {
        "id": str(item.id),
        "question_id": str(item.question_id),
        "question_version": item.question_version,
        "reference_answer_version_id": str(item.reference_answer_version_id),
        "rubric_version": item.rubric_version,
        "title": item.title,
        "total_points": str(item.total_points),
        "status": item.status,
        "content_hash": item.content_hash,
        "confirmed_at": item.confirmed_at,
        "criteria": [
            {
                "id": str(c.id),
                "stable_key": c.stable_key,
                "title": c.title,
                "description": c.description,
                "max_points": str(c.max_points),
                "order": c.display_order,
                "criterion_type": c.criterion_type,
                "required": c.required,
                "dependencies": c.dependencies,
                "expected_evidence": c.expected_evidence,
                "validation_mode": c.validation_mode,
                "manual_review_policy": c.manual_review_policy,
                "partial_credit_policy": c.partial_credit_policy,
                "error_category": c.error_category,
                "validation_rule": c.validation_rule,
                "metadata": c.metadata_,
            }
            for c in criteria
        ],
    }


def _replace_criteria(
    db: Session, rubric: StructuredRubricVersion, payload: RubricInput
) -> list[dict[str, Any]]:
    raw = [item.model_dump(mode="json") for item in payload.criteria]
    errors = validate_rubric(payload.total_points, raw)
    if errors:
        return errors
    for old in _criteria(db, rubric.id):
        db.delete(old)
    for order, value in enumerate(payload.criteria):
        data = value.model_dump(mode="json")
        metadata = data.pop("metadata")
        db.add(
            RubricCriterion(
                rubric_version_id=rubric.id,
                display_order=order,
                metadata_=metadata,
                **data,
            )
        )
    return []


@router.get("/questions/{question_id}/structured-rubrics")
def list_rubrics(question_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    _owned_question(db, actor.id, question_id)
    return [
        _rubric_json(db, item)
        for item in db.scalars(
            select(StructuredRubricVersion)
            .where(StructuredRubricVersion.question_id == question_id)
            .order_by(StructuredRubricVersion.rubric_version.desc())
        )
    ]


@router.post("/questions/{question_id}/structured-rubrics", status_code=201)
def create_rubric(
    question_id: uuid.UUID, payload: RubricInput, db: Db, actor: Actor
) -> dict[str, Any]:
    question = _owned_question(db, actor.id, question_id)
    db.scalar(select(Question.id).where(Question.id == question_id).with_for_update())
    reference = db.get(ReferenceAnswerVersion, payload.reference_answer_version_id)
    if reference is None or reference.question_id != question_id or reference.status != "confirmed":
        raise ApiProblem(422, "REFERENCE_NOT_CONFIRMED", "Rubric 必须绑定已确认标准答案")
    if question.max_score is None or Decimal(question.max_score) != payload.total_points:
        raise ApiProblem(422, "RUBRIC_POINTS_MISMATCH", "Rubric 总分必须与题目满分一致")
    raw = payload.model_dump(mode="json")
    errors = validate_rubric(payload.total_points, raw["criteria"])
    if errors:
        raise ApiProblem(422, "RUBRIC_INVALID", "Rubric 校验失败", {"errors": errors})
    version = (
        db.scalar(
            select(func.coalesce(func.max(StructuredRubricVersion.rubric_version), 0)).where(
                StructuredRubricVersion.question_id == question_id
            )
        )
        or 0
    ) + 1
    item = StructuredRubricVersion(
        question_id=question_id,
        question_version=question_version_token(question),
        reference_answer_version_id=payload.reference_answer_version_id,
        rubric_version=version,
        title=payload.title,
        total_points=payload.total_points,
        status="draft",
        content_hash=_hash(raw),
        created_by=actor.id,
    )
    db.add(item)
    db.flush()
    errors = _replace_criteria(db, item, payload)
    assert not errors
    audit(db, actor.id, "create", "structured_rubric_version", item.id, {"version": version})
    db.commit()
    return _rubric_json(db, item)


@router.put("/structured-rubrics/{rubric_id}")
def update_rubric(
    rubric_id: uuid.UUID, payload: RubricInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = db.scalar(
        select(StructuredRubricVersion)
        .where(StructuredRubricVersion.id == rubric_id)
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "RUBRIC_NOT_FOUND", "Rubric 不存在")
    _owned_question(db, actor.id, item.question_id)
    if item.status != "draft":
        raise ApiProblem(409, "CONFIRMED_IMMUTABLE", "已确认 Rubric 不可原地修改")
    errors = _replace_criteria(db, item, payload)
    if errors:
        raise ApiProblem(422, "RUBRIC_INVALID", "Rubric 校验失败", {"errors": errors})
    item.title = payload.title
    item.total_points = payload.total_points
    item.reference_answer_version_id = payload.reference_answer_version_id
    item.content_hash = _hash(payload.model_dump(mode="json"))
    audit(db, actor.id, "update", "structured_rubric_version", item.id)
    db.commit()
    return _rubric_json(db, item)


@router.post("/structured-rubrics/{rubric_id}/validate")
def validate_endpoint(rubric_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = db.scalar(
        select(StructuredRubricVersion)
        .where(StructuredRubricVersion.id == rubric_id)
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "RUBRIC_NOT_FOUND", "Rubric 不存在")
    _owned_question(db, actor.id, item.question_id)
    raw = [
        {
            "stable_key": c.stable_key,
            "max_points": c.max_points,
            "criterion_type": c.criterion_type,
            "dependencies": c.dependencies,
            "validation_mode": c.validation_mode,
            "validation_rule": c.validation_rule,
        }
        for c in _criteria(db, item.id)
    ]
    errors = validate_rubric(Decimal(item.total_points), raw)
    return {"valid": not errors, "errors": errors}


@router.post("/structured-rubrics/{rubric_id}/confirm")
def confirm_rubric(rubric_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = db.scalar(
        select(StructuredRubricVersion)
        .where(StructuredRubricVersion.id == rubric_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "RUBRIC_NOT_FOUND", "Rubric 不存在")
    _owned_question(db, actor.id, item.question_id)
    result = validate_endpoint(rubric_id, db, actor)
    if not result["valid"]:
        raise ApiProblem(422, "RUBRIC_INVALID", "Rubric 校验失败", result)
    if item.status != "draft":
        raise ApiProblem(409, "RUBRIC_NOT_DRAFT", "只有草稿可确认")
    question = _owned_question(db, actor.id, item.question_id)
    reference = db.get(ReferenceAnswerVersion, item.reference_answer_version_id)
    if reference is None or reference.status != "confirmed":
        raise ApiProblem(422, "REFERENCE_NOT_CONFIRMED", "确认 Rubric 前必须先确认标准答案")
    if question.max_score is None or Decimal(question.max_score) != Decimal(item.total_points):
        raise ApiProblem(422, "RUBRIC_POINTS_MISMATCH", "Rubric 总分必须与题目满分一致")
    item.status, item.confirmed_by, item.confirmed_at = "confirmed", actor.id, now_utc()
    stale_for_question(db, item.question_id, "RUBRIC_VERSION_CONFIRMED")
    audit(db, actor.id, "confirm", "structured_rubric_version", item.id)
    db.commit()
    return _rubric_json(db, item)


def _confirm_question_package_against_bundle(
    assignment_id: uuid.UUID,
    question_id: uuid.UUID,
    payload: ConfirmQuestionPackageInput,
    db: Session,
    actor: Actor,
    current_bundle: dict[str, Any],
) -> tuple[ReferenceAnswerVersion, StructuredRubricVersion, bool]:
    question = _owned_question(db, actor.id, question_id)
    paper = db.get(PaperVersion, question.paper_version_id)
    if paper is None or paper.assignment_id != assignment_id:
        raise ApiProblem(404, "QUESTION_NOT_FOUND", "题目不存在")
    locked_question = db.scalar(
        select(Question)
        .where(Question.id == question_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if locked_question is None:
        raise ApiProblem(404, "QUESTION_NOT_FOUND", "题目不存在")
    question = locked_question
    reference = db.scalar(
        select(ReferenceAnswerVersion)
        .where(ReferenceAnswerVersion.id == payload.reference_answer_version_id)
        .with_for_update()
    )
    rubric = db.scalar(
        select(StructuredRubricVersion)
        .where(StructuredRubricVersion.id == payload.structured_rubric_version_id)
        .with_for_update()
    )
    if reference is None or reference.question_id != question_id:
        raise ApiProblem(404, "REFERENCE_NOT_FOUND", "该题标准答案不存在")
    if rubric is None or rubric.question_id != question_id:
        raise ApiProblem(404, "RUBRIC_NOT_FOUND", "该题评分标准不存在")
    criteria = list(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == rubric.id)
            .order_by(RubricCriterion.display_order)
            .with_for_update()
        )
    )
    bundle_hash_matches = current_bundle["version"]["bundle_hash"] == payload.expected_bundle_hash
    bundle_question = next(
        (item for item in current_bundle["questions"] if item["id"] == str(question_id)),
        None,
    )
    if (
        bundle_question is None
        or bundle_question["content_hash"] != payload.expected_question_content_hash
    ):
        raise ApiProblem(409, "QUESTION_CONTENT_STALE", "题目内容已变化，请刷新后重试")
    bundle_reference = (
        bundle_question["answer"]["materialized"]
        if bundle_question["answer"]["materialized"] is not None
        and bundle_question["answer"]["materialized"]["status"] == "draft"
        else bundle_question["answer"]["selected"]
    )
    bundle_rubric = (
        bundle_question["rubric"]["materialized"]
        if bundle_question["rubric"]["materialized"] is not None
        and bundle_question["rubric"]["materialized"]["status"] == "draft"
        else bundle_question["rubric"]["selected"]
    )
    if (
        bundle_reference is None
        or bundle_reference["id"] != str(payload.reference_answer_version_id)
        or bundle_reference["content_hash"] != payload.expected_reference_answer_content_hash
        or bundle_rubric is None
        or bundle_rubric["id"] != str(payload.structured_rubric_version_id)
        or bundle_rubric["content_hash"] != payload.expected_structured_rubric_content_hash
    ):
        raise ApiProblem(409, "QUESTION_PACKAGE_STALE", "答案或评分标准已变化，请刷新后重试")
    if not bundle_hash_matches and not (
        bundle_reference["status"] == "confirmed" and bundle_rubric["status"] == "confirmed"
    ):
        raise ApiProblem(409, "REVIEW_BUNDLE_STALE", "审查内容已变化，请刷新后重试")
    if rubric.reference_answer_version_id != reference.id:
        raise ApiProblem(
            409, "ANSWER_RUBRIC_BINDING_STALE", "答案与评分标准绑定已变化，请刷新后重试"
        )
    if reference.status not in {"draft", "confirmed"}:
        raise ApiProblem(409, "REFERENCE_NOT_CONFIRMABLE", "当前标准答案不可确认")
    if rubric.status not in {"draft", "confirmed"}:
        raise ApiProblem(409, "RUBRIC_NOT_CONFIRMABLE", "当前评分标准不可确认")
    if reference.source_type == "unknown":
        raise ApiProblem(422, "ANSWER_SOURCE_UNCONFIRMED", "未知来源答案不能确认")
    raw = [
        {
            "stable_key": criterion.stable_key,
            "max_points": criterion.max_points,
            "criterion_type": criterion.criterion_type,
            "dependencies": criterion.dependencies,
            "validation_mode": criterion.validation_mode,
            "validation_rule": criterion.validation_rule,
        }
        for criterion in criteria
    ]
    errors = validate_rubric(Decimal(rubric.total_points), raw)
    if errors:
        raise ApiProblem(422, "RUBRIC_INVALID", "Rubric 校验失败", {"errors": errors})
    if question.max_score is None or Decimal(question.max_score) != Decimal(rubric.total_points):
        raise ApiProblem(422, "RUBRIC_POINTS_MISMATCH", "Rubric 总分必须与题目满分一致")

    now = now_utc()
    changed = False
    if reference.status == "draft":
        reference.status = "confirmed"
        reference.teacher_confirmed_at = now
        audit(db, actor.id, "confirm", "reference_answer_version", reference.id)
        changed = True
    if rubric.status == "draft":
        rubric.status, rubric.confirmed_by, rubric.confirmed_at = "confirmed", actor.id, now
        audit(db, actor.id, "confirm", "structured_rubric_version", rubric.id)
        changed = True
    if changed:
        stale_for_question(db, question_id, "QUESTION_ANSWER_RUBRIC_CONFIRMED")
    if changed:
        audit(
            db,
            actor.id,
            "confirm_question_package",
            "question",
            question_id,
            {
                "reference_answer_version_id": str(reference.id),
                "structured_rubric_version_id": str(rubric.id),
                "bundle_hash": payload.expected_bundle_hash,
            },
        )
    return reference, rubric, not changed


@router.post("/assignments/{assignment_id}/questions/{question_id}/confirm-answer-rubric")
def confirm_question_package(
    assignment_id: uuid.UUID,
    question_id: uuid.UUID,
    payload: ConfirmQuestionPackageInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    """Confirm one question's exact answer/rubric pair atomically."""
    from app.api.assignment_central_review import owned_assignment, review_bundle

    owned_assignment(db, actor.id, assignment_id, lock=True)
    current_bundle = review_bundle(db, actor.id, assignment_id)
    reference, rubric, already_confirmed = _confirm_question_package_against_bundle(
        assignment_id, question_id, payload, db, actor, current_bundle
    )
    db.commit()
    return {
        "answer": _reference_json(reference),
        "rubric": _rubric_json(db, rubric),
        "already_confirmed": already_confirmed,
    }


@router.post("/assignments/{assignment_id}/confirm-all-answer-rubrics")
def confirm_all_question_packages(
    assignment_id: uuid.UUID,
    payload: ConfirmAllQuestionPackagesInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    """Confirm every current question package in one transaction."""
    from app.api.assignment_central_review import owned_assignment, review_bundle

    owned_assignment(db, actor.id, assignment_id, lock=True)
    current_bundle = review_bundle(db, actor.id, assignment_id)
    if current_bundle["version"]["bundle_hash"] != payload.expected_bundle_hash:
        raise ApiProblem(409, "REVIEW_BUNDLE_STALE", "审查内容已变化，请刷新后重试")

    expected_question_ids = {item["id"] for item in current_bundle["questions"]}
    submitted_question_ids = [str(item.question_id) for item in payload.packages]
    if len(set(submitted_question_ids)) != len(submitted_question_ids):
        raise ApiProblem(422, "DUPLICATE_QUESTION_PACKAGE", "同一道题不能重复确认")
    if set(submitted_question_ids) != expected_question_ids:
        raise ApiProblem(409, "QUESTION_PACKAGE_SET_STALE", "题目集合已变化，请刷新后重试")

    results: list[tuple[ReferenceAnswerVersion, StructuredRubricVersion, bool]] = []
    for item in sorted(payload.packages, key=lambda value: str(value.question_id)):
        results.append(
            _confirm_question_package_against_bundle(
                assignment_id,
                item.question_id,
                ConfirmQuestionPackageInput(
                    expected_bundle_hash=payload.expected_bundle_hash,
                    expected_question_content_hash=item.expected_question_content_hash,
                    reference_answer_version_id=item.reference_answer_version_id,
                    expected_reference_answer_content_hash=(
                        item.expected_reference_answer_content_hash
                    ),
                    structured_rubric_version_id=item.structured_rubric_version_id,
                    expected_structured_rubric_content_hash=(
                        item.expected_structured_rubric_content_hash
                    ),
                    explicit_confirmation=True,
                ),
                db,
                actor,
                current_bundle,
            )
        )
    db.commit()
    return {
        "confirmed_count": sum(not already for _, _, already in results),
        "already_confirmed_count": sum(already for _, _, already in results),
        "packages": [
            {
                "answer": _reference_json(reference),
                "rubric": _rubric_json(db, rubric),
                "already_confirmed": already,
            }
            for reference, rubric, already in results
        ],
    }


@router.post("/assignments/{assignment_id}/confirm-all-candidate-answer-rubrics")
def confirm_all_candidate_question_packages(
    assignment_id: uuid.UUID,
    payload: ConfirmAllCandidatePackagesInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    """Accept, materialize, and confirm every displayed candidate package atomically."""
    from app.api.assignment_answer_rubric import (
        _materialize_reference_or_conflict,
        _materialize_rubric_or_conflict,
    )
    from app.api.assignment_central_review import owned_assignment, review_bundle

    owned_assignment(db, actor.id, assignment_id, lock=True)
    current_bundle = review_bundle(db, actor.id, assignment_id)
    if current_bundle["version"]["bundle_hash"] != payload.expected_bundle_hash:
        raise ApiProblem(409, "REVIEW_BUNDLE_STALE", "审查内容已变化，请刷新后重试")
    if current_bundle["version"]["draft_revision_id"] != str(payload.expected_draft_revision_id):
        raise ApiProblem(409, "DRAFT_REVISION_STALE", "生成草稿已变化，请刷新后重试")

    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(
            AssignmentDraftRevision.id == payload.expected_draft_revision_id,
            AssignmentDraftRevision.assignment_id == assignment_id,
            AssignmentDraftRevision.owner_id == actor.id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if (
        revision is None
        or revision.teacher_edit_version != payload.expected_draft_revision_edit_version
        or revision.source_snapshot_hash != payload.expected_source_snapshot_hash
    ):
        raise ApiProblem(409, "CANDIDATE_EDIT_CONFLICT", "生成草稿已变化，请刷新后重试")

    expected_question_ids = {item["id"] for item in current_bundle["questions"]}
    submitted_question_ids = [str(item.question_id) for item in payload.packages]
    if len(set(submitted_question_ids)) != len(submitted_question_ids):
        raise ApiProblem(422, "DUPLICATE_QUESTION_PACKAGE", "同一道题不能重复确认")
    if set(submitted_question_ids) != expected_question_ids:
        raise ApiProblem(409, "QUESTION_PACKAGE_SET_STALE", "题目集合已变化，请刷新后重试")

    now = now_utc()
    answers: dict[uuid.UUID, AssignmentAnswerDraftCandidate] = {}
    rubrics: dict[uuid.UUID, AssignmentRubricDraftCandidate] = {}
    bundle_questions = {item["id"]: item for item in current_bundle["questions"]}
    for item in sorted(payload.packages, key=lambda value: str(value.question_id)):
        bundle_question = bundle_questions[str(item.question_id)]
        if bundle_question["content_hash"] != item.expected_question_content_hash:
            raise ApiProblem(409, "QUESTION_CONTENT_STALE", "题目内容已变化，请刷新后重试")
        if (
            bundle_question["answer"]["candidate"] is None
            or bundle_question["answer"]["candidate"]["id"] != str(item.answer_candidate_id)
            or bundle_question["rubric"]["candidate"] is None
            or bundle_question["rubric"]["candidate"]["id"] != str(item.rubric_candidate_id)
        ):
            raise ApiProblem(409, "QUESTION_PACKAGE_STALE", "答案或评分标准已变化，请刷新后重试")
        answer = db.scalar(
            select(AssignmentAnswerDraftCandidate)
            .where(AssignmentAnswerDraftCandidate.id == item.answer_candidate_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        rubric = db.scalar(
            select(AssignmentRubricDraftCandidate)
            .where(AssignmentRubricDraftCandidate.id == item.rubric_candidate_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if (
            answer is None
            or answer.owner_id != actor.id
            or answer.draft_revision_id != revision.id
            or answer.question_id != item.question_id
            or answer.teacher_edit_version != item.expected_answer_candidate_edit_version
            or answer.question_version != item.expected_answer_question_version
            or answer.source_snapshot_hash != payload.expected_source_snapshot_hash
            or answer.status not in {"suggested", "manual_required", "accepted", "modified"}
        ):
            raise ApiProblem(409, "ANSWER_CANDIDATE_STALE", "答案候选已变化，请刷新后重试")
        if answer.source_type == "unknown":
            raise ApiProblem(422, "ANSWER_SOURCE_UNCONFIRMED", "未知来源答案不能确认")
        if (
            rubric is None
            or rubric.owner_id != actor.id
            or rubric.draft_revision_id != revision.id
            or rubric.question_id != item.question_id
            or rubric.answer_candidate_id != answer.id
            or rubric.teacher_edit_version != item.expected_rubric_candidate_edit_version
            or rubric.question_version != item.expected_rubric_question_version
            or rubric.source_snapshot_hash != payload.expected_source_snapshot_hash
            or rubric.status not in {"suggested", "manual_required", "accepted", "modified"}
        ):
            raise ApiProblem(409, "RUBRIC_CANDIDATE_STALE", "评分标准候选已变化，请刷新后重试")
        answers[item.question_id] = answer
        rubrics[item.question_id] = rubric

    changed_candidates = 0
    for question_id in sorted(answers, key=str):
        answer = answers[question_id]
        if answer.status not in {"accepted", "modified"}:
            answer.status, answer.reviewed_by, answer.reviewed_at = "accepted", actor.id, now
            answer.teacher_edit_version += 1
            changed_candidates += 1
        _materialize_reference_or_conflict(db, answer, actor.id)
    db.flush()
    for question_id in sorted(rubrics, key=str):
        rubric = rubrics[question_id]
        if rubric.status not in {"accepted", "modified"}:
            rubric.status, rubric.reviewed_by, rubric.reviewed_at = "accepted", actor.id, now
            rubric.teacher_edit_version += 1
            changed_candidates += 1
        _materialize_rubric_or_conflict(db, rubric, actor.id)
    revision.teacher_edit_version += changed_candidates
    db.flush()

    materialized_bundle = review_bundle(db, actor.id, assignment_id)
    results: list[tuple[ReferenceAnswerVersion, StructuredRubricVersion, bool]] = []
    for item in sorted(payload.packages, key=lambda value: str(value.question_id)):
        answer = answers[item.question_id]
        rubric = rubrics[item.question_id]
        if (
            answer.materialized_reference_answer_id is None
            or rubric.materialized_structured_rubric_id is None
        ):
            raise ApiProblem(409, "QUESTION_PACKAGE_NOT_MATERIALIZED", "整套内容尚未准备完成")
        bundle_question = next(
            row for row in materialized_bundle["questions"] if row["id"] == str(item.question_id)
        )
        bundle_answer = bundle_question["answer"]["materialized"]
        bundle_rubric = bundle_question["rubric"]["materialized"]
        if bundle_answer is None or bundle_rubric is None:
            raise ApiProblem(409, "QUESTION_PACKAGE_NOT_MATERIALIZED", "整套内容尚未准备完成")
        results.append(
            _confirm_question_package_against_bundle(
                assignment_id,
                item.question_id,
                ConfirmQuestionPackageInput(
                    expected_bundle_hash=materialized_bundle["version"]["bundle_hash"],
                    expected_question_content_hash=bundle_question["content_hash"],
                    reference_answer_version_id=answer.materialized_reference_answer_id,
                    expected_reference_answer_content_hash=bundle_answer["content_hash"],
                    structured_rubric_version_id=rubric.materialized_structured_rubric_id,
                    expected_structured_rubric_content_hash=bundle_rubric["content_hash"],
                    explicit_confirmation=True,
                ),
                db,
                actor,
                materialized_bundle,
            )
        )
    audit(
        db,
        actor.id,
        "confirm_all_candidate_question_packages",
        "assignment",
        assignment_id,
        {"question_count": len(results), "bundle_hash": payload.expected_bundle_hash},
    )
    db.commit()
    return {
        "confirmed_count": sum(not already for _, _, already in results),
        "already_confirmed_count": sum(already for _, _, already in results),
    }


@router.post("/structured-rubrics/{rubric_id}/derive", status_code=201)
def derive_rubric(rubric_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    source = db.get(StructuredRubricVersion, rubric_id)
    if source is None:
        raise ApiProblem(404, "RUBRIC_NOT_FOUND", "Rubric 不存在")
    _owned_question(db, actor.id, source.question_id)
    payload = RubricInput(
        reference_answer_version_id=source.reference_answer_version_id,
        title=source.title,
        total_points=source.total_points,
        criteria=[
            CriterionInput(
                stable_key=c.stable_key,
                title=c.title,
                description=c.description,
                max_points=c.max_points,
                criterion_type=c.criterion_type,
                required=c.required,
                dependencies=c.dependencies,
                expected_evidence=c.expected_evidence,
                validation_mode=c.validation_mode,
                manual_review_policy=c.manual_review_policy,
                partial_credit_policy=c.partial_credit_policy,
                error_category=c.error_category,
                validation_rule=c.validation_rule,
                metadata=c.metadata_,
            )
            for c in _criteria(db, source.id)
        ],
    )
    return create_rubric(source.question_id, payload, db, actor)


@router.get("/structured-rubrics/{left_id}/diff/{right_id}")
def rubric_diff(left_id: uuid.UUID, right_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    left, right = (
        db.get(StructuredRubricVersion, left_id),
        db.get(StructuredRubricVersion, right_id),
    )
    if left is None or right is None or left.question_id != right.question_id:
        raise ApiProblem(404, "RUBRIC_NOT_FOUND", "Rubric 不存在或不属于同一题目")
    _owned_question(db, actor.id, left.question_id)
    left_json, right_json = _rubric_json(db, left), _rubric_json(db, right)
    return {
        "left": left_json,
        "right": right_json,
        "changed_fields": [
            key
            for key in ("title", "total_points", "reference_answer_version_id", "criteria")
            if left_json[key] != right_json[key]
        ],
    }
