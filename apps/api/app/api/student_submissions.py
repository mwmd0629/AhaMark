import hashlib
import io
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from app.api.actor import Actor
from app.api.assignments import MIMES
from app.api.domain import ApiProblem, audit
from app.api.student_portal import _linked_students
from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    Assignment,
    AssignmentParticipantSnapshot,
    AssignmentStatus,
    FileStatus,
    GradingBatch,
    SchoolClass,
    StoredFile,
    Student,
    Submission,
    SubmissionFileMatch,
    SubmissionPage,
    now_utc,
)
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/student", tags=["student-submissions"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
ONLINE_BATCH_NAME = "学生在线提交"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _is_late(due_at: datetime | None) -> bool:
    due = _as_utc(due_at)
    return due is not None and now_utc() > due


def _submission_view(
    assignment: Assignment,
    school_class: SchoolClass,
    student: Student,
    submissions: list[Submission],
) -> dict[str, Any]:
    return {
        "assignment_id": str(assignment.id),
        "assignment_title": assignment.title,
        "description": assignment.description,
        "instructions": assignment.instructions,
        "class_id": str(school_class.id),
        "class_name": school_class.name,
        "student_id": str(student.id),
        "due_at": assignment.due_at,
        "late": _is_late(assignment.due_at),
        "attempts": [
            {
                "id": str(item.id),
                "attempt_number": item.attempt_number,
                "status": item.status,
                "submitted_at": item.submitted_at,
            }
            for item in sorted(submissions, key=lambda value: value.attempt_number, reverse=True)
        ],
    }


def _student_assignment(
    db: Session, actor_id: uuid.UUID, assignment_id: uuid.UUID, *, lock: bool = False
) -> tuple[Assignment, SchoolClass, Student]:
    statement = (
        select(Assignment, SchoolClass, Student)
        .join(
            AssignmentParticipantSnapshot,
            AssignmentParticipantSnapshot.assignment_id == Assignment.id,
        )
        .join(SchoolClass, SchoolClass.id == AssignmentParticipantSnapshot.class_id)
        .join(Student, Student.id == AssignmentParticipantSnapshot.student_id)
        .where(
            Assignment.id == assignment_id,
            Assignment.status == AssignmentStatus.published,
            Student.user_id == actor_id,
            Student.owner_id == Assignment.owner_id,
        )
    )
    if lock:
        statement = statement.with_for_update()
    rows = db.execute(statement).all()
    if len(rows) != 1:
        raise ApiProblem(404, "STUDENT_ASSIGNMENT_NOT_FOUND", "作业不存在或未向当前学生发布")
    return rows[0][0], rows[0][1], rows[0][2]


@router.get("/open-assignments")
def list_student_open_assignments(db: Db, actor: Actor) -> list[dict[str, Any]]:
    _linked_students(db, actor.id)
    rows = db.execute(
        select(Assignment, SchoolClass, Student)
        .join(
            AssignmentParticipantSnapshot,
            AssignmentParticipantSnapshot.assignment_id == Assignment.id,
        )
        .join(SchoolClass, SchoolClass.id == AssignmentParticipantSnapshot.class_id)
        .join(Student, Student.id == AssignmentParticipantSnapshot.student_id)
        .where(
            Assignment.status == AssignmentStatus.published,
            Student.user_id == actor.id,
            Student.owner_id == Assignment.owner_id,
        )
        .order_by(Assignment.due_at.asc().nulls_last(), Assignment.published_at.desc())
    ).all()
    result = []
    for assignment, school_class, student in rows:
        submissions = list(
            db.scalars(
                select(Submission).where(
                    Submission.assignment_id == assignment.id,
                    Submission.class_id == school_class.id,
                    Submission.student_id == student.id,
                    Submission.source == "student_upload",
                    Submission.status != "voided",
                )
            )
        )
        result.append(_submission_view(assignment, school_class, student, submissions))
    return result


