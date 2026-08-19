import uuid
from typing import Annotated, Any, cast

from app.api.actor import Actor
from app.api.domain import ApiProblem
from app.db.session import get_db
from app.models import (
    Assignment,
    GradeRelease,
    KnowledgePoint,
    Question,
    SchoolClass,
    Student,
    StudentAnswer,
    TeacherReview,
    User,
)
from app.results.services import release_scores
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/teacher", tags=["teacher-practice"])
Db = Annotated[Session, Depends(get_db)]
MAX_RELEASE_SOURCES = 500


def _require_teacher(db: Session, actor_id: uuid.UUID) -> None:
    user = db.get(User, actor_id)
    if user is None:
        raise ApiProblem(404, "USER_NOT_FOUND", "用户不存在")
    roles = {role.name for role in user.roles}
    if roles and roles != {"teacher"}:
        raise ApiProblem(403, "TEACHER_ROLE_REQUIRED", "仅教师账号可以查看班级错题")


def _latest_release_rows(
    db: Session,
    actor_id: uuid.UUID,
    class_id: uuid.UUID | None,
    assignment_id: uuid.UUID | None,
) -> list[tuple[GradeRelease, Assignment, SchoolClass]]:
    latest = (
        select(
            GradeRelease.assignment_id.label("assignment_id"),
            GradeRelease.class_id.label("class_id"),
            func.max(GradeRelease.version).label("version"),
        )
        .where(
            GradeRelease.owner_id == actor_id,
            GradeRelease.status == "released",
        )
        .group_by(GradeRelease.assignment_id, GradeRelease.class_id)
        .subquery()
    )
    statement = (
        select(GradeRelease, Assignment, SchoolClass)
        .join(
            latest,
            (latest.c.assignment_id == GradeRelease.assignment_id)
            & (latest.c.class_id == GradeRelease.class_id)
            & (latest.c.version == GradeRelease.version),
        )
        .join(Assignment, Assignment.id == GradeRelease.assignment_id)
        .join(SchoolClass, SchoolClass.id == GradeRelease.class_id)
        .where(
            GradeRelease.owner_id == actor_id,
            GradeRelease.status == "released",
            Assignment.owner_id == actor_id,
            SchoolClass.owner_id == actor_id,
        )
    )
    if class_id is not None:
        statement = statement.where(GradeRelease.class_id == class_id)
    if assignment_id is not None:
        statement = statement.where(GradeRelease.assignment_id == assignment_id)
    rows = cast(
        list[tuple[GradeRelease, Assignment, SchoolClass]],
        db.execute(
            statement.order_by(
                GradeRelease.released_at.desc(),
                GradeRelease.created_at.desc(),
                GradeRelease.id.desc(),
            ).limit(MAX_RELEASE_SOURCES + 1)
        ).all(),
    )
    if len(rows) > MAX_RELEASE_SOURCES:
        raise ApiProblem(
            422,
            "PRACTICE_FILTER_REQUIRED",
            "已发布作业较多，请先按班级或作业筛选",
            {"source_limit": MAX_RELEASE_SOURCES},
        )
    return rows


