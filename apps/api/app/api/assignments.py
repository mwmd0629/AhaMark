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
    AssignmentParticipantSnapshot,
    AssignmentReviewSession,
    AssignmentStatus,
    ClassStudent,
    FileStatus,
    GradingCollaborator,
    KnowledgePoint,
    MembershipStatus,
    PaperPage,
    PaperVersion,
    Question,
    QuestionKnowledgePoint,
    QuestionRegion,
    QuestionStatus,
    ReferenceAnswerVersion,
    RubricCriterion,
    SchoolClass,
    StoredFile,
    StructuredRubricSet,
    StructuredRubricSetItem,
    StructuredRubricVersion,
    Student,
    User,
    now_utc,
)
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.semantic_content import semantic_hash
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
    delivery_mode: Literal["class_assignment", "joint_exam"] = "class_assignment"


class AssignmentPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    subject: str | None = Field(None, max_length=40)
    grade: str | None = Field(None, max_length=40)
    description: str | None = Field(None, max_length=4000)
    instructions: str | None = Field(None, max_length=4000)
    total_score: Decimal | None = Field(None, gt=0)
    due_at: datetime | None = None
    delivery_mode: Literal["class_assignment", "joint_exam"] | None = None
    updated_at: datetime


class ClassesInput(BaseModel):
    class_ids: list[uuid.UUID]
    updated_at: datetime


class JointExamCollaboratorInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class JointExamClassesInput(BaseModel):
    class_ids: list[uuid.UUID] = Field(min_length=1)


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