@router.post("/open-assignments/{assignment_id}/submissions", status_code=201)
async def create_student_submission(
    assignment_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    files: Annotated[list[UploadFile], File()],
) -> dict[str, Any]:
    assignment, school_class, student = _student_assignment(
        db, actor.id, assignment_id, lock=True
    )
    settings = get_settings()
    if not files or len(files) > settings.submission_max_files:
        raise ApiProblem(413, "TOO_MANY_FILES", "请选择有效数量的作业文件")
    inspected: list[tuple[bytes, str, str, int, str]] = []
    total = 0
    seen: set[str] = set()
    for upload in files:
        content = await upload.read(settings.assignment_max_file_bytes + 1)
        total += len(content)
        if (
            not content
            or len(content) > settings.assignment_max_file_bytes
            or total > settings.submission_batch_max_bytes
        ):
            raise ApiProblem(413, "SUBMISSION_UPLOAD_TOO_LARGE", "学生作业超过上传限制")
        try:
            name = safe_filename(upload.filename)
            inspection = inspect_upload(
                name,
                content,
                upload.content_type,
                max_pdf_pages=settings.recognition_max_pdf_pages,
                max_image_pixels=settings.recognition_max_image_pixels,
                allow_docx=False,
            )
        except UnsafeFile as exc:
            status = 415 if exc.code in {"FILE_TYPE_INVALID", "FILE_CONTENT_INVALID"} else 422
            raise ApiProblem(status, exc.code, exc.message) from exc
        checksum = hashlib.sha256(content).hexdigest()
        if checksum in seen:
            raise ApiProblem(409, "DUPLICATE_SUBMISSION_FILE", "本次提交包含重复文件")
        seen.add(checksum)
        inspected.append((content, name, inspection.kind, inspection.page_count, checksum))
    batch = db.scalar(
        select(GradingBatch).where(
            GradingBatch.assignment_id == assignment.id,
            GradingBatch.class_id == school_class.id,
            GradingBatch.owner_id == assignment.owner_id,
            GradingBatch.name == ONLINE_BATCH_NAME,
            GradingBatch.status != "archived",
        )
    )
    if batch is None:
        batch = GradingBatch(
            owner_id=assignment.owner_id,
            assignment_id=assignment.id,
            class_id=school_class.id,
            name=ONLINE_BATCH_NAME,
            description="由学生端在线提交自动创建",
            status="draft",
        )
        db.add(batch)
        db.flush()
    attempt_number = int(
        db.scalar(
            select(func.max(Submission.attempt_number)).where(
                Submission.grading_batch_id == batch.id,
                Submission.student_id == student.id,
            )
        )
        or 0
    ) + 1
    submission = Submission(
        owner_id=assignment.owner_id,
        grading_batch_id=batch.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        student_id=student.id,
        attempt_number=attempt_number,
        status="matched",
        source="student_upload",
        submitted_at=now_utc(),
    )
    db.add(submission)
    db.flush()
    written: list[str] = []
    page_number = 0
    try:
        for content, name, kind, page_count, checksum in inspected:
            key = (
                f"submissions/{assignment.owner_id}/{batch.id}/student/"
                f"{student.id}/{uuid.uuid4().hex}.{kind}"
            )
            storage.put(key, io.BytesIO(content), len(content), MIMES[kind])
            written.append(key)
            stored = StoredFile(
                owner_id=assignment.owner_id,
                storage_key=key,
                original_name=name,
                content_type=MIMES[kind],
                size=len(content),
                checksum=checksum,
                status=FileStatus.ready,
            )
            db.add(stored)
            db.flush()
            db.add(
                SubmissionFileMatch(
                    grading_batch_id=batch.id,
                    stored_file_id=stored.id,
                    suggested_student_id=student.id,
                    confirmed_student_id=student.id,
                    match_method="authenticated_student",
                    confidence=1,
                    status="confirmed",
                    reason="学生登录账号与档案直接关联",
                    confirmed_by=actor.id,
                    confirmed_at=now_utc(),
                )
            )
            for source_page in range(1, page_count + 1):
                page_number += 1
                db.add(
                    SubmissionPage(
                        submission_id=submission.id,
                        stored_file_id=stored.id,
                        page_number=page_number,
                        source_page_number=source_page,
                        status="ready",
                    )
                )
        batch.submission_count += 1
        audit(
            db,
            actor.id,
            "student.submission.create",
            "submission",
            submission.id,
            {
                "assignment_id": str(assignment.id),
                "attempt_number": attempt_number,
                "file_count": len(inspected),
                "late": _is_late(assignment.due_at),
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        for key in written:
            try:
                storage.delete(key)
            except Exception:
                pass
        if isinstance(exc, ApiProblem):
            raise
        raise ApiProblem(503, "STORAGE_UNAVAILABLE", "作业保存失败，未保留半成品") from exc
    return {
        "id": str(submission.id),
        "attempt_number": attempt_number,
        "status": submission.status,
        "submitted_at": submission.submitted_at,
        "file_count": len(inspected),
    }