@router.get("/wrong-questions")
def list_wrong_questions(
    db: Db,
    actor: Actor,
    class_id: uuid.UUID | None = None,
    assignment_id: uuid.UUID | None = None,
    error_type: Annotated[str | None, Query(max_length=80)] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    _require_teacher(db, actor.id)
    release_rows = _latest_release_rows(db, actor.id, class_id, assignment_id)
    source_items: list[dict[str, Any]] = []
    for release, assignment, school_class in release_rows:
        try:
            scores = release_scores(db, release.id)
        except ValueError as exc:
            raise ApiProblem(
                409,
                "PRACTICE_SOURCE_INVALID",
                "已发布成绩来源不完整，暂时不能生成错题视图",
                {"grade_release_id": str(release.id)},
            ) from exc
        for score in sorted(scores, key=lambda value: str(value.payload.student_id)):
            for detail in sorted(score.payload.details, key=lambda value: value.question_number):
                if detail.score >= detail.max_score:
                    continue
                source_items.append(
                    {
                        "id": f"{score.snapshot.id}:{detail.question_id}",
                        "grade_release_id": str(release.id),
                        "grade_release_version": release.version,
                        "released_at": release.released_at,
                        "assignment_id": str(assignment.id),
                        "assignment_title": assignment.title,
                        "class_id": str(school_class.id),
                        "class_name": school_class.name,
                        "student_id": str(score.payload.student_id),
                        "submission_id": str(score.submission.id),
                        "grading_batch_id": str(score.submission.grading_batch_id),
                        "teacher_review_id": str(detail.teacher_review_id),
                        "question_id": str(detail.question_id),
                        "question_number": detail.question_number,
                        "question_type": detail.question_type,
                        "score": str(detail.score),
                        "max_score": str(detail.max_score),
                        "score_rate": float(detail.score / detail.max_score),
                        "error_type": detail.final_error_type,
                        "feedback": detail.final_feedback,
                        "knowledge_point_ids": [str(value) for value in detail.knowledge_point_ids],
                        "snapshot_id": str(score.snapshot.id),
                    }
                )

    student_ids = {uuid.UUID(item["student_id"]) for item in source_items}
    question_ids = {uuid.UUID(item["question_id"]) for item in source_items}
    review_ids = {uuid.UUID(item["teacher_review_id"]) for item in source_items}
    knowledge_ids = {
        uuid.UUID(value) for item in source_items for value in item["knowledge_point_ids"]
    }
    students = {
        str(item.id): item
        for item in db.scalars(select(Student).where(Student.id.in_(student_ids)))
    }
    questions = {
        str(item.id): item
        for item in db.scalars(select(Question).where(Question.id.in_(question_ids)))
    }
    reviews = {
        str(item.id): item
        for item in db.scalars(select(TeacherReview).where(TeacherReview.id.in_(review_ids)))
    }
    answer_ids = {item.student_answer_id for item in reviews.values()}
    answers = {
        str(item.id): item
        for item in db.scalars(select(StudentAnswer).where(StudentAnswer.id.in_(answer_ids)))
    }
    knowledge = {
        str(item.id): item.name
        for item in db.scalars(
            select(KnowledgePoint).where(
                KnowledgePoint.id.in_(knowledge_ids),
                KnowledgePoint.owner_id == actor.id,
            )
        )
    }

    hydrated: list[dict[str, Any]] = []
    for item in source_items:
        student = students.get(item["student_id"])
        question = questions.get(item["question_id"])
        review = reviews.get(item["teacher_review_id"])
        answer = answers.get(str(review.student_answer_id)) if review is not None else None
        if (
            student is None
            or question is None
            or student.owner_id != actor.id
            or review is None
            or answer is None
            or answer.submission_id != uuid.UUID(item["submission_id"])
            or answer.question_id != uuid.UUID(item["question_id"])
            or any(value not in knowledge for value in item["knowledge_point_ids"])
        ):
            raise ApiProblem(409, "PRACTICE_SOURCE_INVALID", "错题关联数据不完整")
        item.pop("teacher_review_id")
        item.update(
            {
                "student_name": student.name,
                "student_number": student.student_number,
                "student_answer_id": str(answer.id),
                "question_content": question.content_text or question.content_latex,
                "student_answer": (
                    answer.corrected_text
                    or answer.corrected_latex
                    or answer.recognized_text
                    or answer.recognized_latex
                    if answer is not None
                    else None
                ),
                "knowledge_points": [
                    {"id": value, "name": knowledge.get(value, value)}
                    for value in item["knowledge_point_ids"]
                ],
            }
        )
        hydrated.append(item)

    class_facets = sorted(
        {(item["class_id"], item["class_name"]) for item in hydrated},
        key=lambda value: (value[1], value[0]),
    )
    assignment_facets = sorted(
        {(item["assignment_id"], item["assignment_title"]) for item in hydrated},
        key=lambda value: (value[1], value[0]),
    )
    error_type_facets = sorted({str(item["error_type"]) for item in hydrated if item["error_type"]})
    normalized_search = search.strip().casefold() if search else ""
    normalized_error = error_type.strip().casefold() if error_type else ""
    filtered = []
    for item in hydrated:
        if normalized_error and str(item["error_type"] or "").casefold() != normalized_error:
            continue
        if normalized_search:
            searchable = " ".join(
                str(value or "")
                for value in (
                    item["student_name"],
                    item["student_number"],
                    item["assignment_title"],
                    item["class_name"],
                    item["question_number"],
                    item["question_content"],
                    item["student_answer"],
                    item["error_type"],
                    " ".join(point["name"] for point in item["knowledge_points"]),
                )
            ).casefold()
            if normalized_search not in searchable:
                continue
        filtered.append(item)

    total = len(filtered)
    start = (page - 1) * page_size
    visible = filtered[start : start + page_size]
    return {
        "items": visible,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "summary": {
            "total_wrong_questions": total,
            "affected_students": len({item["student_id"] for item in filtered}),
            "knowledge_point_count": len(
                {point["id"] for item in filtered for point in item["knowledge_points"]}
            ),
            "average_score_rate": (
                sum(item["score_rate"] for item in filtered) / total if total else None
            ),
        },
        "facets": {
            "classes": [{"id": value[0], "name": value[1]} for value in class_facets],
            "assignments": [{"id": value[0], "title": value[1]} for value in assignment_facets],
            "error_types": error_type_facets,
        },
    }
