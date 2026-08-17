import uuid
from pathlib import Path
from typing import Annotated, Any, cast

from app.api.actor import Actor
from app.api.domain import ApiProblem
from app.db.session import get_db
from app.models import (
    ArchiveStatus,
    Assignment,
    GradeRelease,
    GradeReleaseItem,
    KnowledgePoint,
    Role,
    SchoolClass,
    Student,
    Submission,
    UserRole,
)
from app.results.services import release_scores, student_report_pdf
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/student", tags=["student-portal"])
Db = Annotated[Session, Depends(get_db)]


def _linked_students(db: Session, actor_id: uuid.UUID) -> list[Student]:
    students = list(
        db.scalars(
            select(Student)
            .join(UserRole, UserRole.user_id == Student.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Student.user_id == actor_id, Student.status == ArchiveStatus.active)
            .where(Role.name == "student")
            .order_by(Student.student_number, Student.id)
        )
    )
    if not students:
        raise ApiProblem(403, "STUDENT_ACCOUNT_NOT_LINKED", "当前账号尚未关联学生档案")
    return students


def _visible_release_rows(
    db: Session, actor_id: uuid.UUID, release_id: uuid.UUID | None = None
) -> list[tuple[GradeRelease, GradeReleaseItem, Student, Assignment, SchoolClass]]:
    query = (
        select(GradeRelease, GradeReleaseItem, Student, Assignment, SchoolClass)
        .join(GradeReleaseItem, GradeReleaseItem.grade_release_id == GradeRelease.id)
        .join(Student, Student.id == GradeReleaseItem.student_id)
        .join(Assignment, Assignment.id == GradeRelease.assignment_id)
        .join(SchoolClass, SchoolClass.id == GradeRelease.class_id)
        .where(
            Student.user_id == actor_id,
            Student.status == ArchiveStatus.active,
            Student.owner_id == GradeRelease.owner_id,
            GradeRelease.status == "released",
            GradeRelease.student_visible_at.is_not(None),
            GradeReleaseItem.status == "included",
        )
    )
    if release_id is not None:
        query = query.where(GradeRelease.id == release_id)
    rows = cast(
        list[tuple[GradeRelease, GradeReleaseItem, Student, Assignment, SchoolClass]],
        db.execute(
            query.order_by(
                GradeRelease.version.desc(),
                GradeRelease.student_visible_at.desc(),
                GradeRelease.id.desc(),
            )
        ).all(),
    )
    effective: dict[
        tuple[uuid.UUID, uuid.UUID],
        tuple[int, tuple[GradeRelease, GradeReleaseItem, Student, Assignment, SchoolClass]],
    ] = {}
    for row in rows:
        release, item, student, _assignment, _school_class = row
        submission = db.get(Submission, item.submission_id)
        if submission is None or submission.status == "voided":
            continue
        key = (release.id, student.id)
        current = effective.get(key)
        if current is None or submission.attempt_number > current[0]:
            effective[key] = (submission.attempt_number, row)
    selected = {id(row): row for _attempt, row in effective.values()}
    return [row for row in rows if id(row) in selected]


def _release_summary(
    release: GradeRelease,
    item: GradeReleaseItem,
    student: Student,
    assignment: Assignment,
    school_class: SchoolClass,
) -> dict[str, Any]:
    return {
        "release_id": str(release.id),
        "release_version": release.version,
        "student_visible_at": release.student_visible_at,
        "assignment_id": str(assignment.id),
        "assignment_title": assignment.title,
        "class_id": str(school_class.id),
        "class_name": school_class.name,
        "subject": school_class.subject,
        "student_id": str(student.id),
        "student_name": student.name,
        "student_number": student.student_number,
        "score_snapshot_id": str(item.score_snapshot_id),
    }


@router.get("/me")
def student_me(db: Db, actor: Actor) -> dict[str, Any]:
    students = _linked_students(db, actor.id)
    return {
        "account_id": str(actor.id),
        "email": actor.email,
        "profiles": [
            {
                "student_id": str(student.id),
                "name": student.name,
                "student_number": student.student_number,
            }
            for student in students
        ],
    }


