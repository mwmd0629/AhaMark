import hashlib
import io
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.assignment_central_review import PublishInput
from app.api.domain import ApiProblem, audit
from app.core.config import get_settings
from app.db.session import get_db
from app.math_validation.stale import stale_for_question
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentClass,
    AssignmentStatus,
    FileStatus,
    GradingResult,
    KnowledgePoint,
    PaperPage,
    PaperVersion,
    Question,
    QuestionKnowledgePoint,
    QuestionRegion,
    QuestionRubric,
    QuestionStatus,
    RubricItem,
    RubricVersion,
    SchoolClass,
    StoredFile,
    StudentAnswer,
    Submission,
    VersionStatus,
    now_utc,
)
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/assignments", tags=["assignments"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]

QUESTION_TYPES = {
    "single_choice",
    "multiple_choice",
    "true_false",
    "fill_blank",
    "short_answer",
    "calculation",
    "proof",
    "essay",
    "other",
}
MIMES = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class AssignmentInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    subject: str | None = Field(None, max_length=40)
    grade: str | None = Field(None, max_length=40)
    description: str | None = Field(None, max_length=4000)
    instructions: str | None = Field(None, max_length=4000)
    total_score: Decimal | None = Field(None, gt=0)
    due_at: datetime | None = None
    class_ids: list[uuid.UUID] = Field(default_factory=list)


class AssignmentPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    subject: str | None = Field(None, max_length=40)
    grade: str | None = Field(None, max_length=40)
    description: str | None = Field(None, max_length=4000)
    instructions: str | None = Field(None, max_length=4000)
    total_score: Decimal | None = Field(None, gt=0)
    due_at: datetime | None = None
    updated_at: datetime


class ClassesInput(BaseModel):
    class_ids: list[uuid.UUID]
    updated_at: datetime


class PagePatch(BaseModel):
    page_number: int | None = Field(None, ge=1)
    rotation: Literal[0, 90, 180, 270] | None = None
    status: Literal["ready", "excluded", "pending_conversion"] | None = None


class ReorderInput(BaseModel):
    ids: list[uuid.UUID]


class QuestionInput(BaseModel):
    question_number: str = Field(min_length=1, max_length=40)
    question_type: str
    max_score: Decimal = Field(gt=0)
    content_text: str | None = None
    content_latex: str | None = None
    difficulty: Literal["easy", "medium", "hard"] | None = None
    parent_question_id: uuid.UUID | None = None
    knowledge_points: list[str] = Field(default_factory=list)


class RegionInput(BaseModel):
    paper_page_id: uuid.UUID
    x: Decimal
    y: Decimal
    width: Decimal
    height: Decimal
    region_type: str = "question"

    @model_validator(mode="after")
    def bounds(self) -> "RegionInput":
        if (
            self.x < 0
            or self.y < 0
            or self.width <= 0
            or self.height <= 0
            or self.x + self.width > 1
            or self.y + self.height > 1
        ):
            raise ValueError("区域必须位于 0..1 页面坐标内")
        return self


class RubricInput(BaseModel):
    standard_answer: str | None = None
    alternative_answers: list[str] = Field(default_factory=list)
    scoring_notes: str | None = None
    allow_step_score: bool = True
    unit_requirement: str | None = None
    format_requirement: str | None = None
    precision_requirement: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


def owned(db: Session, actor_id: uuid.UUID, assignment_id: uuid.UUID) -> Assignment:
    item = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.owner_id == actor_id)
    )
    if not item:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    return item


def validate_classes(db: Session, actor_id: uuid.UUID, ids: list[uuid.UUID]) -> list[SchoolClass]:
    if len(ids) != len(set(ids)):
        raise ApiProblem(422, "DUPLICATE_CLASS", "班级不能重复")
    rows = list(db.scalars(select(SchoolClass).where(SchoolClass.id.in_(ids))).all()) if ids else []
    if len(rows) != len(ids) or any(x.owner_id != actor_id for x in rows):
        raise ApiProblem(403, "CLASS_NOT_OWNED", "包含无权使用的班级")
    if any(x.status != ArchiveStatus.active for x in rows):
        raise ApiProblem(409, "CLASS_NOT_ACTIVE", "只能向活动班级布置作业")
    return rows


def paper(db: Session, item: Assignment) -> PaperVersion | None:
    return (
        db.get(PaperVersion, item.active_paper_version_id) if item.active_paper_version_id else None
    )


def rubric(db: Session, item: Assignment) -> RubricVersion | None:
    return (
        db.get(RubricVersion, item.active_rubric_version_id)
        if item.active_rubric_version_id
        else None
    )


