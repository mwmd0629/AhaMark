import uuid
from decimal import Decimal
from typing import Annotated, Any

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.api.student_portal import _linked_students, _visible_release_rows
from app.db.session import get_db
from app.models import (
    Assignment,
    GradeRelease,
    KnowledgePoint,
    Question,
    Role,
    SchoolClass,
    ScoreRevision,
    Student,
    StudentAnswer,
    StudentReviewRequest,
    TeacherReview,
    UserRole,
    now_utc,
)
from app.results.services import release_scores
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(tags=["student-review-requests"])
Db = Annotated[Session, Depends(get_db)]


class CreateReviewRequestInput(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ResolveReviewRequestInput(BaseModel):
    resolution: str
    response: str = Field(min_length=1, max_length=4000)
    new_score: Decimal | None = Field(default=None, ge=0)
    new_feedback: str | None = Field(default=None, max_length=4000)


def _require_teacher(db: Session, actor_id: uuid.UUID) -> None:
    role_names = set(
        db.scalars(
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == actor_id)
        )
    )
    if role_names and role_names != {"teacher"}:
        raise ApiProblem(403, "TEACHER_ROLE_REQUIRED", "仅教师账号可以处理学生复核申请")


def _wrong_question_source(
    db: Session,
    actor_id: uuid.UUID,
    release_id: uuid.UUID,
    question_id: uuid.UUID,
) -> tuple[GradeRelease, Student, Assignment, SchoolClass, Any, TeacherReview, StudentAnswer]:
    rows = _visible_release_rows(db, actor_id, release_id)
    if len(rows) != 1:
        raise ApiProblem(404, "STUDENT_GRADE_NOT_FOUND", "成绩不存在或尚未向学生开放")
    release, item, student, assignment, school_class = rows[0]
    scores = [
        value
        for value in release_scores(db, release.id)
        if value.payload.student_id == student.id and value.snapshot.id == item.score_snapshot_id
    ]
    if len(scores) != 1:
        raise ApiProblem(409, "STUDENT_GRADE_SOURCE_INVALID", "正式成绩来源无效")
    details = [value for value in scores[0].payload.details if value.question_id == question_id]
    if len(details) != 1 or details[0].score >= details[0].max_score:
        raise ApiProblem(404, "WRONG_QUESTION_NOT_FOUND", "该题不在当前正式错题记录中")
    detail = details[0]
    review = db.get(TeacherReview, detail.teacher_review_id)
    answer = db.get(StudentAnswer, review.student_answer_id) if review is not None else None
    if (
        review is None
        or answer is None
        or answer.submission_id != item.submission_id
        or answer.question_id != question_id
    ):
        raise ApiProblem(409, "STUDENT_GRADE_SOURCE_INVALID", "错题关联数据不完整")
    return release, student, assignment, school_class, detail, review, answer


def _request_view(db: Session, item: StudentReviewRequest) -> dict[str, Any]:
    release = db.get(GradeRelease, item.grade_release_id)
    student = db.get(Student, item.student_id)
    answer = db.get(StudentAnswer, item.student_answer_id)
    question = db.get(Question, answer.question_id) if answer is not None else None
    assignment = db.get(Assignment, release.assignment_id) if release is not None else None
    school_class = db.get(SchoolClass, release.class_id) if release is not None else None
    if (
        release is None
        or student is None
        or answer is None
        or question is None
        or assignment is None
        or school_class is None
    ):
        raise ApiProblem(409, "REVIEW_REQUEST_SOURCE_INVALID", "复核申请关联数据不完整")
    return {
        "id": str(item.id),
        "status": item.status,
        "resolution": item.resolution,
        "message": item.message,
        "teacher_response": item.teacher_response,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "resolved_at": item.resolved_at,
        "grade_release_id": str(item.grade_release_id),
        "grade_release_version": release.version,
        "score_snapshot_id": str(item.score_snapshot_id),
        "student_id": str(student.id),
        "student_name": student.name,
        "student_number": student.student_number,
        "assignment_id": str(assignment.id),
        "assignment_title": assignment.title,
        "class_id": str(school_class.id),
        "class_name": school_class.name,
        "student_answer_id": str(answer.id),
        "question_id": str(question.id),
        "question_number": question.question_number,
        "question_content": question.content_text or question.content_latex,
        "student_answer": (
            answer.corrected_text
            or answer.corrected_latex
            or answer.recognized_text
            or answer.recognized_latex
        ),
    }