@router.get("/assignments")
def student_assignments(db: Db, actor: Actor) -> list[dict[str, Any]]:
    _linked_students(db, actor.id)
    rows = _visible_release_rows(db, actor.id)
    latest: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], dict[str, Any]] = {}
    for release, item, student, assignment, school_class in rows:
        key = (assignment.id, school_class.id, student.id)
        latest.setdefault(
            key,
            _release_summary(release, item, student, assignment, school_class),
        )
    return list(latest.values())


def _owned_visible_release(
    db: Session, actor_id: uuid.UUID, release_id: uuid.UUID
) -> tuple[GradeRelease, GradeReleaseItem, Student, Assignment, SchoolClass]:
    rows = _visible_release_rows(db, actor_id, release_id)
    if len(rows) != 1:
        raise ApiProblem(404, "STUDENT_GRADE_NOT_FOUND", "成绩不存在或尚未向学生开放")
    return rows[0]


@router.get("/assignments/{release_id}")
def student_assignment_detail(release_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    release, item, student, assignment, school_class = _owned_visible_release(
        db, actor.id, release_id
    )
    scores = [
        row
        for row in release_scores(db, release.id)
        if row.payload.student_id == student.id and row.snapshot.id == item.score_snapshot_id
    ]
    if len(scores) != 1:
        raise ApiProblem(409, "STUDENT_GRADE_SOURCE_INVALID", "正式成绩来源无效")
    score = scores[0]
    knowledge_ids = {
        point_id for detail in score.payload.details for point_id in detail.knowledge_point_ids
    }
    knowledge_names = {
        point.id: point.name
        for point in db.scalars(select(KnowledgePoint).where(KnowledgePoint.id.in_(knowledge_ids)))
    }
    visible_versions = list(
        db.execute(
            select(GradeRelease, GradeReleaseItem)
            .join(GradeReleaseItem, GradeReleaseItem.grade_release_id == GradeRelease.id)
            .where(
                GradeRelease.assignment_id == release.assignment_id,
                GradeRelease.class_id == release.class_id,
                GradeRelease.owner_id == student.owner_id,
                GradeRelease.status == "released",
                GradeRelease.student_visible_at.is_not(None),
                GradeReleaseItem.student_id == student.id,
                GradeReleaseItem.status == "included",
            )
            .order_by(GradeRelease.version.desc())
        ).all()
    )
    unique_versions: dict[uuid.UUID, tuple[GradeRelease, GradeReleaseItem]] = {}
    for version, version_item in visible_versions:
        unique_versions.setdefault(version.id, (version, version_item))
    current_release_id = next(iter(unique_versions), release.id)
    versions = [
        {
            "release_id": str(version.id),
            "version": version.version,
            "student_visible_at": version.student_visible_at,
            "current": version.id == current_release_id,
        }
        for version, version_item in unique_versions.values()
    ]
    return {
        **_release_summary(release, item, student, assignment, school_class),
        "total_score": float(score.payload.total_score),
        "max_score": float(score.payload.max_score),
        "score_rate": float(score.payload.total_score / score.payload.max_score),
        "questions": [
            {
                "question_id": str(detail.question_id),
                "question_number": detail.question_number,
                "question_type": detail.question_type,
                "score": float(detail.score),
                "max_score": float(detail.max_score),
                "feedback": detail.final_feedback,
                "error_type": detail.final_error_type,
                "knowledge_points": [
                    {
                        "id": str(point_id),
                        "name": knowledge_names.get(point_id, "未命名知识点"),
                    }
                    for point_id in detail.knowledge_point_ids
                ],
            }
            for detail in score.payload.details
        ],
        "versions": versions,
    }


@router.get("/assignments/{release_id}/report.pdf")
def student_assignment_report(release_id: uuid.UUID, db: Db, actor: Actor) -> Response:
    release, _item, student, _assignment, _school_class = _owned_visible_release(
        db, actor.id, release_id
    )
    font_path = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansSC-VF.ttf"
    content = student_report_pdf(db, release, student.id, font_path)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="grade-v{release.version}.pdf"'},
    )
