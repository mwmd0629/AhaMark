import hashlib
import json
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.db.session import get_db
from app.models import (
    Assignment,
    AssignmentStatus,
    PaperVersion,
    Question,
    QuestionStatus,
    ReferenceAnswerVersion,
    RubricCriterion,
    RubricTemplate,
    RubricTemplateApplication,
    RubricTemplateCriterion,
    RubricTemplateVersion,
    StructuredRubricVersion,
    now_utc,
)
from app.question_versions import question_version_token
from app.rubrics.validation import validate_rubric
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["rubric-templates"])
Db = Annotated[Session, Depends(get_db)]


class TemplateCriterionInput(BaseModel):
    stable_key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    description: str | None = None
    max_points: Decimal = Field(gt=0)
    criterion_type: str = "computation"
    required: bool = True
    dependencies: list[str] = Field(default_factory=list)
    validation_mode: Literal["deterministic", "ai_suggestion", "manual_only"] = "ai_suggestion"
    manual_review_policy: dict[str, Any] = Field(default_factory=dict)
    partial_credit_policy: dict[str, Any] = Field(default_factory=dict)
    error_category: str | None = None
    validation_rule: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TemplateCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str | None = Field(None, max_length=40)
    grade: str | None = Field(None, max_length=40)
    question_type: str | None = Field(None, max_length=40)
    scoring_basis: Literal["proportional", "fixed"] = "proportional"
    total_points: Decimal = Field(default=Decimal("100"), gt=0)
    criteria: list[TemplateCriterionInput] = Field(min_length=1)


class TemplatePatchInput(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    subject: str | None = Field(None, max_length=40)
    grade: str | None = Field(None, max_length=40)
    question_type: str | None = Field(None, max_length=40)
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scoring_basis: Literal["proportional", "fixed"] | None = None
    total_points: Decimal | None = Field(None, gt=0)
    criteria: list[TemplateCriterionInput] | None = None


class VersionCreateInput(BaseModel):
    source_version_id: uuid.UUID | None = None


class ExpectedHashInput(BaseModel):
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreviewInput(BaseModel):
    template_version_id: uuid.UUID


class ApplyInput(BaseModel):
    template_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_template_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_question_version: str = Field(min_length=1, max_length=100)
    reference_answer_version_id: uuid.UUID
    expected_reference_answer_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SaveAsTemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scoring_basis: Literal["proportional", "fixed"] = "proportional"
    subject: str | None = Field(None, max_length=40)
    grade: str | None = Field(None, max_length=40)
    question_type: str | None = Field(None, max_length=40)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


_ANSWER_SPECIFIC_KEYS = {
    "answer",
    "canonical_answer",
    "evidence",
    "evidence_id",
    "expected_answer",
    "expected_evidence",
    "expected_value",
    "reference_answer",
    "reference_answer_id",
    "solution",
    "source_file",
    "source_page",
    "source_region",
    "target_value",
}


def _reusable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _reusable_value(item)
            for key, item in value.items()
            if key.lower() not in _ANSWER_SPECIFIC_KEYS
            and not key.lower().startswith(("reference_answer_", "evidence_", "source_region_"))
        }
    if isinstance(value, list):
        return [_reusable_value(item) for item in value]
    return value


def _owned_template(db: Session, owner_id: uuid.UUID, template_id: uuid.UUID) -> RubricTemplate:
    item = db.scalar(
        select(RubricTemplate).where(
            RubricTemplate.id == template_id, RubricTemplate.owner_id == owner_id
        )
    )
    if item is None:
        raise ApiProblem(404, "RUBRIC_TEMPLATE_NOT_FOUND", "评分模板不存在")
    return item


def _criteria(db: Session, version_id: uuid.UUID) -> list[RubricTemplateCriterion]:
    return list(
        db.scalars(
            select(RubricTemplateCriterion)
            .where(RubricTemplateCriterion.template_version_id == version_id)
            .order_by(RubricTemplateCriterion.display_order)
        )
    )