@router.get("/api/student/wrong-questions")
def student_wrong_questions(db: Db, actor: Actor) -> list[dict[str, Any]]:
    _linked_students(db, actor.id)
    latest: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], tuple[Any, ...]] = {}
    for row in _visible_release_rows(db, actor.id):
        release, _item, student, assignment, school_class = row
        latest.setdefault((assignment.id, school_class.id, student.id), row)
    requests = list(
        db.scalars(
            select(StudentReviewRequest)
            .where(StudentReviewRequest.requested_by == actor.id)
            .order_by(StudentReviewRequest.created_at.desc())
        )
    )
    request_by_source = {
        (item.grade_release_id, item.student_answer_id): item for item in requests
    }
    result: list[dict[str, Any]] = []
    for release, item, student, assignment, school_class in latest.values():
        scores = [
            value
            for value in release_scores(db, release.id)
            if value.payload.student_id == student.id
            and value.snapshot.id == item.score_snapshot_id
        ]
        if len(scores) != 1:
            raise ApiProblem(409, "STUDENT_GRADE_SOURCE_INVALID", "正式成绩来源无效")
        for detail in scores[0].payload.details:
            if detail.score >= detail.max_score:
                continue
            review = db.get(TeacherReview, detail.teacher_review_id)
            answer = db.get(StudentAnswer, review.student_answer_id) if review else None
            question = db.get(Question, detail.question_id)
            if review is None or answer is None or question is None:
                raise ApiProblem(409, "STUDENT_GRADE_SOURCE_INVALID", "错题关联数据不完整")
            existing = request_by_source.get((release.id, answer.id))
            knowledge = {
                point.id: point.name
                for point in db.scalars(
                    select(KnowledgePoint).where(
                        KnowledgePoint.id.in_(detail.knowledge_point_ids)
                    )
                )
            }
            result.append(
                {
                    "id": f"{item.score_snapshot_id}:{detail.question_id}",
                    "grade_release_id": str(release.id),
                    "assignment_title": assignment.title,
                    "class_name": school_class.name,
                    "question_id": str(detail.question_id),
                    "question_number": detail.question_number,
                    "question_content": question.content_text or question.content_latex,
                    "student_answer": answer.corrected_text
                    or answer.corrected_latex
                    or answer.recognized_text
                    or answer.recognized_latex,
                    "score": str(detail.score),
                    "max_score": str(detail.max_score),
                    "feedback": detail.final_feedback,
                    "error_type": detail.final_error_type,
                    "knowledge_points": [
                        {"id": str(point_id), "name": knowledge.get(point_id, "未命名知识点")}
                        for point_id in detail.knowledge_point_ids
                    ],
                    "review_request": (
                        {
                            "id": str(existing.id),
                            "status": existing.status,
                            "resolution": existing.resolution,
                            "teacher_response": existing.teacher_response,
                        }
                        if existing
                        else None
                    ),
                }
            )
    return result


@router.get("/api/student/review-requests")
def student_review_requests(db: Db, actor: Actor) -> list[dict[str, Any]]:
    _linked_students(db, actor.id)
    rows = db.scalars(
        select(StudentReviewRequest)
        .where(StudentReviewRequest.requested_by == actor.id)
        .order_by(StudentReviewRequest.created_at.desc())
    ).all()
    return [_request_view(db, item) for item in rows]