def owned(
    db: Session,
    actor_id: uuid.UUID,
    assignment_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Assignment:
    query = select(Assignment).where(
        Assignment.id == assignment_id, Assignment.owner_id == actor_id
    )
    if lock:
        query = query.execution_options(populate_existing=True).with_for_update()
    item = db.scalar(query)
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


def _active_teacher_collaborator(db: Session, assignment_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    collaborator = db.scalar(
        select(GradingCollaborator.id).where(
            GradingCollaborator.assignment_id == assignment_id,
            GradingCollaborator.user_id == user_id,
            GradingCollaborator.status == "active",
        )
    )
    user = db.get(User, user_id) if collaborator is not None else None
    return bool(
        collaborator is not None
        and user is not None
        and user.status == "active"
        and "teacher" in {role.name for role in user.roles}
    )


def joint_exam_access(db: Session, actor_id: uuid.UUID, assignment_id: uuid.UUID) -> Assignment:
    item = db.get(Assignment, assignment_id)
    if item is None or item.delivery_mode != "joint_exam":
        raise ApiProblem(404, "JOINT_EXAM_NOT_FOUND", "联考不存在")
    if item.owner_id == actor_id:
        return item
    if not _active_teacher_collaborator(db, item.id, actor_id):
        raise ApiProblem(404, "JOINT_EXAM_NOT_FOUND", "联考不存在")
    return item


def joint_exam_team_json(db: Session, item: Assignment, actor_id: uuid.UUID) -> dict[str, Any]:
    owner = db.get(User, item.owner_id)
    collaborators = db.execute(
        select(GradingCollaborator, User)
        .join(User, User.id == GradingCollaborator.user_id)
        .where(
            GradingCollaborator.assignment_id == item.id,
            GradingCollaborator.status == "active",
        )
        .order_by(User.display_name, User.email)
    ).all()
    active_collaborators = [
        (row, user)
        for row, user in collaborators
        if _active_teacher_collaborator(db, item.id, user.id)
    ]
    class_rows = db.execute(
        select(AssignmentClass, SchoolClass, User)
        .join(SchoolClass, SchoolClass.id == AssignmentClass.class_id)
        .join(User, User.id == SchoolClass.owner_id)
        .where(AssignmentClass.assignment_id == item.id)
        .order_by(SchoolClass.name, SchoolClass.id)
    ).all()
    return {
        "assignment_id": str(item.id),
        "title": item.title,
        "status": item.status,
        "is_owner": item.owner_id == actor_id,
        "owner": {
            "id": str(item.owner_id),
            "display_name": owner.display_name if owner else "主责老师",
            "email": owner.email if owner else None,
        },
        "collaborators": [
            {
                "id": str(row.user_id),
                "display_name": user.display_name,
                "email": user.email,
                "role": row.role,
            }
            for row, user in active_collaborators
        ],
        "classes": [
            {
                "id": str(cls.id),
                "name": cls.name,
                "owner_id": str(cls.owner_id),
                "owner_name": class_owner.display_name,
                "authorized_by": str(link.authorized_by) if link.authorized_by else None,
                "authorized": cls.owner_id == item.owner_id or link.authorized_by == cls.owner_id,
                "mine": cls.owner_id == actor_id,
            }
            for link, cls, class_owner in class_rows
        ],
    }


def paper(db: Session, item: Assignment) -> PaperVersion | None:
    return (
        db.get(PaperVersion, item.active_paper_version_id) if item.active_paper_version_id else None
    )


def structured_rubric_set(db: Session, item: Assignment) -> StructuredRubricSet | None:
    return (
        db.get(StructuredRubricSet, item.active_structured_rubric_set_id)
        if item.active_structured_rubric_set_id
        else None
    )


def _criterion_json(criterion: RubricCriterion) -> dict[str, Any]:
    return {
        "id": str(criterion.id),
        "stable_key": criterion.stable_key,
        "title": criterion.title,
        "description": criterion.description,
        "max_points": str(criterion.max_points),
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


def _structured_set_json(db: Session, rubric_set: StructuredRubricSet) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(StructuredRubricSetItem)
            .where(StructuredRubricSetItem.rubric_set_id == rubric_set.id)
            .order_by(StructuredRubricSetItem.display_order, StructuredRubricSetItem.id)
        )
    )
    rubric_ids = [row.structured_rubric_version_id for row in rows]
    criteria_by_rubric: dict[uuid.UUID, list[RubricCriterion]] = {
        rubric_id: [] for rubric_id in rubric_ids
    }
    if rubric_ids:
        for criterion in db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id.in_(rubric_ids))
            .order_by(
                RubricCriterion.rubric_version_id,
                RubricCriterion.display_order,
                RubricCriterion.id,
            )
        ):
            criteria_by_rubric[criterion.rubric_version_id].append(criterion)
    return {
        "id": str(rubric_set.id),
        "version": rubric_set.version,
        "status": rubric_set.status,
        "paper_version_id": str(rubric_set.paper_version_id),
        "content_hash": rubric_set.content_hash,
        "source_snapshot_hash": rubric_set.source_snapshot_hash,
        "total_points": str(rubric_set.total_points),
        "confirmed_by": str(rubric_set.confirmed_by) if rubric_set.confirmed_by else None,
        "confirmed_at": rubric_set.confirmed_at,
        "activated_at": rubric_set.activated_at,
        "items": [
            {
                "id": str(row.id),
                "question_id": str(row.question_id),
                "question_version": row.question_version,
                "reference_answer_version_id": str(row.reference_answer_version_id),
                "structured_rubric_version_id": str(row.structured_rubric_version_id),
                "answer_content_hash": row.answer_content_hash,
                "rubric_content_hash": row.rubric_content_hash,
                "criteria_hash": row.criteria_hash,
                "display_order": row.display_order,
                "max_points": str(row.max_points),
                "criteria": [
                    _criterion_json(criterion)
                    for criterion in criteria_by_rubric[row.structured_rubric_version_id]
                ],
            }
            for row in rows
        ],
    }


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
    rubric_set = structured_rubric_set(db, item)
    pages = (
        db.scalars(
            select(PaperPage)
            .where(PaperPage.paper_version_id == pv.id)
            .order_by(PaperPage.page_number)
        ).all()
        if pv
        else []
    )
    stored_file_names = {
        stored_file.id: stored_file.original_name
        for stored_file in (
            db.scalars(
                select(StoredFile).where(StoredFile.id.in_({page.stored_file_id for page in pages}))
            ).all()
            if pages
            else []
        )
    }
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
    issues = publish_issues(db, item, preloaded_paper=pv, preloaded_questions=qs)
    participant_rows = db.execute(
        select(
            AssignmentParticipantSnapshot.class_id,
            func.count(AssignmentParticipantSnapshot.id),
            func.min(AssignmentParticipantSnapshot.frozen_at),
        )
        .where(AssignmentParticipantSnapshot.assignment_id == item.id)
        .group_by(AssignmentParticipantSnapshot.class_id)
    ).all()
    participant_by_class = {str(class_id): count for class_id, count, _ in participant_rows}
    frozen_at = min((row[2] for row in participant_rows), default=None)
    return {
        "id": str(item.id),
        "title": item.title,
        "delivery_mode": item.delivery_mode,
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
        "participant_snapshot": {
            "frozen": bool(participant_rows),
            "frozen_at": frozen_at,
            "total": sum(participant_by_class.values()),
            "by_class": participant_by_class,
        },
        "paper_version": (
            {
                "id": str(pv.id),
                "version": pv.version,
                "status": pv.status,
                "pages": [
                    {
                        "id": str(x.id),
                        "stored_file_id": str(x.stored_file_id),
                        "file_name": stored_file_names.get(x.stored_file_id),
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
        "structured_rubric_set": _structured_set_json(db, rubric_set) if rubric_set else None,
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
    preloaded_questions: Sequence[Question] | None = None,
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
    if item.delivery_mode == "joint_exam" and len(links) < 2:
        out.append(
            {
                "code": "JOINT_EXAM_CLASSES_REQUIRED",
                "message": "联考统批至少需要两个班级",
                "step": 1,
            }
        )
    if item.delivery_mode == "joint_exam" and links:
        class_ids = [link.class_id for link in links]
        classes_by_id = {
            cls.id: cls
            for cls in db.scalars(select(SchoolClass).where(SchoolClass.id.in_(class_ids))).all()
        }
        unauthorized_class_ids = [
            str(link.class_id)
            for link in links
            if (cls := classes_by_id.get(link.class_id)) is not None
            and cls.owner_id != item.owner_id
            and link.authorized_by != cls.owner_id
        ]
        if unauthorized_class_ids:
            out.append(
                {
                    "code": "JOINT_EXAM_CLASS_AUTHORIZATION_REQUIRED",
                    "message": "跨教师班级需要由班级负责人授权",
                    "step": 1,
                    "class_ids": unauthorized_class_ids,
                }
            )
        participant_rows = db.execute(
            select(ClassStudent.class_id, Student.id)
            .join(Student, Student.id == ClassStudent.student_id)
            .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
            .where(
                ClassStudent.class_id.in_(class_ids),
                ClassStudent.status == MembershipStatus.active,
                Student.owner_id == SchoolClass.owner_id,
                Student.status == ArchiveStatus.active,
            )
        ).all()
        populated_class_ids = {class_id for class_id, _ in participant_rows}
        empty_class_ids = [
            str(class_id) for class_id in class_ids if class_id not in populated_class_ids
        ]
        if empty_class_ids:
            out.append(
                {
                    "code": "JOINT_EXAM_EMPTY_CLASS",
                    "message": "联考班级需要先加入在读学生",
                    "step": 1,
                    "class_ids": empty_class_ids,
                }
            )
        class_ids_by_student: dict[uuid.UUID, set[uuid.UUID]] = {}
        for class_id, student_id in participant_rows:
            class_ids_by_student.setdefault(student_id, set()).add(class_id)
        duplicate_student_ids = [
            str(student_id)
            for student_id, student_class_ids in class_ids_by_student.items()
            if len(student_class_ids) > 1
        ]
        if duplicate_student_ids:
            out.append(
                {
                    "code": "JOINT_EXAM_DUPLICATE_STUDENT",
                    "message": "同一学生不能重复加入本次联考的多个班级",
                    "step": 1,
                    "student_ids": duplicate_student_ids,
                }
            )
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
    rubric_set = structured_rubric_set(db, item)
    if rubric_set is None:
        out.append(
            {
                "code": "STRUCTURED_RUBRIC_SET_REQUIRED",
                "message": "请先准备完整的结构化评分标准发布包",
                "step": 5,
            }
        )
        return out
    if (
        rubric_set.owner_id != item.owner_id
        or rubric_set.assignment_id != item.id
        or pv is None
        or rubric_set.paper_version_id != pv.id
    ):
        out.append(
            {
                "code": "STRUCTURED_RUBRIC_SET_STALE",
                "message": "结构化评分标准发布包与当前作业或试卷不一致",
                "step": 5,
            }
        )
        return out
    session = db.scalar(
        select(AssignmentReviewSession)
        .where(
            AssignmentReviewSession.assignment_id == item.id,
            AssignmentReviewSession.owner_id == item.owner_id,
            AssignmentReviewSession.structured_rubric_set_id == rubric_set.id,
            AssignmentReviewSession.invalidated_at.is_(None),
        )
        .order_by(AssignmentReviewSession.review_version.desc(), AssignmentReviewSession.id)
    )
    if session is None:
        out.append(
            {
                "code": "STRUCTURED_RUBRIC_SET_REVIEW_REQUIRED",
                "message": "结构化评分标准发布包缺少当前发布核查记录",
                "step": 5,
            }
        )
        return out
    from app.api.assignment_central_review import validate_current_structured_set_under_locks

    validation = validate_current_structured_set_under_locks(
        db,
        session,
        rubric_set_id=rubric_set.id,
        lock=False,
        require_confirmed=False,
    )
    if not validation.current:
        out.append(
            {
                "code": "STRUCTURED_RUBRIC_SET_STALE",
                "message": "结构化评分标准发布包内容或指纹已变化",
                "step": 5,
                "reason": validation.reason,
            }
        )
        return out
    set_items = list(
        db.scalars(
            select(StructuredRubricSetItem)
            .where(StructuredRubricSetItem.rubric_set_id == rubric_set.id)
            .order_by(StructuredRubricSetItem.display_order, StructuredRubricSetItem.id)
        )
    )
    question_by_id = {question.id: question for question in qs}
    if {set_item.question_id for set_item in set_items} != set(question_by_id):
        out.append(
            {
                "code": "STRUCTURED_RUBRIC_SET_INCOMPLETE",
                "message": "结构化评分标准发布包未完整覆盖当前题目",
                "step": 5,
            }
        )
        return out
    for set_item in set_items:
        question = question_by_id[set_item.question_id]
        reference = db.get(ReferenceAnswerVersion, set_item.reference_answer_version_id)
        rubric = db.get(StructuredRubricVersion, set_item.structured_rubric_version_id)
        if (
            question.max_score is None
            or Decimal(set_item.max_points) != Decimal(question.max_score)
            or reference is None
            or rubric is None
            or reference.question_id != question.id
            or rubric.question_id != question.id
            or rubric.reference_answer_version_id != reference.id
        ):
            out.append(
                {
                    "code": "STRUCTURED_RUBRIC_SET_INCOMPLETE",
                    "message": f"第 {question.question_number} 题的固定答案或评分标准不完整",
                    "step": 5,
                    "question_id": str(question.id),
                }
            )
        elif reference.status != "confirmed" or rubric.status != "confirmed":
            out.append(
                {
                    "code": "STRUCTURED_RUBRIC_FORMAL_NOT_CONFIRMED",
                    "message": f"第 {question.question_number} 题的答案或评分标准尚未由教师确认",
                    "step": 5,
                    "question_id": str(question.id),
                }
            )
    return out


def freeze_participant_roster(db: Session, item: Assignment) -> int:
    """Freeze a joint exam roster in the same transaction as publication."""
    if item.delivery_mode != "joint_exam":
        return 0
    existing_count = (
        db.scalar(
            select(func.count())
            .select_from(AssignmentParticipantSnapshot)
            .where(AssignmentParticipantSnapshot.assignment_id == item.id)
        )
        or 0
    )
    if existing_count:
        return existing_count
    class_ids = list(
        db.scalars(select(AssignmentClass.class_id).where(AssignmentClass.assignment_id == item.id))
    )
    rows = db.execute(
        select(ClassStudent, Student)
        .join(Student, Student.id == ClassStudent.student_id)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(
            ClassStudent.class_id.in_(class_ids),
            ClassStudent.status == MembershipStatus.active,
            Student.owner_id == SchoolClass.owner_id,
            Student.status == ArchiveStatus.active,
        )
        .order_by(ClassStudent.class_id, Student.student_number, Student.id)
    ).all()
    frozen_at = now_utc()
    for membership, student in rows:
        db.add(
            AssignmentParticipantSnapshot(
                assignment_id=item.id,
                class_id=membership.class_id,
                student_id=student.id,
                student_number=student.student_number,
                student_name=student.name,
                membership_joined_at=membership.joined_at,
                frozen_at=frozen_at,
            )
        )
    db.flush()
    return len(rows)


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
                    "delivery_mode",
                    "subject",
                    "grade",
                    "status",
                    "total_score",
                    "due_at",
                    "updated_at",
                    "classes",
                    "participant_snapshot",
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
        db.add(AssignmentClass(assignment_id=item.id, class_id=cid, authorized_by=actor.id))
    audit(db, actor.id, "assignment.create", "assignment", item.id)
    db.commit()
    db.refresh(item)
    return detail(db, item)


@router.get("/joint-exams/invitations")
def list_joint_exam_invitations(db: Db, actor: Actor) -> list[dict[str, Any]]:
    items = db.scalars(
        select(Assignment)
        .join(GradingCollaborator, GradingCollaborator.assignment_id == Assignment.id)
        .where(
            GradingCollaborator.user_id == actor.id,
            GradingCollaborator.status == "active",
            Assignment.delivery_mode == "joint_exam",
            Assignment.status != AssignmentStatus.archived,
        )
        .order_by(Assignment.updated_at.desc(), Assignment.id)
    ).all()
    return [
        joint_exam_team_json(db, item, actor.id)
        for item in items
        if _active_teacher_collaborator(db, item.id, actor.id)
    ]


@router.get("/{assignment_id}/joint-team")
def get_joint_exam_team(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return joint_exam_team_json(db, joint_exam_access(db, actor.id, assignment_id), actor.id)


@router.post("/{assignment_id}/joint-team/collaborators", status_code=201)
def invite_joint_exam_collaborator(
    assignment_id: uuid.UUID,
    data: JointExamCollaboratorInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id, lock=True)
    if item.delivery_mode != "joint_exam":
        raise ApiProblem(409, "NOT_JOINT_EXAM", "该作业不是联考")
    if item.status == AssignmentStatus.archived:
        raise ApiProblem(409, "ASSIGNMENT_ARCHIVED", "已归档联考不能邀请教师")
    user = db.scalar(select(User).where(func.lower(User.email) == data.email.strip().lower()))
    if user is None or user.status != "active":
        raise ApiProblem(404, "COLLABORATOR_NOT_FOUND", "未找到可用的教师账号")
    if "teacher" not in {role.name for role in user.roles}:
        raise ApiProblem(422, "COLLABORATOR_TEACHER_REQUIRED", "仅教师账号可参与联考")
    if user.id == actor.id:
        raise ApiProblem(422, "OWNER_ALREADY_LEADS", "主责老师无需邀请自己")
    row = db.scalar(
        select(GradingCollaborator).where(
            GradingCollaborator.assignment_id == item.id,
            GradingCollaborator.user_id == user.id,
        )
    )
    if row is None:
        row = GradingCollaborator(
            assignment_id=item.id,
            user_id=user.id,
            added_by=actor.id,
            role="grader",
            status="active",
        )
        db.add(row)
    else:
        row.status, row.added_by = "active", actor.id
    audit(
        db,
        actor.id,
        "joint_exam.collaborator.invite",
        "assignment",
        item.id,
        {"collaborator_id": str(user.id)},
    )
    db.commit()
    return joint_exam_team_json(db, item, actor.id)


@router.post("/{assignment_id}/joint-classes", status_code=201)
def authorize_joint_exam_classes(
    assignment_id: uuid.UUID,
    data: JointExamClassesInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    item = joint_exam_access(db, actor.id, assignment_id)
    if item.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "发布后不能更改联考班级")
    classes = validate_classes(db, actor.id, data.class_ids)
    existing = {
        row.class_id: row
        for row in db.scalars(
            select(AssignmentClass).where(
                AssignmentClass.assignment_id == item.id,
                AssignmentClass.class_id.in_(data.class_ids),
            )
        ).all()
    }
    for cls in classes:
        row = existing.get(cls.id)
        if row is None:
            db.add(
                AssignmentClass(
                    assignment_id=item.id,
                    class_id=cls.id,
                    authorized_by=actor.id,
                )
            )
        else:
            row.authorized_by = actor.id
    item.updated_at = now_utc()
    audit(
        db,
        actor.id,
        "joint_exam.classes.authorize",
        "assignment",
        item.id,
        {"class_ids": [str(cls.id) for cls in classes]},
    )
    db.commit()
    return joint_exam_team_json(db, item, actor.id)


@router.delete("/{assignment_id}/joint-classes/{class_id}")
def remove_joint_exam_class(
    assignment_id: uuid.UUID,
    class_id: uuid.UUID,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    item = joint_exam_access(db, actor.id, assignment_id)
    if item.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "发布后不能更改联考班级")
    cls = db.get(SchoolClass, class_id)
    if cls is None or (actor.id not in {item.owner_id, cls.owner_id}):
        raise ApiProblem(404, "JOINT_EXAM_CLASS_NOT_FOUND", "联考班级不存在")
    link = db.scalar(
        select(AssignmentClass).where(
            AssignmentClass.assignment_id == item.id,
            AssignmentClass.class_id == class_id,
        )
    )
    if link is None:
        raise ApiProblem(404, "JOINT_EXAM_CLASS_NOT_FOUND", "联考班级不存在")
    db.delete(link)
    item.updated_at = now_utc()
    audit(
        db,
        actor.id,
        "joint_exam.classes.remove",
        "assignment",
        item.id,
        {"class_id": str(class_id), "class_owner_id": str(cls.owner_id)},
    )
    db.commit()
    return joint_exam_team_json(db, item, actor.id)


@router.get("/{assignment_id}")
def get_assignment(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return detail(db, owned(db, actor.id, assignment_id))


@router.patch("/{assignment_id}")
def update_assignment(
    assignment_id: uuid.UUID, data: AssignmentPatch, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id, lock=True)
    if item.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "已发布作业不能直接修改重要结构")
    if item.updated_at != data.updated_at:
        raise ApiProblem(409, "EDIT_CONFLICT", "作业已被其他窗口更新，请刷新后重试")
    changes = data.model_dump(exclude_unset=True, exclude={"updated_at"})
    if "delivery_mode" in changes and changes["delivery_mode"] != item.delivery_mode:
        frozen_count = db.scalar(
            select(func.count())
            .select_from(AssignmentParticipantSnapshot)
            .where(AssignmentParticipantSnapshot.assignment_id == item.id)
        )
        if frozen_count:
            raise ApiProblem(409, "JOINT_EXAM_ROSTER_FROZEN", "联考名单冻结后不能更改布置方式")
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
    item = owned(db, actor.id, assignment_id, lock=True)
    if item.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "发布后不能修改班级")
    if item.updated_at != data.updated_at:
        raise ApiProblem(409, "EDIT_CONFLICT", "作业已被更新")
    frozen_count = db.scalar(
        select(func.count())
        .select_from(AssignmentParticipantSnapshot)
        .where(AssignmentParticipantSnapshot.assignment_id == item.id)
    )
    if frozen_count:
        raise ApiProblem(409, "JOINT_EXAM_ROSTER_FROZEN", "联考名单冻结后不能更改班级范围")
    validate_classes(db, actor.id, data.class_ids)
    if item.delivery_mode == "joint_exam":
        owned_class_ids = select(SchoolClass.id).where(SchoolClass.owner_id == actor.id)
        db.execute(
            delete(AssignmentClass).where(
                AssignmentClass.assignment_id == item.id,
                AssignmentClass.class_id.in_(owned_class_ids),
            )
        )
    else:
        db.execute(delete(AssignmentClass).where(AssignmentClass.assignment_id == item.id))
    for cid in data.class_ids:
        db.add(AssignmentClass(assignment_id=item.id, class_id=cid, authorized_by=actor.id))
    item.updated_at = now_utc()
    audit(db, actor.id, "assignment.classes.update", "assignment", item.id)
    db.commit()
    return detail(db, item)


@router.post("/{assignment_id}/archive")
def archive(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id, lock=True)
    if item.status != AssignmentStatus.archived:
        item.status = AssignmentStatus.archived
        item.archived_at = now_utc()
        audit(db, actor.id, "assignment.archive", "assignment", item.id)
        db.commit()
    return detail(db, item)


@router.post("/{assignment_id}/restore")
def restore(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id, lock=True)
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
        delivery_mode=src.delivery_mode,
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
        source_set = structured_rubric_set(db, src)
        if source_set:
            source_items = list(
                db.scalars(
                    select(StructuredRubricSetItem)
                    .where(StructuredRubricSetItem.rubric_set_id == source_set.id)
                    .order_by(
                        StructuredRubricSetItem.display_order,
                        StructuredRubricSetItem.id,
                    )
                )
            )
            copied_items: list[dict[str, Any]] = []
            for source_item in source_items:
                target_question_id = mapping.get(source_item.question_id)
                source_answer = db.get(
                    ReferenceAnswerVersion, source_item.reference_answer_version_id
                )
                source_rubric = db.get(
                    StructuredRubricVersion, source_item.structured_rubric_version_id
                )
                target_question = (
                    db.get(Question, target_question_id) if target_question_id else None
                )
                if source_answer is None or source_rubric is None or target_question is None:
                    raise ApiProblem(
                        409,
                        "STRUCTURED_RUBRIC_SET_INCOMPLETE",
                        "源作业的结构化评分标准发布包不完整，无法复制",
                    )
                answer_payload = {
                    "source_type": "copy",
                    "source_file": source_answer.source_file,
                    "source_page": source_answer.source_page,
                    "source_region": source_answer.source_region,
                    "raw_content": source_answer.raw_content,
                    "normalized_content": source_answer.normalized_content,
                    "structured_content": source_answer.structured_content,
                    "provenance": {
                        **(source_answer.provenance or {}),
                        "copied_from_reference_answer_version_id": str(source_answer.id),
                    },
                }
                copied_answer = ReferenceAnswerVersion(
                    question_id=target_question.id,
                    version=1,
                    created_by=actor.id,
                    status="draft",
                    content_hash=semantic_hash(answer_payload),
                    **answer_payload,
                )
                db.add(copied_answer)
                db.flush()
                question_version = (
                    f"{target_question.paper_version_id}:{target_question.updated_at.isoformat()}"
                )
                copied_rubric = StructuredRubricVersion(
                    question_id=target_question.id,
                    question_version=question_version,
                    reference_answer_version_id=copied_answer.id,
                    rubric_version=1,
                    title=source_rubric.title,
                    total_points=source_rubric.total_points,
                    status="draft",
                    content_hash="0" * 64,
                    created_by=actor.id,
                )
                db.add(copied_rubric)
                db.flush()
                copied_criteria: list[RubricCriterion] = []
                for source_criterion in db.scalars(
                    select(RubricCriterion)
                    .where(RubricCriterion.rubric_version_id == source_rubric.id)
                    .order_by(RubricCriterion.display_order, RubricCriterion.id)
                ):
                    copied_criterion = RubricCriterion(
                        rubric_version_id=copied_rubric.id,
                        stable_key=source_criterion.stable_key,
                        title=source_criterion.title,
                        description=source_criterion.description,
                        max_points=source_criterion.max_points,
                        display_order=source_criterion.display_order,
                        criterion_type=source_criterion.criterion_type,
                        required=source_criterion.required,
                        dependencies=list(source_criterion.dependencies or []),
                        expected_evidence=dict(source_criterion.expected_evidence or {}),
                        validation_mode=source_criterion.validation_mode,
                        validation_rule=dict(source_criterion.validation_rule or {}),
                        manual_review_policy=dict(source_criterion.manual_review_policy or {}),
                        partial_credit_policy=dict(source_criterion.partial_credit_policy or {}),
                        error_category=source_criterion.error_category,
                        metadata_=dict(source_criterion.metadata_ or {}),
                    )
                    db.add(copied_criterion)
                    copied_criteria.append(copied_criterion)
                db.flush()
                criteria_payload = [_criterion_json(row) for row in copied_criteria]
                rubric_payload = {
                    "question_version": copied_rubric.question_version,
                    "title": copied_rubric.title,
                    "total_points": str(copied_rubric.total_points),
                    "reference_answer_version_id": str(copied_answer.id),
                    "criteria": [
                        {
                            "id": row["id"],
                            "key": row["stable_key"],
                            "title": row["title"],
                            "description": row["description"],
                            "points": row["max_points"],
                            "display_order": row["display_order"],
                            "criterion_type": row["criterion_type"],
                            "required": row["required"],
                            "dependencies": row["dependencies"],
                            "expected_evidence": row["expected_evidence"],
                            "validation_mode": row["validation_mode"],
                            "validation_rule": row["validation_rule"],
                            "manual_review_policy": row["manual_review_policy"],
                            "partial_credit_policy": row["partial_credit_policy"],
                            "error_category": row["error_category"],
                            "metadata": row["metadata"],
                        }
                        for row in criteria_payload
                    ],
                }
                copied_rubric.content_hash = semantic_hash(rubric_payload)
                copied_items.append(
                    {
                        "question_id": target_question.id,
                        "question_version": question_version,
                        "reference_answer_version_id": copied_answer.id,
                        "structured_rubric_version_id": copied_rubric.id,
                        "answer_content_hash": copied_answer.content_hash,
                        "rubric_content_hash": copied_rubric.content_hash,
                        "criteria_hash": semantic_hash(rubric_payload["criteria"]),
                        "display_order": source_item.display_order,
                        "max_points": source_item.max_points,
                    }
                )
            if copied_items:
                source_snapshot_hash = semantic_hash(
                    {
                        "copied_from_assignment_id": str(src.id),
                        "copied_from_structured_rubric_set_id": str(source_set.id),
                        "copied_from_content_hash": source_set.content_hash,
                    }
                )
                set_payload = {
                    "assignment_id": str(dst.id),
                    "paper_version_id": str(dp.id),
                    "source_snapshot_hash": source_snapshot_hash,
                    "items": [
                        {
                            key: str(value) if isinstance(value, uuid.UUID) else value
                            for key, value in row.items()
                        }
                        for row in copied_items
                    ],
                }
                copied_set = StructuredRubricSet(
                    owner_id=actor.id,
                    assignment_id=dst.id,
                    paper_version_id=dp.id,
                    version=1,
                    status="draft",
                    content_hash=semantic_hash(set_payload),
                    source_snapshot_hash=source_snapshot_hash,
                    total_points=sum(
                        (Decimal(row["max_points"]) for row in copied_items), Decimal(0)
                    ),
                    created_by=actor.id,
                )
                db.add(copied_set)
                db.flush()
                for row in copied_items:
                    db.add(StructuredRubricSetItem(rubric_set_id=copied_set.id, **row))
                dst.active_structured_rubric_set_id = copied_set.id
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
    item = owned(db, actor.id, assignment_id, lock=True)
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
            allow_docx=False,
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
                status="ready",
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


@router.delete("/{assignment_id}/files/{file_id}")
def delete_file(
    assignment_id: uuid.UUID, file_id: uuid.UUID, db: Db, actor: Actor, storage: Storage
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id, lock=True)
    if item.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "只能删除草稿中的试卷文件")
    pv = paper(db, item)
    if not pv:
        raise ApiProblem(404, "FILE_NOT_FOUND", "文件不存在或已删除")
    sf = db.scalar(
        select(StoredFile)
        .join(PaperPage)
        .where(
            StoredFile.id == file_id,
            StoredFile.owner_id == actor.id,
            StoredFile.status.in_([FileStatus.ready, FileStatus.pending]),
            PaperPage.paper_version_id == pv.id,
        )
    )
    if not sf:
        raise ApiProblem(404, "FILE_NOT_FOUND", "文件不存在或已删除")

    page_ids = list(
        db.scalars(
            select(PaperPage.id).where(
                PaperPage.paper_version_id == pv.id,
                PaperPage.stored_file_id == sf.id,
            )
        ).all()
    )
    storage_key = sf.storage_key
    if sf.status == FileStatus.ready:
        # Persist a recoverable deletion marker before touching object storage. Keeping
        # the pages until the object delete succeeds also preserves the exact
        # assignment/file authorization link for retries.
        sf.status = FileStatus.pending
        audit(
            db,
            actor.id,
            "assignment.file.delete_requested",
            "assignment",
            item.id,
            {"stored_file_id": str(sf.id), "pages_pending": len(page_ids)},
        )
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            raise ApiProblem(
                503,
                "FILE_DELETE_PREPARE_FAILED",
                "文件删除准备失败，对象未删除",
            ) from exc

    try:
        # Delete only the exact key authorized through this assignment's PaperPage.
        # Object deletion is required to be idempotent so a pending request can retry.
        storage.delete(storage_key)
    except Exception as exc:
        raise ApiProblem(
            503,
            "STORAGE_UNAVAILABLE",
            "对象存储不可用，删除已排队且可重试",
        ) from exc

    db.execute(delete(PaperPage).where(PaperPage.id.in_(page_ids)))
    sf.status = FileStatus.deleted
    db.flush()
    remaining = db.scalars(
        select(PaperPage)
        .where(PaperPage.paper_version_id == pv.id)
        .order_by(PaperPage.page_number, PaperPage.id)
    ).all()
    for index, current in enumerate(remaining, 1):
        current.page_number = index
    audit(
        db,
        actor.id,
        "assignment.file.delete",
        "assignment",
        item.id,
        {"stored_file_id": str(sf.id), "pages_deleted": len(page_ids)},
    )
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise ApiProblem(
            503,
            "FILE_DELETE_FINALIZE_FAILED",
            "对象已删除，数据库收尾待重试",
        ) from exc
    return {"id": str(sf.id), "pages_deleted": len(page_ids)}


@router.patch("/{assignment_id}/pages/{page_id}")
def patch_page(
    assignment_id: uuid.UUID, page_id: uuid.UUID, data: PagePatch, db: Db, actor: Actor
) -> dict[str, Any]:
    item = owned(db, actor.id, assignment_id, lock=True)
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
    item = owned(db, actor.id, assignment_id, lock=True)
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
    item = owned(db, actor.id, assignment_id, lock=True)
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
    item = owned(db, actor.id, assignment_id, lock=True)
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
    item = owned(db, actor.id, assignment_id, lock=True)
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
    item = owned(db, actor.id, assignment_id, lock=True)
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
    item = owned(db, actor.id, assignment_id, lock=True)
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
    item = owned(db, actor.id, assignment_id, lock=True)
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
    # Import locally so the Structured-only publication service can reuse this
    # module's assignment and roster helpers without an import cycle.
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