def _criterion_data(item: RubricTemplateCriterion) -> dict[str, Any]:
    return {
        "stable_key": item.stable_key,
        "title": item.title,
        "description": item.description,
        "max_points": str(item.max_points),
        "criterion_type": item.criterion_type,
        "required": item.required,
        "dependencies": item.dependencies,
        "validation_mode": item.validation_mode,
        "manual_review_policy": item.manual_review_policy,
        "partial_credit_policy": item.partial_credit_policy,
        "error_category": item.error_category,
        "validation_rule": item.validation_rule,
        "metadata": item.metadata_,
    }


def _version_payload(
    title: str, scoring_basis: str, total_points: Decimal, criteria: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "title": title,
        "scoring_basis": scoring_basis,
        "total_points": str(total_points),
        "criteria": criteria,
    }


def _validate(scoring_basis: str, total_points: Decimal, criteria: list[dict[str, Any]]) -> None:
    expected = Decimal("100") if scoring_basis == "proportional" else total_points
    errors = validate_rubric(expected, criteria)
    precision = Decimal("0.0001") if scoring_basis == "proportional" else Decimal("0.01")
    for item in criteria:
        points = Decimal(str(item.get("max_points")))
        if points.quantize(precision) != points:
            errors.append(
                {
                    "code": "POINTS_PRECISION_INVALID",
                    "criterion": str(item.get("stable_key", "")),
                }
            )
    if scoring_basis == "proportional" and total_points != Decimal("100"):
        errors.append({"code": "PROPORTIONAL_TOTAL_NOT_100", "message": "比例模板总计必须为 100%"})
    if scoring_basis == "fixed" and total_points.quantize(Decimal("0.01")) != total_points:
        errors.append({"code": "TOTAL_POINTS_PRECISION_INVALID"})
    if errors:
        raise ApiProblem(422, "RUBRIC_TEMPLATE_INVALID", "评分模板校验失败", {"errors": errors})


def _replace_criteria(
    db: Session, version: RubricTemplateVersion, criteria: list[TemplateCriterionInput]
) -> None:
    data = [item.model_dump(mode="json") for item in criteria]
    _validate(version.scoring_basis, Decimal(version.total_points), data)
    db.execute(
        delete(RubricTemplateCriterion).where(
            RubricTemplateCriterion.template_version_id == version.id
        )
    )
    for order, item in enumerate(criteria):
        values = item.model_dump()
        metadata = values.pop("metadata")
        db.add(
            RubricTemplateCriterion(
                template_version_id=version.id,
                display_order=order,
                metadata_=metadata,
                **values,
            )
        )
    version.content_hash = _hash(
        _version_payload(version.title, version.scoring_basis, Decimal(version.total_points), data)
    )


def _version_json(db: Session, item: RubricTemplateVersion) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "version": item.version,
        "title": item.title,
        "scoring_basis": item.scoring_basis,
        "total_points": str(item.total_points),
        "status": item.status,
        "content_hash": item.content_hash,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "confirmed_at": item.confirmed_at,
        "criteria": [_criterion_data(value) for value in _criteria(db, item.id)],
    }


def _template_json(
    db: Session, item: RubricTemplate, include_versions: bool = False
) -> dict[str, Any]:
    versions = list(
        db.scalars(
            select(RubricTemplateVersion)
            .where(RubricTemplateVersion.template_id == item.id)
            .order_by(RubricTemplateVersion.version.desc())
        )
    )
    current = next((value for value in versions if value.id == item.current_version_id), None)
    current = current or (versions[0] if versions else None)
    result: dict[str, Any] = {
        "id": str(item.id),
        "name": item.name,
        "subject": item.subject,
        "grade": item.grade,
        "question_type": item.question_type,
        "status": item.status,
        "criterion_count": len(_criteria(db, current.id)) if current else 0,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "current_version": _version_json(db, current) if current else None,
    }
    if include_versions:
        result["versions"] = [_version_json(db, value) for value in versions]
    return result