def clone_rubric_version(db: Session, source: RubricVersion, actor_id: uuid.UUID) -> RubricVersion:
    target = RubricVersion(
        assignment_id=source.assignment_id,
        version=source.version + 1,
        created_by=actor_id,
        notes=f"由 RubricVersion v{source.version} 创建的新草稿",
    )
    db.add(target)
    db.flush()
    for old_rubric in db.scalars(
        select(QuestionRubric).where(QuestionRubric.rubric_version_id == source.id)
    ).all():
        new_rubric = QuestionRubric(
            rubric_version_id=target.id,
            question_id=old_rubric.question_id,
            standard_answer=old_rubric.standard_answer,
            alternative_answers=list(old_rubric.alternative_answers or []),
            scoring_notes=old_rubric.scoring_notes,
            allow_step_score=old_rubric.allow_step_score,
            unit_requirement=old_rubric.unit_requirement,
            format_requirement=old_rubric.format_requirement,
            precision_requirement=old_rubric.precision_requirement,
        )
        db.add(new_rubric)
        db.flush()
        for old_item in db.scalars(
            select(RubricItem)
            .where(RubricItem.question_rubric_id == old_rubric.id)
            .order_by(RubricItem.display_order)
        ).all():
            db.add(
                RubricItem(
                    question_rubric_id=new_rubric.id,
                    display_order=old_item.display_order,
                    title=old_item.title,
                    description=old_item.description,
                    points=old_item.points,
                    item_type=old_item.item_type,
                    required=old_item.required,
                    deduction_rule=old_item.deduction_rule,
                )
            )
    db.flush()
    return target


def invalidate_grading_for_rubric_change(db: Session, assignment_id: uuid.UUID) -> int:
    results = db.scalars(
        select(GradingResult)
        .join(StudentAnswer, StudentAnswer.id == GradingResult.student_answer_id)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(
            Submission.assignment_id == assignment_id,
            GradingResult.status.in_(["suggested", "accepted", "modified"]),
        )
    ).all()
    answer_ids: set[uuid.UUID] = set()
    for result in results:
        result.status = "stale"
        answer_ids.add(result.student_answer_id)
    if answer_ids:
        for answer in db.scalars(
            select(StudentAnswer).where(StudentAnswer.id.in_(answer_ids))
        ).all():
            answer.status, answer.requires_review = "stale", True
    return len(results)