@router.post(
    "/api/student/wrong-questions/{release_id}/{question_id}/review-requests",
    status_code=201,
)
def create_student_review_request(
    release_id: uuid.UUID,
    question_id: uuid.UUID,
    data: CreateReviewRequestInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    release, student, _assignment, _school_class, _detail, _review, answer = (
        _wrong_question_source(db, actor.id, release_id, question_id)
    )
    existing = db.scalar(
        select(StudentReviewRequest).where(
            StudentReviewRequest.requested_by == actor.id,
            StudentReviewRequest.grade_release_id == release.id,
            StudentReviewRequest.student_answer_id == answer.id,
            StudentReviewRequest.status.in_(("pending", "needs_information")),
        )
    )
    if existing is not None:
        raise ApiProblem(409, "REVIEW_REQUEST_ALREADY_OPEN", "该题已有待处理复核申请")
    item = StudentReviewRequest(
        owner_id=release.owner_id,
        student_id=student.id,
        requested_by=actor.id,
        grade_release_id=release.id,
        score_snapshot_id=_visible_release_rows(db, actor.id, release.id)[0][1].score_snapshot_id,
        student_answer_id=answer.id,
        message=data.message.strip(),
    )
    db.add(item)
    db.flush()
    audit(
        db,
        actor.id,
        "student.review_request.create",
        "student_review_request",
        item.id,
        {"grade_release_id": str(release.id), "question_id": str(question_id)},
    )
    db.commit()
    db.refresh(item)
    return _request_view(db, item)


@router.get("/api/teacher/review-requests")
def teacher_review_requests(
    db: Db,
    actor: Actor,
    status: Annotated[str | None, Query(max_length=24)] = None,
) -> list[dict[str, Any]]:
    _require_teacher(db, actor.id)
    query = select(StudentReviewRequest).where(StudentReviewRequest.owner_id == actor.id)
    if status is not None:
        query = query.where(StudentReviewRequest.status == status)
    rows = db.scalars(query.order_by(StudentReviewRequest.created_at.asc())).all()
    return [_request_view(db, item) for item in rows]


@router.patch("/api/teacher/review-requests/{request_id}")
def resolve_teacher_review_request(
    request_id: uuid.UUID,
    data: ResolveReviewRequestInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    _require_teacher(db, actor.id)
    if data.resolution not in {"upheld", "score_changed", "needs_information"}:
        raise ApiProblem(422, "REVIEW_RESOLUTION_INVALID", "复核处理结果无效")
    item = db.scalar(
        select(StudentReviewRequest)
        .where(
            StudentReviewRequest.id == request_id,
            StudentReviewRequest.owner_id == actor.id,
        )
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "REVIEW_REQUEST_NOT_FOUND", "复核申请不存在")
    if item.status not in {"pending", "needs_information"}:
        raise ApiProblem(409, "REVIEW_REQUEST_ALREADY_RESOLVED", "复核申请已处理")
    answer = db.get(StudentAnswer, item.student_answer_id)
    review = db.scalar(
        select(TeacherReview)
        .where(TeacherReview.student_answer_id == item.student_answer_id)
        .with_for_update()
    )
    question = db.get(Question, answer.question_id) if answer is not None else None
    if answer is None or review is None or question is None:
        raise ApiProblem(409, "REVIEW_REQUEST_SOURCE_INVALID", "复核申请关联数据不完整")
    if data.resolution == "score_changed":
        if data.new_score is None:
            raise ApiProblem(422, "FINAL_SCORE_REQUIRED", "修改分数时必须填写新分数")
        if question.max_score is None or data.new_score > Decimal(question.max_score):
            raise ApiProblem(422, "SCORE_OUT_OF_RANGE", "新分数超出题目分值范围")
        db.add(
            ScoreRevision(
                teacher_review_id=review.id,
                student_answer_id=answer.id,
                actor_id=actor.id,
                previous_score=review.final_score,
                new_score=data.new_score,
                previous_feedback=review.final_feedback,
                new_feedback=data.new_feedback,
                reason=f"学生复核申请：{data.response.strip()}",
            )
        )
        review.final_score = data.new_score
        review.final_feedback = data.new_feedback
        review.reviewer_id = actor.id
        review.decision = "modified"
        review.review_notes = data.response.strip()
        review.confirmed_at = now_utc()
        review.review_version += 1
    item.resolution = data.resolution
    item.teacher_response = data.response.strip()
    if data.resolution == "needs_information":
        item.status = "needs_information"
        item.resolved_at = None
    else:
        item.status = "resolved"
        item.resolved_at = now_utc()
    audit(
        db,
        actor.id,
        "student.review_request.resolve",
        "student_review_request",
        item.id,
        {
            "resolution": data.resolution,
            "new_score": str(data.new_score) if data.new_score is not None else None,
            "published_snapshot_unchanged": True,
        },
    )
    db.commit()
    db.refresh(item)
    return _request_view(db, item)