@router.get("/rubric-templates")
def list_templates(
    db: Db,
    actor: Actor,
    search: str | None = None,
    subject: str | None = None,
    grade: str | None = None,
    question_type: str | None = None,
    status: str | None = Query(None, pattern="^(draft|confirmed|archived)$"),
) -> list[dict[str, Any]]:
    query = select(RubricTemplate).where(RubricTemplate.owner_id == actor.id)
    if search:
        query = query.where(
            or_(
                RubricTemplate.name.ilike(f"%{search}%"),
                RubricTemplate.subject.ilike(f"%{search}%"),
            )
        )
    for column, value in (
        (RubricTemplate.subject, subject),
        (RubricTemplate.grade, grade),
        (RubricTemplate.question_type, question_type),
        (RubricTemplate.status, status),
    ):
        if value:
            query = query.where(column == value)
    return [
        _template_json(db, item)
        for item in db.scalars(query.order_by(RubricTemplate.updated_at.desc()))
    ]


def _create_template_record(
    payload: TemplateCreateInput, db: Session, actor_id: uuid.UUID
) -> RubricTemplate:
    template = RubricTemplate(
        owner_id=actor_id,
        name=payload.name,
        subject=payload.subject,
        grade=payload.grade,
        question_type=payload.question_type,
        status="draft",
    )
    db.add(template)
    db.flush()
    version = RubricTemplateVersion(
        template_id=template.id,
        version=1,
        title=payload.name,
        scoring_basis=payload.scoring_basis,
        total_points=payload.total_points,
        status="draft",
        content_hash="",
        created_by=actor_id,
    )
    db.add(version)
    db.flush()
    _replace_criteria(db, version, payload.criteria)
    template.current_version_id = version.id
    audit(db, actor_id, "create", "rubric_template", template.id, {"version": 1})
    return template


@router.post("/rubric-templates", status_code=201)
def create_template(payload: TemplateCreateInput, db: Db, actor: Actor) -> dict[str, Any]:
    template = _create_template_record(payload, db, actor.id)
    db.commit()
    db.refresh(template)
    return _template_json(db, template, True)


