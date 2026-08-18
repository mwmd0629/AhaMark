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
    AssignmentClass,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    now_utc,
)
from app.rubrics.validation import validate_rubric
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
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
    item = db.get(ReferenceAnswerVersion, reference_id)
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
    item = db.get(ReferenceAnswerVersion, reference_id)
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


@router.get("/structured-rubrics")
def rubric_catalog(
    db: Db,
    actor: Actor,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    search: str = Query("", max_length=100),
    status: Literal["draft", "confirmed", "retired"] | None = None,
    class_id: uuid.UUID | None = None,
    subject: str | None = Query(None, max_length=80),
) -> dict[str, Any]:
    filters: list[Any] = [Assignment.owner_id == actor.id]
    normalized_search = search.strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            or_(
                StructuredRubricVersion.title.ilike(pattern),
                Assignment.title.ilike(pattern),
                Question.question_number.ilike(pattern),
                Question.content_text.ilike(pattern),
            )
        )
    if status is not None:
        filters.append(StructuredRubricVersion.status == status)
    if subject is not None:
        filters.append(Assignment.subject == subject)
    if class_id is not None:
        filters.append(
            Assignment.id.in_(
                select(AssignmentClass.assignment_id).where(
                    AssignmentClass.class_id == class_id
                )
            )
        )

    joined = (
        select(StructuredRubricVersion, Question, Assignment)
        .join(Question, Question.id == StructuredRubricVersion.question_id)
        .join(PaperVersion, PaperVersion.id == Question.paper_version_id)
        .join(Assignment, Assignment.id == PaperVersion.assignment_id)
        .where(*filters)
    )
    total = (
        db.scalar(
            select(func.count(StructuredRubricVersion.id))
            .select_from(StructuredRubricVersion)
            .join(Question, Question.id == StructuredRubricVersion.question_id)
            .join(PaperVersion, PaperVersion.id == Question.paper_version_id)
            .join(Assignment, Assignment.id == PaperVersion.assignment_id)
            .where(*filters)
        )
        or 0
    )
    rows = db.execute(
        joined.order_by(
            StructuredRubricVersion.created_at.desc(), StructuredRubricVersion.id.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "rubric": _rubric_json(db, rubric),
                "created_at": rubric.created_at,
                "confirmed_at": rubric.confirmed_at,
                "assignment": {
                    "id": str(assignment.id),
                    "title": assignment.title,
                    "subject": assignment.subject,
                    "grade": assignment.grade,
                    "status": assignment.status,
                },
                "question": {
                    "id": str(question.id),
                    "question_number": question.question_number,
                    "content_text": (question.content_text or "")[:500],
                    "max_score": (
                        str(question.max_score) if question.max_score is not None else None
                    ),
                },
            }
            for rubric, question, assignment in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
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
        question_version=f"{question.paper_version_id}:{question.updated_at.isoformat()}",
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
    item = db.get(StructuredRubricVersion, rubric_id)
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
    item = db.get(StructuredRubricVersion, rubric_id)
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
    item = db.get(StructuredRubricVersion, rubric_id)
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