def question_json(
    db: Session,
    q: Question,
    regions: list[QuestionRegion] | None = None,
    knowledge_points: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if regions is None:
        regions = list(db.scalars(select(QuestionRegion).where(QuestionRegion.question_id == q.id)))
    if knowledge_points is None:
        knowledge_points = [
            {"id": str(row.id), "name": row.name}
            for row in db.execute(
                select(KnowledgePoint.id, KnowledgePoint.name)
                .join(QuestionKnowledgePoint)
                .where(QuestionKnowledgePoint.question_id == q.id)
            )
        ]
    return {
        "id": str(q.id),
        "question_number": q.question_number,
        "display_order": q.display_order,
        "question_type": q.question_type,
        "content_text": q.content_text,
        "content_latex": q.content_latex,
        "max_score": str(q.max_score) if q.max_score is not None else None,
        "difficulty": q.difficulty,
        "parent_question_id": str(q.parent_question_id) if q.parent_question_id else None,
        "source": q.source,
        "knowledge_points": knowledge_points,
        "regions": [
            {
                "id": str(r.id),
                "paper_page_id": str(r.paper_page_id),
                "x": str(r.x),
                "y": str(r.y),
                "width": str(r.width),
                "height": str(r.height),
                "source": r.source,
            }
            for r in regions
        ],
    }


def detail(db: Session, item: Assignment) -> dict[str, Any]:
    classes = db.execute(
        select(SchoolClass.id, SchoolClass.name, SchoolClass.status)
        .join(AssignmentClass, AssignmentClass.class_id == SchoolClass.id)
        .where(AssignmentClass.assignment_id == item.id)
    ).all()
    pv = paper(db, item)
    rv = rubric(db, item)
    pages = (
        db.scalars(
            select(PaperPage)
            .where(PaperPage.paper_version_id == pv.id)
            .order_by(PaperPage.page_number)
        ).all()
        if pv
        else []
    )
    qs = (
        db.scalars(
            select(Question)
            .where(Question.paper_version_id == pv.id, Question.status == QuestionStatus.active)
            .order_by(Question.display_order)
        ).all()
        if pv
        else []
    )
    question_ids = [question.id for question in qs]
    regions_by_question: dict[uuid.UUID, list[QuestionRegion]] = {
        question_id: [] for question_id in question_ids
    }
    knowledge_points_by_question: dict[uuid.UUID, list[dict[str, str]]] = {
        question_id: [] for question_id in question_ids
    }
    if question_ids:
        for region in db.scalars(
            select(QuestionRegion).where(QuestionRegion.question_id.in_(question_ids))
        ):
            regions_by_question[region.question_id].append(region)
        for question_id, knowledge_point_id, knowledge_point_name in db.execute(
            select(
                QuestionKnowledgePoint.question_id,
                KnowledgePoint.id,
                KnowledgePoint.name,
            )
            .join(
                KnowledgePoint,
                KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id,
            )
            .where(QuestionKnowledgePoint.question_id.in_(question_ids))
        ):
            knowledge_points_by_question[question_id].append(
                {"id": str(knowledge_point_id), "name": knowledge_point_name}
            )
    rubrics: list[dict[str, Any]] = []
    question_rubrics: list[QuestionRubric] = []
    if rv:
        question_rubrics = list(
            db.scalars(select(QuestionRubric).where(QuestionRubric.rubric_version_id == rv.id))
        )
        rubric_items_by_rubric: dict[uuid.UUID, list[RubricItem]] = {
            question_rubric.id: [] for question_rubric in question_rubrics
        }
        if rubric_items_by_rubric:
            for rubric_item in db.scalars(
                select(RubricItem)
                .where(RubricItem.question_rubric_id.in_(rubric_items_by_rubric))
                .order_by(RubricItem.question_rubric_id, RubricItem.display_order)
            ):
                rubric_items_by_rubric[rubric_item.question_rubric_id].append(rubric_item)
        for qr in question_rubrics:
            rubrics.append(
                {
                    "id": str(qr.id),
                    "question_id": str(qr.question_id),
                    "standard_answer": qr.standard_answer,
                    "alternative_answers": qr.alternative_answers,
                    "scoring_notes": qr.scoring_notes,
                    "allow_step_score": qr.allow_step_score,
                    "unit_requirement": qr.unit_requirement,
                    "format_requirement": qr.format_requirement,
                    "precision_requirement": qr.precision_requirement,
                    "items": [
                        {
                            "id": str(x.id),
                            "title": x.title,
                            "description": x.description,
                            "points": str(x.points),
                            "item_type": x.item_type,
                            "required": x.required,
                            "deduction_rule": x.deduction_rule,
                        }
                        for x in rubric_items_by_rubric[qr.id]
                    ],
                }
            )
    issues = publish_issues(
        db,
        item,
        preloaded_paper=pv,
        preloaded_rubric=rv,
        preloaded_questions=qs,
        preloaded_question_rubrics=question_rubrics,
    )
    return {
        "id": str(item.id),
        "title": item.title,
        "subject": item.subject,
        "grade": item.grade,
        "description": item.description,
        "instructions": item.instructions,
        "status": item.status,
        "total_score": str(item.total_score) if item.total_score is not None else None,
        "due_at": item.due_at,
        "published_at": item.published_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "classes": [{"id": str(x.id), "name": x.name, "status": x.status} for x in classes],
        "paper_version": (
            {
                "id": str(pv.id),
                "version": pv.version,
                "status": pv.status,
                "pages": [
                    {
                        "id": str(x.id),
                        "stored_file_id": str(x.stored_file_id),
                        "page_number": x.page_number,
                        "source_page_number": x.source_page_number,
                        "width": x.width,
                        "height": x.height,
                        "rotation": x.rotation,
                        "status": x.status,
                    }
                    for x in pages
                ],
                "questions": [
                    question_json(
                        db,
                        question,
                        regions_by_question[question.id],
                        knowledge_points_by_question[question.id],
                    )
                    for question in qs
                ],
            }
            if pv
            else None
        ),
        "rubric_version": (
            {
                "id": str(rv.id),
                "version": rv.version,
                "status": rv.status,
                "question_rubrics": rubrics,
            }
            if rv
            else None
        ),
        "completeness": {
            "ready": not issues,
            "issues": issues,
            "next_step": issues[0]["step"] if issues else 6,
        },
    }


def publish_issues(
    db: Session,
    item: Assignment,
    *,
    preloaded_paper: PaperVersion | None = None,
    preloaded_rubric: RubricVersion | None = None,
    preloaded_questions: Sequence[Question] | None = None,
    preloaded_question_rubrics: list[QuestionRubric] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    links = db.scalars(
        select(AssignmentClass).where(AssignmentClass.assignment_id == item.id)
    ).all()
    if not links:
        out.append({"code": "NO_CLASSES", "message": "请选择班级", "step": 1})
    elif any(
        cls is None or cls.status != ArchiveStatus.active
        for cls in (db.get(SchoolClass, x.class_id) for x in links)
    ):
        out.append({"code": "CLASS_NOT_ACTIVE", "message": "关联班级已归档", "step": 1})
    pv = preloaded_paper if preloaded_questions is not None else paper(db, item)
    qs = (
        preloaded_questions
        if preloaded_questions is not None
        else (
            db.scalars(
                select(Question).where(
                    Question.paper_version_id == pv.id, Question.status == QuestionStatus.active
                )
            ).all()
            if pv
            else []
        )
    )
    if not pv or not qs:
        out.append({"code": "NO_QUESTIONS", "message": "请上传试卷并创建题目", "step": 4})
    missing_scores = [q for q in qs if q.max_score is None]
    for q in missing_scores:
        out.append(
            {
                "code": "QUESTION_SCORE_REQUIRED",
                "message": f"第 {q.question_number} 题分值未设置",
                "step": 4,
                "question_id": str(q.id),
                "question_number": q.question_number,
            }
        )
    total = sum((Decimal(q.max_score) for q in qs if q.max_score is not None), Decimal(0))
    if missing_scores:
        out.append(
            {
                "code": "ASSIGNMENT_TOTAL_SCORE_INCOMPLETE",
                "message": "存在分值未设置的题目，无法核对作业总分",
                "step": 4,
                "question_ids": [str(q.id) for q in missing_scores],
            }
        )
    elif item.total_score is None or Decimal(item.total_score) != total:
        out.append(
            {"code": "TOTAL_SCORE_MISMATCH", "message": "题目分值合计必须等于作业总分", "step": 4}
        )
    rv = preloaded_rubric if preloaded_questions is not None else rubric(db, item)
    if not rv:
        out.append({"code": "NO_RUBRIC", "message": "请设置评分标准", "step": 5})
    else:
        question_rubric_by_question = (
            {
                question_rubric.question_id: question_rubric
                for question_rubric in preloaded_question_rubrics
            }
            if preloaded_question_rubrics is not None
            else {}
        )
        rubric_points = (
            {
                question_rubric_id: Decimal(points or 0)
                for question_rubric_id, points in db.execute(
                    select(RubricItem.question_rubric_id, func.sum(RubricItem.points))
                    .where(
                        RubricItem.question_rubric_id.in_(
                            [
                                question_rubric.id
                                for question_rubric in preloaded_question_rubrics or []
                            ]
                        )
                    )
                    .group_by(RubricItem.question_rubric_id)
                )
            }
            if preloaded_question_rubrics is not None
            else {}
        )
        for q in qs:
            if q.max_score is None:
                continue
            qr = (
                question_rubric_by_question.get(q.id)
                if preloaded_question_rubrics is not None
                else db.scalar(
                    select(QuestionRubric).where(
                        QuestionRubric.rubric_version_id == rv.id,
                        QuestionRubric.question_id == q.id,
                    )
                )
            )
            if not qr or not qr.standard_answer:
                out.append(
                    {
                        "code": "RUBRIC_INCOMPLETE",
                        "message": f"第 {q.question_number} 题缺少标准答案",
                        "step": 5,
                        "question_id": str(q.id),
                    }
                )
                continue
            pts = (
                rubric_points.get(qr.id, Decimal(0))
                if preloaded_question_rubrics is not None
                else (
                    db.scalar(
                        select(func.sum(RubricItem.points)).where(
                            RubricItem.question_rubric_id == qr.id
                        )
                    )
                    or 0
                )
            )
            if Decimal(pts) != Decimal(q.max_score):
                out.append(
                    {
                        "code": "RUBRIC_POINTS_MISMATCH",
                        "message": f"第 {q.question_number} 题评分项分值不一致",
                        "step": 5,
                        "question_id": str(q.id),
                    }
                )
    return out


@router.get("")
def list_assignments(
    db: Db,
    actor: Actor,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = "",
    status: AssignmentStatus | None = None,
    class_id: uuid.UUID | None = None,
    subject: str | None = None,
    sort: Literal["updated_desc", "created_desc", "title_asc"] = "updated_desc",
) -> dict[str, Any]:
    filters: list[Any] = [Assignment.owner_id == actor.id]
    if search:
        filters.append(Assignment.title.ilike(f"%{search.strip()}%"))
    if status:
        filters.append(Assignment.status == status)
    if subject:
        filters.append(Assignment.subject == subject)
    if class_id:
        filters.append(
            Assignment.id.in_(
                select(AssignmentClass.assignment_id).where(AssignmentClass.class_id == class_id)
            )
        )
    total = db.scalar(select(func.count()).select_from(Assignment).where(*filters)) or 0
    order = (
        Assignment.title.asc()
        if sort == "title_asc"
        else (
            Assignment.created_at.desc() if sort == "created_desc" else Assignment.updated_at.desc()
        )
    )
    rows = db.scalars(
        select(Assignment)
        .where(*filters)
        .order_by(order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for x in rows:
        d = detail(db, x)
        items.append(
            {
                k: d[k]
                for k in [
                    "id",
                    "title",
                    "subject",
                    "grade",
                    "status",
                    "total_score",
                    "due_at",
                    "updated_at",
                    "classes",
                    "completeness",
                ]
            }
            | {
                "question_count": len(d.get("paper_version", {}).get("questions", []))
                if d.get("paper_version")
                else 0
            }
        )
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


@router.post("", status_code=201)
def create_assignment(data: AssignmentInput, db: Db, actor: Actor) -> dict[str, Any]:
    validate_classes(db, actor.id, data.class_ids)
    item = Assignment(
        owner_id=actor.id, **data.model_dump(exclude={"class_ids"}), status=AssignmentStatus.draft
    )
    db.add(item)
    db.flush()
    for cid in data.class_ids:
        db.add(AssignmentClass(assignment_id=item.id, class_id=cid))
    audit(db, actor.id, "assignment.create", "assignment", item.id)
    db.commit()
    db.refresh(item)
    return detail(db, item)


@router.get("/{assignment_id}")
def get_assignment(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return detail(db, owned(db, actor.id, assignment_id))


@router.patch("/{assignment_id}")
def update_assignment(
    assignment_id: uuid.UUID, data: AssignmentPatch, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    if item.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "已发布作业不能直接修改重要结构")
    if item.updated_at != data.updated_at:
        raise ApiProblem(409, "EDIT_CONFLICT", "作业已被其他窗口更新，请刷新后重试")
    changes = data.model_dump(exclude_unset=True, exclude={"updated_at"})
    for k, v in changes.items():
        setattr(item, k, v)
    audit(db, actor.id, "assignment.update", "assignment", item.id, {"fields": sorted(changes)})
    db.commit()
    db.refresh(item)
    return detail(db, item)


@router.put("/{assignment_id}/classes")
def set_classes(
    assignment_id: uuid.UUID, data: ClassesInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    if item.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "发布后不能修改班级")
    if item.updated_at != data.updated_at:
        raise ApiProblem(409, "EDIT_CONFLICT", "作业已被更新")
    validate_classes(db, actor.id, data.class_ids)
    db.execute(delete(AssignmentClass).where(AssignmentClass.assignment_id == item.id))
    for cid in data.class_ids:
        db.add(AssignmentClass(assignment_id=item.id, class_id=cid))
    item.updated_at = now_utc()
    audit(db, actor.id, "assignment.classes.update", "assignment", item.id)
    db.commit()
    return detail(db, item)


@router.post("/{assignment_id}/archive")
def archive(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    if item.status != AssignmentStatus.archived:
        item.status = AssignmentStatus.archived
        item.archived_at = now_utc()
        audit(db, actor.id, "assignment.archive", "assignment", item.id)
        db.commit()
    return detail(db, item)


@router.post("/{assignment_id}/restore")
def restore(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    if item.status == AssignmentStatus.archived:
        item.status = AssignmentStatus.draft
        item.archived_at = None
        audit(db, actor.id, "assignment.restore", "assignment", item.id)
        db.commit()
    return detail(db, item)


@router.post("/{assignment_id}/copy", status_code=201)
def copy_assignment(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    src = owned(db, actor.id, assignment_id)
    dst = Assignment(
        owner_id=actor.id,
        title=f"{src.title}（副本）",
        subject=src.subject,
        grade=src.grade,
        description=src.description,
        instructions=src.instructions,
        total_score=src.total_score,
        copied_from_id=src.id,
    )
    db.add(dst)
    db.flush()
    sp = paper(db, src)
    if sp:
        dp = PaperVersion(assignment_id=dst.id, version=1, created_by=actor.id, source_type="copy")
        db.add(dp)
        db.flush()
        dst.active_paper_version_id = dp.id
        mapping: dict[uuid.UUID, uuid.UUID] = {}
        for q in db.scalars(
            select(Question)
            .where(Question.paper_version_id == sp.id, Question.status == QuestionStatus.active)
            .order_by(Question.display_order)
        ).all():
            nq = Question(
                paper_version_id=dp.id,
                question_number=q.question_number,
                display_order=q.display_order,
                question_type=q.question_type,
                content_text=q.content_text,
                content_latex=q.content_latex,
                max_score=q.max_score,
                difficulty=q.difficulty,
                source="copy",
            )
            db.add(nq)
            db.flush()
            mapping[q.id] = nq.id
        sr = rubric(db, src)
        if sr:
            dr = RubricVersion(assignment_id=dst.id, version=1, created_by=actor.id)
            db.add(dr)
            db.flush()
            dst.active_rubric_version_id = dr.id
            for qr in db.scalars(
                select(QuestionRubric).where(QuestionRubric.rubric_version_id == sr.id)
            ).all():
                if qr.question_id not in mapping:
                    continue
                nqr = QuestionRubric(
                    rubric_version_id=dr.id,
                    question_id=mapping[qr.question_id],
                    standard_answer=qr.standard_answer,
                    alternative_answers=qr.alternative_answers,
                    scoring_notes=qr.scoring_notes,
                    allow_step_score=qr.allow_step_score,
                )
                db.add(nqr)
                db.flush()
                for ri in db.scalars(
                    select(RubricItem).where(RubricItem.question_rubric_id == qr.id)
                ).all():
                    db.add(
                        RubricItem(
                            question_rubric_id=nqr.id,
                            display_order=ri.display_order,
                            title=ri.title,
                            description=ri.description,
                            points=ri.points,
                            item_type=ri.item_type,
                            required=ri.required,
                            deduction_rule=ri.deduction_rule,
                        )
                    )
    audit(db, actor.id, "assignment.copy", "assignment", dst.id, {"copied_from_id": str(src.id)})
    db.commit()
    return detail(db, dst)


@router.post("/{assignment_id}/files", status_code=201)
async def upload(
    assignment_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    if item.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "只能为草稿上传试卷")
    s = get_settings()
    content = await file.read(s.assignment_max_file_bytes + 1)
    if not content:
        raise ApiProblem(422, "FILE_EMPTY", "文件为空")
    if len(content) > s.assignment_max_file_bytes:
        raise ApiProblem(413, "FILE_TOO_LARGE", "文件超过大小限制")
    pv = paper(db, item)
    if not pv:
        pv = PaperVersion(
            assignment_id=item.id, version=1, created_by=actor.id, source_type="upload"
        )
        db.add(pv)
        db.flush()
        item.active_paper_version_id = pv.id
    count = (
        db.scalar(
            select(func.count())
            .select_from(StoredFile)
            .join(PaperPage)
            .where(PaperPage.paper_version_id == pv.id, StoredFile.status == FileStatus.ready)
        )
        or 0
    )
    if count >= s.assignment_max_files:
        raise ApiProblem(422, "FILE_LIMIT", "上传文件数量已达上限")
    try:
        name = safe_filename(file.filename)
        inspection = inspect_upload(
            name,
            content,
            file.content_type,
            max_pdf_pages=s.recognition_max_pdf_pages,
            max_image_pixels=s.recognition_max_image_pixels,
            allow_docx=True,
        )
    except UnsafeFile as exc:
        status = 415 if exc.code in {"FILE_TYPE_INVALID", "FILE_CONTENT_INVALID"} else 422
        raise ApiProblem(status, exc.code, exc.message) from exc
    ext = inspection.kind
    key = f"assignments/{actor.id}/{item.id}/{pv.id}/{uuid.uuid4()}.{ext}"
    sf = StoredFile(
        owner_id=actor.id,
        storage_key=key,
        original_name=name,
        content_type=MIMES[ext],
        size=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
        status=FileStatus.pending,
    )
    db.add(sf)
    db.flush()
    try:
        storage.put(key, io.BytesIO(content), len(content), MIMES[ext])
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(key)
        except Exception:
            pass
        raise ApiProblem(503, "STORAGE_UNAVAILABLE", "对象存储不可用，文件未保存") from exc
    sf.status = FileStatus.ready
    existing = (
        db.scalar(
            select(func.count()).select_from(PaperPage).where(PaperPage.paper_version_id == pv.id)
        )
        or 0
    )
    width, height = inspection.width, inspection.height
    page_count = inspection.page_count
    for i in range(page_count):
        db.add(
            PaperPage(
                paper_version_id=pv.id,
                stored_file_id=sf.id,
                page_number=existing + i + 1,
                source_page_number=i + 1,
                width=width,
                height=height,
                status="pending_conversion" if ext == "docx" else "ready",
            )
        )
    audit(
        db,
        actor.id,
        "assignment.file.upload",
        "assignment",
        item.id,
        {"stored_file_id": str(sf.id), "pages": page_count},
    )
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(key)
        except Exception:
            pass
        raise ApiProblem(503, "FILE_SAVE_FAILED", "文件保存失败，未保留半成品") from exc
    return {
        "id": str(sf.id),
        "name": sf.original_name,
        "content_type": sf.content_type,
        "size": sf.size,
        "checksum": sf.checksum,
        "status": sf.status,
        "pages_created": page_count,
    }


@router.post("/{assignment_id}/files/{file_id}/preview")
def preview(
    assignment_id: uuid.UUID, file_id: uuid.UUID, db: Db, actor: Actor, storage: Storage
) -> dict[str, str]:
    owned(db, actor.id, assignment_id)
    sf = db.scalar(
        select(StoredFile)
        .join(PaperPage)
        .join(PaperVersion)
        .where(
            StoredFile.id == file_id,
            StoredFile.owner_id == actor.id,
            PaperVersion.assignment_id == assignment_id,
        )
    )
    if not sf:
        raise ApiProblem(404, "FILE_NOT_FOUND", "文件不存在")
    return {"url": storage.presigned_get(sf.storage_key, 900)}


@router.patch("/{assignment_id}/pages/{page_id}")
def patch_page(
    assignment_id: uuid.UUID, page_id: uuid.UUID, data: PagePatch, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    pp = db.scalar(
        select(PaperPage)
        .join(PaperVersion)
        .where(PaperPage.id == page_id, PaperVersion.assignment_id == item.id)
    )
    if not pp:
        raise ApiProblem(404, "PAGE_NOT_FOUND", "页面不存在")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(pp, k, v)
    audit(db, actor.id, "paper.page.update", "paper_page", pp.id)
    db.commit()
    return {
        "id": str(pp.id),
        "page_number": pp.page_number,
        "rotation": pp.rotation,
        "status": pp.status,
    }


@router.put("/{assignment_id}/pages/reorder")
def reorder_pages(
    assignment_id: uuid.UUID, data: ReorderInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    pv = paper(db, item)
    pages = db.scalars(
        select(PaperPage).where(PaperPage.paper_version_id == (pv.id if pv else None))
    ).all()
    if set(data.ids) != {x.id for x in pages}:
        raise ApiProblem(422, "PAGE_SET_INVALID", "页面集合不匹配")
    for i, pid in enumerate(data.ids, 1):
        current = db.get(PaperPage, pid)
        assert current is not None
        current.page_number = -i
    db.flush()
    for i, pid in enumerate(data.ids, 1):
        current = db.get(PaperPage, pid)
        assert current is not None
        current.page_number = i
    audit(db, actor.id, "paper.pages.reorder", "assignment", item.id)
    db.commit()
    return detail(db, item)


@router.post("/{assignment_id}/questions", status_code=201)
def create_question(
    assignment_id: uuid.UUID, data: QuestionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    pv = paper(db, item)
    if not pv:
        pv = PaperVersion(assignment_id=item.id, version=1, created_by=actor.id)
        db.add(pv)
        db.flush()
        item.active_paper_version_id = pv.id
    if data.question_type not in QUESTION_TYPES:
        raise ApiProblem(422, "QUESTION_TYPE_INVALID", "题型无效")
    order = (
        db.scalar(
            select(func.max(Question.display_order)).where(Question.paper_version_id == pv.id)
        )
        or 0
    ) + 1
    q = Question(
        paper_version_id=pv.id,
        display_order=order,
        **data.model_dump(exclude={"knowledge_points"}),
        source="manual",
    )
    db.add(q)
    db.flush()
    set_kps(db, actor.id, item, q, data.knowledge_points)
    audit(db, actor.id, "question.create", "question", q.id)
    db.commit()
    return question_json(db, q)


def set_kps(
    db: Session, actor_id: uuid.UUID, item: Assignment, q: Question, names: list[str]
) -> None:
    db.execute(delete(QuestionKnowledgePoint).where(QuestionKnowledgePoint.question_id == q.id))
    for name in dict.fromkeys(x.strip() for x in names if x.strip()):
        kp = db.scalar(
            select(KnowledgePoint).where(
                KnowledgePoint.owner_id == actor_id,
                KnowledgePoint.subject == item.subject,
                KnowledgePoint.grade == item.grade,
                KnowledgePoint.name == name,
            )
        )
        if not kp:
            kp = KnowledgePoint(
                owner_id=actor_id, subject=item.subject, grade=item.grade, name=name
            )
            db.add(kp)
            db.flush()
        db.add(QuestionKnowledgePoint(question_id=q.id, knowledge_point_id=kp.id))


@router.patch("/{assignment_id}/questions/{question_id}")
def patch_question(
    assignment_id: uuid.UUID, question_id: uuid.UUID, data: QuestionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    q = db.scalar(
        select(Question)
        .join(PaperVersion)
        .where(Question.id == question_id, PaperVersion.assignment_id == item.id)
    )
    if not q:
        raise ApiProblem(404, "QUESTION_NOT_FOUND", "题目不存在")
    for k, v in data.model_dump(exclude={"knowledge_points", "parent_question_id"}).items():
        setattr(q, k, v)
    q.parent_question_id = data.parent_question_id
    set_kps(db, actor.id, item, q, data.knowledge_points)
    stale_for_question(db, q.id, "QUESTION_CONTENT_CHANGED")
    audit(db, actor.id, "question.update", "question", q.id)
    db.commit()
    return question_json(db, q)


@router.delete("/{assignment_id}/questions/{question_id}", status_code=204)
def remove_question(assignment_id: uuid.UUID, question_id: uuid.UUID, db: Db, actor: Actor) -> None:
    item = owned(db, actor.id, assignment_id)
    q = db.scalar(
        select(Question)
        .join(PaperVersion)
        .where(Question.id == question_id, PaperVersion.assignment_id == item.id)
    )
    if not q:
        raise ApiProblem(404, "QUESTION_NOT_FOUND", "题目不存在")
    q.status = QuestionStatus.removed
    stale_for_question(db, q.id, "QUESTION_STATUS_CHANGED")
    audit(db, actor.id, "question.remove", "question", q.id)
    db.commit()


@router.put("/{assignment_id}/questions/reorder")
def reorder_questions(
    assignment_id: uuid.UUID, data: ReorderInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    pv = paper(db, item)
    qs = db.scalars(
        select(Question).where(
            Question.paper_version_id == (pv.id if pv else None),
            Question.status == QuestionStatus.active,
        )
    ).all()
    if set(data.ids) != {x.id for x in qs}:
        raise ApiProblem(422, "QUESTION_SET_INVALID", "题目集合不匹配")
    for i, qid in enumerate(data.ids, 1):
        current = db.get(Question, qid)
        assert current is not None
        current.display_order = i
    audit(db, actor.id, "questions.reorder", "assignment", item.id)
    db.commit()
    return detail(db, item)


@router.post("/{assignment_id}/questions/{question_id}/regions", status_code=201)
def add_region(
    assignment_id: uuid.UUID, question_id: uuid.UUID, data: RegionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    q = db.scalar(
        select(Question)
        .join(PaperVersion)
        .where(Question.id == question_id, PaperVersion.assignment_id == item.id)
    )
    page = db.scalar(
        select(PaperPage)
        .join(PaperVersion)
        .where(PaperPage.id == data.paper_page_id, PaperVersion.assignment_id == item.id)
    )
    if not q or not page or q.paper_version_id != page.paper_version_id:
        raise ApiProblem(422, "REGION_TARGET_INVALID", "题目和页面必须属于同一试卷版本")
    r = QuestionRegion(question_id=q.id, source="manual", **data.model_dump())
    db.add(r)
    db.flush()
    audit(db, actor.id, "question.region.create", "question_region", r.id)
    db.commit()
    return {"id": str(r.id), **data.model_dump(mode="json"), "source": "manual"}


@router.delete("/{assignment_id}/regions/{region_id}", status_code=204)
def remove_region(assignment_id: uuid.UUID, region_id: uuid.UUID, db: Db, actor: Actor) -> None:
    item = owned(db, actor.id, assignment_id)
    r = db.scalar(
        select(QuestionRegion)
        .join(Question)
        .join(PaperVersion)
        .where(QuestionRegion.id == region_id, PaperVersion.assignment_id == item.id)
    )
    if not r:
        raise ApiProblem(404, "REGION_NOT_FOUND", "区域不存在")
    db.delete(r)
    audit(db, actor.id, "question.region.delete", "question_region", r.id)
    db.commit()


@router.put("/{assignment_id}/rubrics/{question_id}")
def put_rubric(
    assignment_id: uuid.UUID, question_id: uuid.UUID, data: RubricInput, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    q = db.scalar(
        select(Question)
        .join(PaperVersion)
        .where(Question.id == question_id, PaperVersion.assignment_id == item.id)
    )
    if not q:
        raise ApiProblem(422, "RUBRIC_QUESTION_INVALID", "题目不属于当前试卷")
    if q.max_score is None:
        raise ApiProblem(
            422,
            "QUESTION_SCORE_REQUIRED",
            f"第 {q.question_number} 题分值未设置，不能确认评分标准",
            {"question_id": str(q.id), "question_number": q.question_number, "step": 4},
        )
    rv = rubric(db, item)
    if rv and rv.status == VersionStatus.confirmed:
        rv = clone_rubric_version(db, rv, actor.id)
        item.active_rubric_version_id = rv.id
    elif not rv:
        rv = RubricVersion(assignment_id=item.id, version=1, created_by=actor.id)
        db.add(rv)
        db.flush()
        item.active_rubric_version_id = rv.id
    qr = db.scalar(
        select(QuestionRubric).where(
            QuestionRubric.rubric_version_id == rv.id, QuestionRubric.question_id == q.id
        )
    )
    values = data.model_dump(exclude={"items"})
    if not qr:
        qr = QuestionRubric(rubric_version_id=rv.id, question_id=q.id, **values)
        db.add(qr)
        db.flush()
    else:
        for k, v in values.items():
            setattr(qr, k, v)
        db.execute(delete(RubricItem).where(RubricItem.question_rubric_id == qr.id))
    for i, raw in enumerate(data.items, 1):
        points = Decimal(str(raw.get("points", 0)))
        if points < 0:
            raise ApiProblem(422, "NEGATIVE_RUBRIC_POINTS", "评分项分值不能为负")
        db.add(
            RubricItem(
                question_rubric_id=qr.id,
                display_order=i,
                title=str(raw.get("title", f"评分点 {i}")),
                description=raw.get("description"),
                points=points,
                item_type=str(raw.get("item_type", "step")),
                required=bool(raw.get("required", False)),
                deduction_rule=raw.get("deduction_rule"),
            )
        )
    invalidated = invalidate_grading_for_rubric_change(db, item.id)
    audit(
        db,
        actor.id,
        "rubric.update",
        "rubric_version",
        rv.id,
        {"invalidated_grading_results": invalidated},
    )
    db.commit()
    return detail(db, item)


@router.get("/{assignment_id}/publish-check")
def check_publish(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id)
    issues = publish_issues(db, item)
    return {"ready": not issues, "issues": issues}


@router.post("/{assignment_id}/publish")
def publish_assignment(
    assignment_id: uuid.UUID,
    data: PublishInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    # Import locally to keep the legacy completeness checker reusable by the
    # central-review service without introducing an import cycle.
    from app.api.assignment_central_review import teacher_publish

    return detail(db, teacher_publish(db, actor.id, assignment_id, data))


class RecognitionRegion(BaseModel):
    paper_page_id: uuid.UUID
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class QuestionCandidate(BaseModel):
    temporary_id: str
    question_number: str | None = None
    question_type: str | None = None
    content_text: str | None = None
    content_latex: str | None = None
    max_score: Decimal | None = None
    confidence: float = Field(ge=0, le=1)
    regions: list[RecognitionRegion]


class RecognitionResult(BaseModel):
    paper_version_id: uuid.UUID
    pages: list[dict[str, Any]]
    question_candidates: list[QuestionCandidate]