@router.get("/rubric-templates/{template_id}")
def get_template(template_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return _template_json(db, _owned_template(db, actor.id, template_id), True)


@router.patch("/rubric-templates/{template_id}")
def update_template(
    template_id: uuid.UUID, payload: TemplatePatchInput, db: Db, actor: Actor
) -> dict[str, Any]:
    template = _owned_template(db, actor.id, template_id)
    db.scalar(select(RubricTemplate.id).where(RubricTemplate.id == template.id).with_for_update())
    version = db.scalar(
        select(RubricTemplateVersion)
        .where(RubricTemplateVersion.id == template.current_version_id)
        .with_for_update()
    )
    if version is None or version.status != "draft":
        raise ApiProblem(409, "RUBRIC_TEMPLATE_IMMUTABLE", "已确认版本不可原地修改，请新建草稿版本")
    if version.content_hash != payload.expected_content_hash:
        raise ApiProblem(409, "RUBRIC_TEMPLATE_STALE", "模板已更新，请载入最新版本")
    for field in ("name", "subject", "grade", "question_type"):
        if field in payload.model_fields_set:
            value = getattr(payload, field)
            if field != "name" or value is not None:
                setattr(template, field, value)
    if payload.name is not None:
        version.title = payload.name
    if payload.scoring_basis is not None:
        version.scoring_basis = payload.scoring_basis
    if payload.total_points is not None:
        version.total_points = payload.total_points
    existing = [
        TemplateCriterionInput(**_criterion_data(item)) for item in _criteria(db, version.id)
    ]
    _replace_criteria(db, version, payload.criteria if payload.criteria is not None else existing)
    audit(db, actor.id, "update", "rubric_template", template.id, {"version": version.version})
    db.commit()
    db.refresh(template)
    return _template_json(db, template, True)


@router.post("/rubric-templates/{template_id}/versions", status_code=201)
def create_version(
    template_id: uuid.UUID, payload: VersionCreateInput, db: Db, actor: Actor
) -> dict[str, Any]:
    template = _owned_template(db, actor.id, template_id)
    if template.status == "archived":
        raise ApiProblem(409, "RUBRIC_TEMPLATE_ARCHIVED", "已归档模板不能创建新版本")
    db.scalar(select(RubricTemplate.id).where(RubricTemplate.id == template.id).with_for_update())
    source = db.get(RubricTemplateVersion, payload.source_version_id or template.current_version_id)
    if source is None or source.template_id != template.id:
        raise ApiProblem(404, "RUBRIC_TEMPLATE_VERSION_NOT_FOUND", "模板版本不存在")
    number = (
        db.scalar(
            select(func.coalesce(func.max(RubricTemplateVersion.version), 0)).where(
                RubricTemplateVersion.template_id == template.id
            )
        )
        or 0
    ) + 1
    version = RubricTemplateVersion(
        template_id=template.id,
        version=number,
        title=source.title,
        scoring_basis=source.scoring_basis,
        total_points=source.total_points,
        status="draft",
        content_hash=source.content_hash,
        created_by=actor.id,
    )
    db.add(version)
    db.flush()
    for order, item in enumerate(_criteria(db, source.id)):
        data = _criterion_data(item)
        metadata = data.pop("metadata")
        data["max_points"] = Decimal(data["max_points"])
        db.add(
            RubricTemplateCriterion(
                template_version_id=version.id, display_order=order, metadata_=metadata, **data
            )
        )
    template.current_version_id = version.id
    template.status = "draft"
    audit(db, actor.id, "create_version", "rubric_template", template.id, {"version": number})
    db.commit()
    db.refresh(template)
    return _template_json(db, template, True)


@router.post("/rubric-templates/{template_id}/confirm")
def confirm_template(
    template_id: uuid.UUID, payload: ExpectedHashInput, db: Db, actor: Actor
) -> dict[str, Any]:
    template = _owned_template(db, actor.id, template_id)
    db.scalar(select(RubricTemplate.id).where(RubricTemplate.id == template.id).with_for_update())
    version = db.scalar(
        select(RubricTemplateVersion)
        .where(RubricTemplateVersion.id == template.current_version_id)
        .with_for_update()
    )
    if version is None or version.status != "draft":
        raise ApiProblem(409, "RUBRIC_TEMPLATE_NOT_DRAFT", "只有草稿版本可以确认")
    if version.content_hash != payload.expected_content_hash:
        raise ApiProblem(409, "RUBRIC_TEMPLATE_STALE", "模板已更新，请重新核对")
    data = [_criterion_data(item) for item in _criteria(db, version.id)]
    _validate(version.scoring_basis, Decimal(version.total_points), data)
    for old in db.scalars(
        select(RubricTemplateVersion).where(
            RubricTemplateVersion.template_id == template.id,
            RubricTemplateVersion.status == "confirmed",
        )
    ):
        old.status = "archived"
    version.status = "confirmed"
    version.confirmed_by = actor.id
    version.confirmed_at = now_utc()
    template.status = "confirmed"
    audit(db, actor.id, "confirm", "rubric_template", template.id, {"version": version.version})
    db.commit()
    db.refresh(template)
    return _template_json(db, template, True)


@router.post("/rubric-templates/{template_id}/archive")
def archive_template(template_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    template = _owned_template(db, actor.id, template_id)
    template.status = "archived"
    for version in db.scalars(
        select(RubricTemplateVersion).where(
            RubricTemplateVersion.template_id == template.id,
            RubricTemplateVersion.status != "archived",
        )
    ):
        version.status = "archived"
    audit(db, actor.id, "archive", "rubric_template", template.id)
    db.commit()
    db.refresh(template)
    return _template_json(db, template, True)


@router.post("/rubric-templates/{template_id}/duplicate", status_code=201)
def duplicate_template(template_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    source_template = _owned_template(db, actor.id, template_id)
    source = db.get(RubricTemplateVersion, source_template.current_version_id)
    if source is None:
        raise ApiProblem(409, "RUBRIC_TEMPLATE_EMPTY", "模板没有可复制版本")
    criteria = [
        TemplateCriterionInput(**_criterion_data(item)) for item in _criteria(db, source.id)
    ]
    return create_template(
        TemplateCreateInput(
            name=f"{source_template.name}（副本）",
            subject=source_template.subject,
            grade=source_template.grade,
            question_type=source_template.question_type,
            scoring_basis=source.scoring_basis,
            total_points=source.total_points,
            criteria=criteria,
        ),
        db,
        actor,
    )


def _question_context(
    db: Session, owner_id: uuid.UUID, question_id: uuid.UUID, lock: bool = False
) -> tuple[Question, PaperVersion, Assignment, ReferenceAnswerVersion]:
    query = (
        select(Question, PaperVersion, Assignment)
        .join(PaperVersion, PaperVersion.id == Question.paper_version_id)
        .join(Assignment, Assignment.id == PaperVersion.assignment_id)
        .where(Question.id == question_id, Assignment.owner_id == owner_id)
    )
    if lock:
        query = query.with_for_update()
    row = db.execute(query).one_or_none()
    if row is None:
        raise ApiProblem(404, "QUESTION_NOT_FOUND", "题目不存在")
    question, paper, assignment = row
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_NOT_DRAFT", "只有草稿作业可以应用模板")
    if assignment.active_paper_version_id != paper.id or question.status != QuestionStatus.active:
        raise ApiProblem(409, "QUESTION_NOT_ACTIVE", "题目不属于当前试卷")
    if question.max_score is None or Decimal(question.max_score) <= 0:
        raise ApiProblem(409, "QUESTION_SCORE_REQUIRED", "请先确认题目满分")
    reference = db.scalar(
        select(ReferenceAnswerVersion)
        .where(
            ReferenceAnswerVersion.question_id == question.id,
            ReferenceAnswerVersion.status == "confirmed",
        )
        .order_by(ReferenceAnswerVersion.version.desc())
    )
    if reference is None:
        raise ApiProblem(409, "REFERENCE_ANSWER_REQUIRED", "请先确认本题标准答案")
    return question, paper, assignment, reference


def _converted(db: Session, version: RubricTemplateVersion, score: Decimal) -> list[dict[str, Any]]:
    source = _criteria(db, version.id)
    if not source:
        raise ApiProblem(409, "RUBRIC_TEMPLATE_EMPTY", "模板没有评分项")
    result: list[dict[str, Any]] = []
    allocated = Decimal("0")
    for index, item in enumerate(source):
        if version.scoring_basis == "proportional":
            points = (
                score - allocated
                if index == len(source) - 1
                else (score * Decimal(item.max_points) / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
        else:
            if Decimal(version.total_points) != score:
                raise ApiProblem(409, "FIXED_SCORE_MISMATCH", "固定分值模板总分必须等于题目满分")
            points = Decimal(item.max_points)
        if points <= 0:
            raise ApiProblem(422, "RUBRIC_TEMPLATE_ROUNDING_INVALID", "换算后评分项分值必须大于 0")
        allocated += points
        data = _criterion_data(item)
        data["max_points"] = str(points)
        data["expected_evidence"] = {}
        result.append(data)
    errors = validate_rubric(score, result)
    if errors:
        raise ApiProblem(
            422, "RUBRIC_TEMPLATE_CONVERSION_INVALID", "模板无法换算到本题", {"errors": errors}
        )
    return result


def _preview(
    db: Session,
    actor_id: uuid.UUID,
    question_id: uuid.UUID,
    version_id: uuid.UUID,
    lock: bool = False,
) -> tuple[
    dict[str, Any],
    RubricTemplate,
    RubricTemplateVersion,
    ReferenceAnswerVersion,
    Assignment,
    Question,
]:
    question, _paper, assignment, reference = _question_context(db, actor_id, question_id, lock)
    version = db.scalar(
        select(RubricTemplateVersion)
        .join(RubricTemplate)
        .where(RubricTemplateVersion.id == version_id, RubricTemplate.owner_id == actor_id)
    )
    if version is None:
        raise ApiProblem(404, "RUBRIC_TEMPLATE_VERSION_NOT_FOUND", "模板版本不存在")
    template = db.get(RubricTemplate, version.template_id)
    if template is None or template.status == "archived" or version.status != "confirmed":
        raise ApiProblem(409, "RUBRIC_TEMPLATE_NOT_CONFIRMED", "只有已确认且未归档的模板可以使用")
    assert question.max_score is not None  # guarded by _question_context
    converted = _converted(db, version, Decimal(question.max_score))
    response = {
        "template_id": str(template.id),
        "template_version_id": str(version.id),
        "template_content_hash": version.content_hash,
        "question_id": str(question.id),
        "question_version": question_version_token(question),
        "reference_answer_version_id": str(reference.id),
        "reference_answer_content_hash": reference.content_hash,
        "total_points": str(question.max_score),
        "criteria": converted,
        "blockers": [],
    }
    return response, template, version, reference, assignment, question


@router.post("/questions/{question_id}/rubric-template-preview")
def preview_template(
    question_id: uuid.UUID, payload: PreviewInput, db: Db, actor: Actor
) -> dict[str, Any]:
    try:
        return _preview(db, actor.id, question_id, payload.template_version_id)[0]
    except ApiProblem as problem:
        if problem.status not in {409, 422}:
            raise
        return {
            "question_id": str(question_id),
            "template_version_id": str(payload.template_version_id),
            "criteria": [],
            "blockers": [{"code": problem.code, "message": problem.message}],
        }


@router.post("/questions/{question_id}/apply-rubric-template", status_code=201)
def apply_template(
    question_id: uuid.UUID, payload: ApplyInput, db: Db, actor: Actor
) -> dict[str, Any]:
    request_hash = _hash(payload.model_dump(mode="json") | {"question_id": str(question_id)})
    replay = db.scalar(
        select(RubricTemplateApplication).where(
            RubricTemplateApplication.owner_id == actor.id,
            RubricTemplateApplication.idempotency_key == payload.idempotency_key,
        )
    )
    if replay:
        if replay.request_hash != request_hash:
            raise ApiProblem(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于不同请求")
        rubric = db.get(StructuredRubricVersion, replay.structured_rubric_version_id)
        return {
            "application_id": str(replay.id),
            "structured_rubric_version_id": str(replay.structured_rubric_version_id),
            "rubric_version": rubric.rubric_version if rubric else None,
            "replayed": True,
        }
    preview, template, version, reference, assignment, question = _preview(
        db, actor.id, question_id, payload.template_version_id, True
    )
    if (
        version.content_hash != payload.expected_template_content_hash
        or preview["question_version"] != payload.expected_question_version
        or reference.id != payload.reference_answer_version_id
        or reference.content_hash != payload.expected_reference_answer_content_hash
    ):
        raise ApiProblem(409, "RUBRIC_TEMPLATE_APPLY_STALE", "题目、答案或模板已变化，请重新预览")
    number = (
        db.scalar(
            select(func.coalesce(func.max(StructuredRubricVersion.rubric_version), 0)).where(
                StructuredRubricVersion.question_id == question.id
            )
        )
        or 0
    ) + 1
    criteria = preview["criteria"]
    rubric_payload = {
        "reference_answer_version_id": str(reference.id),
        "title": version.title,
        "total_points": str(question.max_score),
        "criteria": criteria,
    }
    rubric = StructuredRubricVersion(
        question_id=question.id,
        question_version=preview["question_version"],
        reference_answer_version_id=reference.id,
        rubric_version=number,
        title=version.title,
        total_points=question.max_score,
        status="draft",
        content_hash=_hash(rubric_payload),
        created_by=actor.id,
    )
    db.add(rubric)
    db.flush()
    for order, criterion in enumerate(criteria):
        values = dict(criterion)
        metadata = values.pop("metadata")
        values["max_points"] = Decimal(values["max_points"])
        db.add(
            RubricCriterion(
                rubric_version_id=rubric.id, display_order=order, metadata_=metadata, **values
            )
        )
    application = RubricTemplateApplication(
        id=uuid.uuid4(),
        owner_id=actor.id,
        template_id=template.id,
        template_version_id=version.id,
        assignment_id=assignment.id,
        question_id=question.id,
        question_version=preview["question_version"],
        reference_answer_version_id=reference.id,
        reference_answer_content_hash=reference.content_hash,
        structured_rubric_version_id=rubric.id,
        template_content_hash=version.content_hash,
        conversion={
            "total_points": preview["total_points"],
            "criteria": [
                {"stable_key": item["stable_key"], "max_points": item["max_points"]}
                for item in criteria
            ],
        },
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        actor_id=actor.id,
    )
    db.add(application)
    audit(
        db,
        actor.id,
        "apply",
        "rubric_template",
        template.id,
        {
            "application_id": str(application.id),
            "question_id": str(question.id),
            "rubric_id": str(rubric.id),
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = db.scalar(
            select(RubricTemplateApplication).where(
                RubricTemplateApplication.owner_id == actor.id,
                RubricTemplateApplication.idempotency_key == payload.idempotency_key,
            )
        )
        if replay is None or replay.request_hash != request_hash:
            raise ApiProblem(
                409,
                "RUBRIC_TEMPLATE_APPLY_CONFLICT",
                "模板应用发生并发冲突，请重新预览",
            ) from None
        rubric = db.get(StructuredRubricVersion, replay.structured_rubric_version_id)
        return {
            "application_id": str(replay.id),
            "structured_rubric_version_id": str(replay.structured_rubric_version_id),
            "rubric_version": rubric.rubric_version if rubric else None,
            "replayed": True,
        }
    return {
        "application_id": str(application.id),
        "structured_rubric_version_id": str(rubric.id),
        "rubric_version": number,
        "replayed": False,
    }


@router.post("/structured-rubrics/{rubric_id}/save-as-template", status_code=201)
def save_as_template(
    rubric_id: uuid.UUID, payload: SaveAsTemplateInput, db: Db, actor: Actor
) -> dict[str, Any]:
    row = db.execute(
        select(StructuredRubricVersion, Question, PaperVersion, Assignment)
        .join(Question, Question.id == StructuredRubricVersion.question_id)
        .join(PaperVersion, PaperVersion.id == Question.paper_version_id)
        .join(Assignment, Assignment.id == PaperVersion.assignment_id)
        .where(StructuredRubricVersion.id == rubric_id, Assignment.owner_id == actor.id)
    ).one_or_none()
    if row is None:
        raise ApiProblem(404, "STRUCTURED_RUBRIC_NOT_FOUND", "评分标准不存在")
    rubric, question, _paper, assignment = row
    source = list(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == rubric.id)
            .order_by(RubricCriterion.display_order)
        )
    )
    total = Decimal(rubric.total_points)
    criteria: list[TemplateCriterionInput] = []
    allocated = Decimal("0")
    for index, item in enumerate(source):
        points = Decimal(item.max_points)
        if payload.scoring_basis == "proportional":
            points = (
                Decimal("100") - allocated
                if index == len(source) - 1
                else (points * Decimal("100") / total).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
            )
        allocated += points
        criteria.append(
            TemplateCriterionInput(
                stable_key=item.stable_key,
                title=item.title,
                description=None,
                max_points=points,
                criterion_type=item.criterion_type,
                required=item.required,
                dependencies=item.dependencies,
                validation_mode=item.validation_mode,
                manual_review_policy=_reusable_value(item.manual_review_policy),
                partial_credit_policy=_reusable_value(item.partial_credit_policy),
                error_category=item.error_category,
                validation_rule=_reusable_value(item.validation_rule),
                metadata=_reusable_value(item.metadata_),
            )
        )
    template = _create_template_record(
        TemplateCreateInput(
            name=payload.name,
            subject=payload.subject or assignment.subject,
            grade=payload.grade or assignment.grade,
            question_type=payload.question_type or question.question_type,
            scoring_basis=payload.scoring_basis,
            total_points=Decimal("100") if payload.scoring_basis == "proportional" else total,
            criteria=criteria,
        ),
        db,
        actor.id,
    )
    audit(
        db,
        actor.id,
        "save_as_template",
        "structured_rubric_version",
        rubric.id,
        {"template_id": str(template.id)},
    )
    db.commit()
    db.refresh(template)
    return _template_json(db, template, True)
