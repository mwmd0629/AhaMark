import hashlib
import io
import re
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.core.config import get_settings
from app.db.session import get_db
from app.grading.providers import grade_objective, provider_from_settings
from app.models import (
    Assignment,
    AssignmentClass,
    AssignmentStatus,
    ClassStudent,
    FileStatus,
    GradingBatch,
    GradingCriterionResult,
    GradingEvidence,
    GradingJob,
    GradingResult,
    MembershipStatus,
    Question,
    QuestionKnowledgePoint,
    QuestionRubric,
    QuestionStatus,
    RubricItem,
    RubricVersion,
    SchoolClass,
    ScoreRevision,
    StoredFile,
    Student,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionFileMatch,
    SubmissionPage,
    SubmissionRecognitionJob,
    SubmissionScoreSnapshot,
    TeacherReview,
    now_utc,
)
from app.recognition.pipeline import provider_from_settings as recognition_provider_from_settings
from app.recognition.submission import mark_submission_stale
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["grading"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


class BatchInput(BaseModel):
    class_id: uuid.UUID
    name: str | None = Field(None, max_length=160)
    description: str | None = Field(None, max_length=2000)


class MatchInput(BaseModel):
    student_id: uuid.UUID


class AnswerInput(BaseModel):
    question_id: uuid.UUID
    recognized_text: str | None = None
    recognized_latex: str | None = None
    recognition_confidence: Decimal | None = Field(None, ge=0, le=1)
    recognition_provider: str = "manual"
    recognition_provider_version: str = "none"
    is_blank: bool = False


class AnswerPatch(BaseModel):
    corrected_text: str | None = None
    corrected_latex: str | None = None


class ReviewInput(BaseModel):
    decision: Literal["accepted", "modified", "rejected", "manual_scored", "needs_more_information"]
    final_score: Decimal | None = Field(None, ge=0)
    final_feedback: str | None = None
    final_error_type: str | None = None
    review_notes: str | None = None
    reason: str | None = None


class RecognitionStartInput(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=100)


class PageOrderInput(BaseModel):
    page_ids: list[uuid.UUID] = Field(min_length=1)


class MovePagesInput(BaseModel):
    target_submission_id: uuid.UUID
    page_ids: list[uuid.UUID] = Field(min_length=1)


class SplitSubmissionInput(BaseModel):
    page_ids: list[uuid.UUID] = Field(min_length=1)


class MergeSubmissionInput(BaseModel):
    source_submission_id: uuid.UUID


class BulkAcceptInput(BaseModel):
    answer_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class RegradeInput(BaseModel):
    question_id: uuid.UUID | None = None
    submission_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    only_unreviewed: bool = False
    only_stale: bool = False


def owned_assignment(db: Session, owner: uuid.UUID, assignment_id: uuid.UUID) -> Assignment:
    item = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.owner_id == owner)
    )
    if item is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    return item


def owned_batch(db: Session, owner: uuid.UUID, batch_id: uuid.UUID) -> GradingBatch:
    item = db.scalar(
        select(GradingBatch).where(GradingBatch.id == batch_id, GradingBatch.owner_id == owner)
    )
    if item is None:
        raise ApiProblem(404, "GRADING_BATCH_NOT_FOUND", "批改批次不存在")
    return item


def owned_submission(db: Session, owner: uuid.UUID, submission_id: uuid.UUID) -> Submission:
    item = db.scalar(
        select(Submission).where(Submission.id == submission_id, Submission.owner_id == owner)
    )
    if item is None:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "提交不存在")
    return item


def batch_json(db: Session, x: GradingBatch) -> dict[str, Any]:
    matches = db.scalars(
        select(SubmissionFileMatch).where(SubmissionFileMatch.grading_batch_id == x.id)
    ).all()
    members = db.scalars(
        select(Student)
        .join(ClassStudent, ClassStudent.student_id == Student.id)
        .where(
            ClassStudent.class_id == x.class_id,
            ClassStudent.status == MembershipStatus.active,
            Student.owner_id == x.owner_id,
        )
        .order_by(Student.student_number, Student.id)
    ).all()
    return {
        "id": str(x.id),
        "assignment_id": str(x.assignment_id),
        "class_id": str(x.class_id),
        "name": x.name,
        "description": x.description,
        "status": x.status,
        "submission_count": x.submission_count,
        "recognized_count": x.recognized_count,
        "graded_count": x.graded_count,
        "reviewed_count": x.reviewed_count,
        "failed_count": x.failed_count,
        "matching": {
            "total": len(matches),
            "confirmed": sum(m.status == "confirmed" for m in matches),
            "ambiguous": sum(m.match_method == "ambiguous" for m in matches),
            "unmatched": sum(m.match_method == "unmatched" for m in matches),
            "items": [
                {
                    "id": str(match.id),
                    "filename": (
                        stored.original_name
                        if (stored := db.get(StoredFile, match.stored_file_id))
                        else "unknown"
                    ),
                    "status": match.status,
                    "method": match.match_method,
                    "reason": match.reason,
                    "suggested_student_id": (
                        str(match.suggested_student_id) if match.suggested_student_id else None
                    ),
                    "confirmed_student_id": (
                        str(match.confirmed_student_id) if match.confirmed_student_id else None
                    ),
                }
                for match in matches
            ],
            "student_options": [
                {
                    "id": str(student.id),
                    "student_number": student.student_number,
                    "name": student.name,
                }
                for student in members
            ],
        },
        "actions": ["upload", "review_matches", "grade", "archive"]
        if x.status != "archived"
        else [],
    }


@router.post("/assignments/{assignment_id}/grading-batches", status_code=201)
def create_batch(
    assignment_id: uuid.UUID, data: BatchInput, db: Db, actor: Actor
) -> dict[str, Any]:
    assignment = owned_assignment(db, actor.id, assignment_id)
    school_class = db.scalar(
        select(SchoolClass).where(SchoolClass.id == data.class_id, SchoolClass.owner_id == actor.id)
    )
    linked = db.scalar(
        select(AssignmentClass.id).where(
            AssignmentClass.assignment_id == assignment.id,
            AssignmentClass.class_id == data.class_id,
        )
    )
    if school_class is None or linked is None:
        raise ApiProblem(409, "ASSIGNMENT_CLASS_MISMATCH", "班级未关联到该作业")
    if assignment.status not in {
        AssignmentStatus.published,
        AssignmentStatus.grading,
        AssignmentStatus.completed,
    }:
        raise ApiProblem(409, "ASSIGNMENT_NOT_GRADABLE", "作业尚未发布")
    if not assignment.active_paper_version_id or not assignment.active_rubric_version_id:
        raise ApiProblem(409, "GRADING_VERSIONS_REQUIRED", "缺少有效试卷或评分标准版本")
    questions = db.scalars(
        select(Question).where(
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    ).all()
    if not questions or any(q.max_score is None for q in questions):
        raise ApiProblem(409, "QUESTION_SCORE_REQUIRED", "题目分值不完整")
    rubric_ids = set(
        db.scalars(
            select(QuestionRubric.question_id).where(
                QuestionRubric.rubric_version_id == assignment.active_rubric_version_id
            )
        ).all()
    )
    if any(q.id not in rubric_ids for q in questions):
        raise ApiProblem(409, "RUBRIC_INCOMPLETE", "评分标准不完整")
    item = GradingBatch(
        owner_id=actor.id,
        assignment_id=assignment.id,
        class_id=data.class_id,
        name=data.name,
        description=data.description,
        status="collecting",
    )
    db.add(item)
    db.flush()
    audit(db, actor.id, "grading_batch.create", "grading_batch", item.id)
    db.commit()
    return batch_json(db, item)


@router.get("/assignments/{assignment_id}/grading-batches")
def list_batches(
    assignment_id: uuid.UUID,
    db: Db,
    actor: Actor,
    status: str | None = None,
    class_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    owned_assignment(db, actor.id, assignment_id)
    filters: list[Any] = [
        GradingBatch.owner_id == actor.id,
        GradingBatch.assignment_id == assignment_id,
    ]
    if status:
        filters.append(GradingBatch.status == status)
    if class_id:
        filters.append(GradingBatch.class_id == class_id)
    total = db.scalar(select(func.count()).select_from(GradingBatch).where(*filters)) or 0
    items = db.scalars(
        select(GradingBatch)
        .where(*filters)
        .order_by(GradingBatch.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [batch_json(db, x) for x in items],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/grading-batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return batch_json(db, owned_batch(db, actor.id, batch_id))


@router.post("/grading-batches/{batch_id}/archive")
def archive_batch(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = owned_batch(db, actor.id, batch_id)
    item.status = "archived"
    audit(db, actor.id, "grading_batch.archive", "grading_batch", item.id)
    db.commit()
    return batch_json(db, item)


def match_student(
    db: Session, batch: GradingBatch, filename: str
) -> tuple[Student | None, str, Decimal, str]:
    members = db.scalars(
        select(Student)
        .join(ClassStudent, ClassStudent.student_id == Student.id)
        .where(
            ClassStudent.class_id == batch.class_id,
            ClassStudent.status == MembershipStatus.active,
            Student.owner_id == batch.owner_id,
        )
    ).all()
    numbers = [
        s
        for s in members
        if re.search(rf"(?<!\w){re.escape(s.student_number)}(?!\w)", filename, re.I)
    ]
    names = [s for s in members if s.name in filename]
    candidates = {s.id: s for s in numbers + names}
    if len(candidates) > 1:
        return None, "ambiguous", Decimal("0"), "文件名包含多个学生标识"
    if len(numbers) == 1:
        return numbers[0], "student_number", Decimal("1"), "学号精确匹配"
    if len(names) == 1 and sum(s.name == names[0].name for s in members) == 1:
        return names[0], "exact_name", Decimal("0.98"), "班级内唯一姓名精确匹配"
    if names:
        return None, "ambiguous", Decimal("0"), "姓名在班级内不唯一"
    return None, "unmatched", Decimal("0"), "未找到可靠学生标识"


@router.post("/grading-batches/{batch_id}/files", status_code=201)
async def upload_submissions(
    batch_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    files: Annotated[list[UploadFile], File()],
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    settings = get_settings()
    if batch.status == "archived":
        raise ApiProblem(409, "BATCH_ARCHIVED", "批次已归档")
    if len(files) > settings.submission_max_files:
        raise ApiProblem(413, "TOO_MANY_FILES", "文件数量超过限制")
    created: list[dict[str, Any]] = []
    total = 0
    inspected: list[tuple[UploadFile, bytes, str, str, int]] = []
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
            )
        except UnsafeFile as exc:
            status = 415 if exc.code in {"FILE_TYPE_INVALID", "FILE_CONTENT_INVALID"} else 422
            raise ApiProblem(status, exc.code, exc.message) from exc
        suffix = f".{inspection.kind}"
        mime = upload.content_type or "application/octet-stream"
        checksum = hashlib.sha256(content).hexdigest()
        duplicate = db.scalar(
            select(StoredFile)
            .join(SubmissionFileMatch, SubmissionFileMatch.stored_file_id == StoredFile.id)
            .where(
                SubmissionFileMatch.grading_batch_id == batch.id, StoredFile.checksum == checksum
            )
        )
        if duplicate:
            raise ApiProblem(409, "DUPLICATE_SUBMISSION_FILE", "批次中已存在相同文件")
        inspected.append((upload, content, suffix, mime, inspection.page_count))
    for upload, content, suffix, mime, page_count in inspected:
        checksum = hashlib.sha256(content).hexdigest()
        key = f"submissions/{actor.id}/{batch.id}/{uuid.uuid4().hex}{suffix}"
        try:
            storage.put(key, io.BytesIO(content), len(content), mime)
        except Exception as exc:
            db.rollback()
            raise ApiProblem(503, "STORAGE_UNAVAILABLE", "对象存储不可用，文件未保存") from exc
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=key,
            original_name=safe_filename(upload.filename),
            content_type=mime,
            size=len(content),
            checksum=checksum,
            status=FileStatus.ready,
        )
        db.add(stored)
        db.flush()
        student, method, confidence, reason = match_student(db, batch, stored.original_name)
        match = SubmissionFileMatch(
            grading_batch_id=batch.id,
            stored_file_id=stored.id,
            suggested_student_id=student.id if student else None,
            confirmed_student_id=student.id
            if student and confidence >= Decimal(str(settings.submission_match_threshold))
            else None,
            match_method=method,
            confidence=confidence,
            status="confirmed"
            if student and confidence >= Decimal(str(settings.submission_match_threshold))
            else "pending",
            reason=reason,
            confirmed_by=actor.id
            if student and confidence >= Decimal(str(settings.submission_match_threshold))
            else None,
            confirmed_at=now_utc()
            if student and confidence >= Decimal(str(settings.submission_match_threshold))
            else None,
        )
        db.add(match)
        db.flush()
        submission = None
        if match.confirmed_student_id:
            submission = db.scalar(
                select(Submission).where(
                    Submission.grading_batch_id == batch.id,
                    Submission.student_id == match.confirmed_student_id,
                    Submission.attempt_number == 1,
                )
            )
            if submission is None:
                submission = Submission(
                    owner_id=actor.id,
                    grading_batch_id=batch.id,
                    assignment_id=batch.assignment_id,
                    class_id=batch.class_id,
                    student_id=match.confirmed_student_id,
                    status="matched",
                )
                db.add(submission)
                db.flush()
                batch.submission_count += 1
            next_page = (
                db.scalar(
                    select(func.max(SubmissionPage.page_number)).where(
                        SubmissionPage.submission_id == submission.id
                    )
                )
                or 0
            ) + 1
            for source_page in range(1, page_count + 1):
                db.add(
                    SubmissionPage(
                        submission_id=submission.id,
                        stored_file_id=stored.id,
                        page_number=next_page + source_page - 1,
                        source_page_number=source_page,
                        status="ready",
                    )
                )
        created.append(
            {
                "match_id": str(match.id),
                "file_id": str(stored.id),
                "filename": stored.original_name,
                "method": method,
                "confidence": str(confidence),
                "status": match.status,
                "suggested_student_id": str(student.id) if student else None,
                "submission_id": str(submission.id) if submission else None,
            }
        )
    db.commit()
    return {"items": created, "count": len(created)}


@router.post("/grading-batches/{batch_id}/matches/{match_id}/confirm")
def confirm_match(
    batch_id: uuid.UUID, match_id: uuid.UUID, data: MatchInput, db: Db, actor: Actor
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    match = db.scalar(
        select(SubmissionFileMatch).where(
            SubmissionFileMatch.id == match_id, SubmissionFileMatch.grading_batch_id == batch.id
        )
    )
    student = db.scalar(
        select(Student)
        .join(ClassStudent, ClassStudent.student_id == Student.id)
        .where(
            Student.id == data.student_id,
            Student.owner_id == actor.id,
            ClassStudent.class_id == batch.class_id,
            ClassStudent.status == MembershipStatus.active,
        )
    )
    if match is None or student is None:
        raise ApiProblem(404, "MATCH_OR_STUDENT_NOT_FOUND", "匹配记录或班级学生不存在")
    if match.status == "confirmed":
        if match.confirmed_student_id != student.id:
            raise ApiProblem(409, "MATCH_ALREADY_CONFIRMED", "已确认匹配不能改到其他学生")
        existing_submission = db.scalar(
            select(Submission)
            .join(SubmissionPage, SubmissionPage.submission_id == Submission.id)
            .where(
                Submission.grading_batch_id == batch.id,
                Submission.student_id == student.id,
                SubmissionPage.stored_file_id == match.stored_file_id,
            )
        )
        if existing_submission is None:
            raise ApiProblem(409, "MATCH_CONFIRMATION_INCONSISTENT", "已确认匹配缺少提交页面")
        return {"submission_id": str(existing_submission.id), "status": "confirmed"}
    submission = db.scalar(
        select(Submission).where(
            Submission.grading_batch_id == batch.id,
            Submission.student_id == student.id,
            Submission.attempt_number == 1,
        )
    )
    if submission is None:
        submission = Submission(
            owner_id=actor.id,
            grading_batch_id=batch.id,
            assignment_id=batch.assignment_id,
            class_id=batch.class_id,
            student_id=student.id,
            status="matched",
        )
        db.add(submission)
        db.flush()
        batch.submission_count += 1
    if (
        db.scalar(
            select(SubmissionPage.id).where(
                SubmissionPage.submission_id == submission.id,
                SubmissionPage.stored_file_id == match.stored_file_id,
            )
        )
        is None
    ):
        db.add(
            SubmissionPage(
                submission_id=submission.id,
                stored_file_id=match.stored_file_id,
                page_number=(
                    db.scalar(
                        select(func.max(SubmissionPage.page_number)).where(
                            SubmissionPage.submission_id == submission.id
                        )
                    )
                    or 0
                )
                + 1,
                source_page_number=1,
            )
        )
    (
        match.confirmed_student_id,
        match.match_method,
        match.status,
        match.confirmed_by,
        match.confirmed_at,
    ) = student.id, "manual", "confirmed", actor.id, now_utc()
    audit(db, actor.id, "submission_match.confirm", "submission_file_match", match.id)
    db.commit()
    return {"submission_id": str(submission.id), "status": "confirmed"}


@router.get("/grading-batches/{batch_id}/submissions")
def list_submissions(batch_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    batch = owned_batch(db, actor.id, batch_id)
    rows = db.scalars(
        select(Submission)
        .where(Submission.grading_batch_id == batch.id)
        .order_by(Submission.created_at)
    ).all()
    return [
        {
            "id": str(x.id),
            "student_id": str(x.student_id) if x.student_id else None,
            "status": x.status,
            "attempt_number": x.attempt_number,
            "page_count": db.scalar(
                select(func.count())
                .select_from(SubmissionPage)
                .where(SubmissionPage.submission_id == x.id)
            )
            or 0,
        }
        for x in rows
    ]


def _editable_submission(db: Session, owner: uuid.UUID, submission_id: uuid.UUID) -> Submission:
    submission = owned_submission(db, owner, submission_id)
    if submission.status == "finalized":
        raise ApiProblem(409, "SUBMISSION_FINALIZED", "已完成提交不能修改页面结构")
    return submission


def _renumber_pages(db: Session, submission_id: uuid.UUID) -> None:
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission_id)
        .order_by(SubmissionPage.page_number, SubmissionPage.id)
    ).all()
    for index, page in enumerate(pages, 1):
        page.page_number = -index
    db.flush()
    for index, page in enumerate(pages, 1):
        page.page_number = index


def submission_job_json(db: Session, job: SubmissionRecognitionJob) -> dict[str, Any]:
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == job.submission_id)
        .order_by(SubmissionPage.page_number)
    ).all()
    return {
        "id": str(job.id),
        "submission_id": str(job.submission_id),
        "status": job.status,
        "provider": job.provider,
        "provider_version": job.provider_version,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "pages": [
            {
                "id": str(page.id),
                "page_number": page.page_number,
                "status": page.status,
                "rendered_storage_key": page.rendered_storage_key,
                "processed_storage_key": page.processed_storage_key,
                "thumbnail_storage_key": page.thumbnail_storage_key,
            }
            for page in pages
        ],
    }


@router.post("/submissions/{submission_id}/recognition-jobs", status_code=201)
def start_submission_recognition(
    submission_id: uuid.UUID,
    data: RecognitionStartInput,
    db: Db,
    actor: Actor,
    storage: Storage,
    run_now: bool = False,
) -> dict[str, Any]:
    submission = _editable_submission(db, actor.id, submission_id)
    existing = db.scalar(
        select(SubmissionRecognitionJob).where(
            SubmissionRecognitionJob.owner_id == actor.id,
            SubmissionRecognitionJob.idempotency_key == data.idempotency_key,
        )
    )
    if existing:
        return submission_job_json(db, existing)
    provider = recognition_provider_from_settings(get_settings())
    job = SubmissionRecognitionJob(
        owner_id=actor.id,
        submission_id=submission.id,
        provider=provider.name,
        provider_version=provider.version,
        idempotency_key=data.idempotency_key,
        status="queued",
    )
    db.add(job)
    db.flush()
    audit(db, actor.id, "submission_recognition.create", "submission_recognition_job", job.id)
    db.commit()
    if run_now:
        from app.recognition.submission import run_submission_recognition_job

        assert storage is not None
        run_submission_recognition_job(db, storage, job.id)
    else:
        try:
            from workers.celery_app import celery_app

            celery_app.send_task("ahamark.submission_recognition.run", args=[str(job.id)])
        except Exception as exc:
            job.status, job.error_code, job.error_message = (
                "failed",
                "WORKER_UNAVAILABLE",
                type(exc).__name__,
            )
            db.commit()
    return submission_job_json(db, job)


@router.get("/submissions/{submission_id}/recognition-jobs/{job_id}")
def get_submission_recognition(
    submission_id: uuid.UUID, job_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    owned_submission(db, actor.id, submission_id)
    job = db.scalar(
        select(SubmissionRecognitionJob).where(
            SubmissionRecognitionJob.id == job_id,
            SubmissionRecognitionJob.submission_id == submission_id,
            SubmissionRecognitionJob.owner_id == actor.id,
        )
    )
    if job is None:
        raise ApiProblem(404, "SUBMISSION_RECOGNITION_NOT_FOUND", "识别任务不存在")
    return submission_job_json(db, job)


@router.post("/submissions/{submission_id}/recognition-jobs/{job_id}/pages/{page_id}/retry")
def retry_submission_page(
    submission_id: uuid.UUID,
    job_id: uuid.UUID,
    page_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    run_now: bool = False,
) -> dict[str, Any]:
    _editable_submission(db, actor.id, submission_id)
    job = db.scalar(
        select(SubmissionRecognitionJob).where(
            SubmissionRecognitionJob.id == job_id,
            SubmissionRecognitionJob.submission_id == submission_id,
            SubmissionRecognitionJob.owner_id == actor.id,
        )
    )
    page = db.scalar(
        select(SubmissionPage).where(
            SubmissionPage.id == page_id, SubmissionPage.submission_id == submission_id
        )
    )
    if job is None or page is None:
        raise ApiProblem(404, "SUBMISSION_PAGE_NOT_FOUND", "识别页面不存在")
    page.status, job.status = "ready", "queued"
    db.commit()
    if run_now:
        from app.recognition.submission import run_submission_recognition_job

        assert storage is not None
        run_submission_recognition_job(db, storage, job.id, page.id)
    else:
        from workers.celery_app import celery_app

        celery_app.send_task("ahamark.submission_recognition.run", args=[str(job.id), str(page.id)])
    return submission_job_json(db, job)


@router.put("/submissions/{submission_id}/pages/order")
def reorder_submission_pages(
    submission_id: uuid.UUID, data: PageOrderInput, db: Db, actor: Actor
) -> dict[str, Any]:
    submission = _editable_submission(db, actor.id, submission_id)
    pages = db.scalars(
        select(SubmissionPage).where(SubmissionPage.submission_id == submission.id)
    ).all()
    if len(data.page_ids) != len(pages) or set(data.page_ids) != {page.id for page in pages}:
        raise ApiProblem(422, "PAGE_ORDER_INCOMPLETE", "排序必须包含且仅包含全部页面")
    by_id = {page.id: page for page in pages}
    for index, page in enumerate(pages, 1):
        page.page_number = -index
    db.flush()
    for index, page_id in enumerate(data.page_ids, 1):
        by_id[page_id].page_number = index
    mark_submission_stale(db, submission.id)
    audit(
        db,
        actor.id,
        "submission.pages.reorder",
        "submission",
        submission.id,
        {"page_ids": [str(x) for x in data.page_ids]},
    )
    db.commit()
    return {"submission_id": str(submission.id), "page_ids": [str(x) for x in data.page_ids]}


@router.post("/submissions/{submission_id}/pages/move")
def move_submission_pages(
    submission_id: uuid.UUID, data: MovePagesInput, db: Db, actor: Actor
) -> dict[str, Any]:
    source = _editable_submission(db, actor.id, submission_id)
    target = _editable_submission(db, actor.id, data.target_submission_id)
    if source.grading_batch_id != target.grading_batch_id or source.id == target.id:
        raise ApiProblem(409, "SUBMISSION_MOVE_INVALID", "页面只能在同一批次的不同提交间移动")
    pages = db.scalars(
        select(SubmissionPage).where(
            SubmissionPage.submission_id == source.id, SubmissionPage.id.in_(data.page_ids)
        )
    ).all()
    source_count = (
        db.scalar(
            select(func.count())
            .select_from(SubmissionPage)
            .where(SubmissionPage.submission_id == source.id)
        )
        or 0
    )
    if len(pages) != len(set(data.page_ids)) or len(pages) >= source_count:
        raise ApiProblem(422, "PAGE_MOVE_INVALID", "移动页不存在或不能移走全部页面")
    next_page = (
        db.scalar(
            select(func.max(SubmissionPage.page_number)).where(
                SubmissionPage.submission_id == target.id
            )
        )
        or 0
    ) + 1
    for offset, page in enumerate(pages):
        page.submission_id, page.page_number = target.id, next_page + offset
    db.flush()
    _renumber_pages(db, source.id)
    mark_submission_stale(db, source.id)
    mark_submission_stale(db, target.id)
    audit(
        db,
        actor.id,
        "submission.pages.move",
        "submission",
        source.id,
        {"target_submission_id": str(target.id), "page_ids": [str(x.id) for x in pages]},
    )
    db.commit()
    return {"source_submission_id": str(source.id), "target_submission_id": str(target.id)}


@router.post("/submissions/{submission_id}/split", status_code=201)
def split_submission(
    submission_id: uuid.UUID, data: SplitSubmissionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    source = _editable_submission(db, actor.id, submission_id)
    pages = db.scalars(
        select(SubmissionPage).where(
            SubmissionPage.submission_id == source.id, SubmissionPage.id.in_(data.page_ids)
        )
    ).all()
    source_count = (
        db.scalar(
            select(func.count())
            .select_from(SubmissionPage)
            .where(SubmissionPage.submission_id == source.id)
        )
        or 0
    )
    if len(pages) != len(set(data.page_ids)) or len(pages) >= source_count:
        raise ApiProblem(422, "SUBMISSION_SPLIT_INVALID", "拆分页不存在或不能拆出全部页面")
    attempt = (
        db.scalar(
            select(func.max(Submission.attempt_number)).where(
                Submission.grading_batch_id == source.grading_batch_id,
                Submission.student_id == source.student_id,
            )
        )
        or 0
    ) + 1
    target = Submission(
        owner_id=source.owner_id,
        grading_batch_id=source.grading_batch_id,
        assignment_id=source.assignment_id,
        class_id=source.class_id,
        student_id=source.student_id,
        attempt_number=attempt,
        status="matched",
        source="split",
    )
    db.add(target)
    db.flush()
    for index, page in enumerate(pages, 1):
        page.submission_id, page.page_number = target.id, index
    db.flush()
    _renumber_pages(db, source.id)
    mark_submission_stale(db, source.id)
    mark_submission_stale(db, target.id)
    audit(
        db,
        actor.id,
        "submission.split",
        "submission",
        source.id,
        {"new_submission_id": str(target.id)},
    )
    db.commit()
    return {"source_submission_id": str(source.id), "new_submission_id": str(target.id)}


@router.post("/submissions/{submission_id}/merge")
def merge_submission(
    submission_id: uuid.UUID, data: MergeSubmissionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    target = _editable_submission(db, actor.id, submission_id)
    source = _editable_submission(db, actor.id, data.source_submission_id)
    pages = db.scalars(
        select(SubmissionPage).where(SubmissionPage.submission_id == source.id)
    ).all()
    if (
        source.grading_batch_id != target.grading_batch_id
        or source.assignment_id != target.assignment_id
        or source.class_id != target.class_id
        or source.id == target.id
    ):
        raise ApiProblem(409, "SUBMISSION_MERGE_INVALID", "只能合并同一批次的不同提交")
    next_page = (
        db.scalar(
            select(func.max(SubmissionPage.page_number)).where(
                SubmissionPage.submission_id == target.id
            )
        )
        or 0
    ) + 1
    for offset, page in enumerate(pages):
        page.submission_id, page.page_number = target.id, next_page + offset
    source.status = "merged"
    mark_submission_stale(db, target.id)
    mark_submission_stale(db, source.id)
    audit(
        db,
        actor.id,
        "submission.merge",
        "submission",
        target.id,
        {"source_submission_id": str(source.id)},
    )
    db.commit()
    return {
        "target_submission_id": str(target.id),
        "source_submission_id": str(source.id),
        "page_count": len(pages),
    }


@router.post("/submissions/{submission_id}/answers", status_code=201)
def create_answer(
    submission_id: uuid.UUID, data: AnswerInput, db: Db, actor: Actor
) -> dict[str, Any]:
    submission = owned_submission(db, actor.id, submission_id)
    assignment = owned_assignment(db, actor.id, submission.assignment_id)
    question = db.scalar(
        select(Question).where(
            Question.id == data.question_id,
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    )
    if question is None:
        raise ApiProblem(409, "QUESTION_VERSION_MISMATCH", "题目不属于当前试卷版本")
    if db.scalar(
        select(StudentAnswer.id).where(
            StudentAnswer.submission_id == submission.id, StudentAnswer.question_id == question.id
        )
    ):
        raise ApiProblem(409, "ANSWER_EXISTS", "该题答案已存在")
    confidence = data.recognition_confidence
    formula = bool(data.recognized_latex)
    status = (
        "blank"
        if data.is_blank
        else "formula_unavailable"
        if formula
        else "low_confidence"
        if confidence is not None
        and confidence < Decimal(str(get_settings().recognition_low_confidence))
        else "ready_for_grading"
    )
    answer = StudentAnswer(
        submission_id=submission.id,
        question_id=question.id,
        question_version_reference=str(assignment.active_paper_version_id),
        status=status,
        recognized_text=data.recognized_text,
        recognized_latex=data.recognized_latex,
        recognition_confidence=confidence,
        recognition_provider=data.recognition_provider,
        recognition_provider_version=data.recognition_provider_version,
        is_blank=data.is_blank,
        requires_review=status != "ready_for_grading",
    )
    db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer_json(answer)


def answer_json(x: StudentAnswer) -> dict[str, Any]:
    return {
        "id": str(x.id),
        "submission_id": str(x.submission_id),
        "question_id": str(x.question_id),
        "status": x.status,
        "recognized_text": x.recognized_text,
        "corrected_text": x.corrected_text,
        "effective_text": x.corrected_text if x.corrected_text is not None else x.recognized_text,
        "confidence": str(x.recognition_confidence)
        if x.recognition_confidence is not None
        else None,
        "requires_review": x.requires_review,
    }


@router.patch("/student-answers/{answer_id}")
def patch_answer(answer_id: uuid.UUID, data: AnswerPatch, db: Db, actor: Actor) -> dict[str, Any]:
    answer = db.scalar(
        select(StudentAnswer)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(StudentAnswer.id == answer_id, Submission.owner_id == actor.id)
    )
    if answer is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    answer.corrected_text, answer.corrected_latex, answer.status, answer.requires_review = (
        data.corrected_text,
        data.corrected_latex,
        "manually_entered",
        True,
    )
    for result in db.scalars(
        select(GradingResult).where(
            GradingResult.student_answer_id == answer.id,
            GradingResult.status.in_(["suggested", "accepted", "modified"]),
        )
    ).all():
        result.status = "superseded"
    db.commit()
    return answer_json(answer)


@router.post("/student-answers/{answer_id}/grade")
def grade_answer(answer_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    answer = db.scalar(
        select(StudentAnswer)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(StudentAnswer.id == answer_id, Submission.owner_id == actor.id)
    )
    if answer is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    submission = owned_submission(db, actor.id, answer.submission_id)
    assignment = owned_assignment(db, actor.id, submission.assignment_id)
    question = db.get(Question, answer.question_id)
    rubric = db.scalar(
        select(QuestionRubric).where(
            QuestionRubric.rubric_version_id == assignment.active_rubric_version_id,
            QuestionRubric.question_id == answer.question_id,
        )
    )
    if question is None or question.max_score is None or rubric is None:
        raise ApiProblem(409, "RUBRIC_INCOMPLETE", "题目或评分标准不完整")
    job = GradingJob(
        owner_id=actor.id,
        grading_batch_id=submission.grading_batch_id,
        submission_id=submission.id,
        question_id=question.id,
        rubric_version_id=assignment.active_rubric_version_id,
        status="running",
        provider="pending",
        provider_version="pending",
        prompt_version=get_settings().grading_prompt_version,
        config_version=get_settings().grading_config_version,
        idempotency_key=uuid.uuid4().hex,
        started_at=now_utc(),
    )
    db.add(job)
    db.flush()
    text = (
        answer.corrected_text
        if answer.corrected_text is not None
        else (answer.recognized_text or "")
    )
    if question.question_type in {"single_choice", "multiple_choice", "true_false", "fill_blank"}:
        suggestion = grade_objective(
            text,
            [rubric.standard_answer or "", *rubric.alternative_answers],
            Decimal(question.max_score),
        )
        method, provider, version = "objective_rule", "objective-rule", "v1"
    else:
        chosen = provider_from_settings(get_settings())
        suggestion = chosen.grade(text, Decimal(question.max_score))
        method, provider, version = (
            ("ai_provider" if suggestion.score is not None else "unavailable"),
            chosen.name,
            chosen.version,
        )
    job.status, job.provider, job.provider_version, job.completed_at = (
        "completed",
        provider,
        version,
        now_utc(),
    )
    requires = (
        answer.requires_review
        or suggestion.score is None
        or suggestion.confidence is None
        or suggestion.confidence < Decimal(str(get_settings().grading_auto_accept_confidence))
    )
    for previous in db.scalars(
        select(GradingResult).where(
            GradingResult.student_answer_id == answer.id,
            GradingResult.status.in_(["suggested", "accepted", "modified", "stale"]),
        )
    ).all():
        previous.status = "superseded"
    result = GradingResult(
        grading_job_id=job.id,
        student_answer_id=answer.id,
        question_id=question.id,
        rubric_version_id=assignment.active_rubric_version_id,
        grading_method=method,
        provider=provider,
        provider_version=version,
        prompt_version=job.prompt_version,
        score=suggestion.score,
        max_score=question.max_score,
        confidence=suggestion.confidence,
        recognized_answer_snapshot=text,
        reasoning_summary=suggestion.summary,
        error_type=suggestion.error_type,
        student_feedback=suggestion.feedback,
        requires_review=requires,
        status="suggested",
    )
    db.add(result)
    # A successful regrade creates a fresh result under the active Rubric.
    # Clear the answer-level stale marker so the teacher can review/accept it.
    answer.status = "graded"
    db.flush()
    rubric_items = db.scalars(
        select(RubricItem)
        .where(RubricItem.question_rubric_id == rubric.id)
        .order_by(RubricItem.display_order)
    ).all()
    item_maximum = sum((Decimal(item.points) for item in rubric_items), Decimal("0"))
    if rubric_items and item_maximum != Decimal(question.max_score):
        raise ApiProblem(409, "RUBRIC_CRITERIA_TOTAL_MISMATCH", "评分分项总分与题目满分不一致")
    for item in rubric_items:
        awarded = (
            Decimal(item.points)
            if suggestion.score == Decimal(question.max_score)
            else Decimal("0")
            if suggestion.score is not None
            else None
        )
        db.add(
            GradingCriterionResult(
                grading_result_id=result.id,
                rubric_item_id=item.id,
                status="evaluated" if awarded is not None else "unavailable",
                awarded_points=awarded,
                max_points=item.points,
                reason=suggestion.summary,
                confidence=suggestion.confidence,
            )
        )
    regions = db.scalars(
        select(StudentAnswerRegion).where(StudentAnswerRegion.student_answer_id == answer.id)
    ).all()
    for region in regions:
        values = [
            Decimal(region.x),
            Decimal(region.y),
            Decimal(region.width),
            Decimal(region.height),
        ]
        if (
            any(value < 0 or value > 1 for value in values)
            or values[0] + values[2] > 1
            or values[1] + values[3] > 1
        ):
            raise ApiProblem(422, "EVIDENCE_REGION_INVALID", "证据区域必须位于页面 0–1 坐标内")
        db.add(
            GradingEvidence(
                grading_result_id=result.id,
                student_answer_id=answer.id,
                submission_page_id=region.submission_page_id,
                evidence_type="answer_region",
                quote=text[:500] or None,
                x=region.x,
                y=region.y,
                width=region.width,
                height=region.height,
                description="OCR/教师标注答案区域",
            )
        )
    db.commit()
    db.refresh(result)
    return {
        "id": str(result.id),
        "method": method,
        "provider": provider,
        "provider_version": version,
        "prompt_version": result.prompt_version,
        "score": str(result.score) if result.score is not None else None,
        "max_score": str(result.max_score),
        "confidence": str(result.confidence) if result.confidence is not None else None,
        "requires_review": result.requires_review,
        "status": result.status,
        "reasoning_summary": result.reasoning_summary,
        "criterion_count": len(rubric_items),
        "evidence_count": len(regions),
    }


@router.put("/student-answers/{answer_id}/review")
def review_answer(answer_id: uuid.UUID, data: ReviewInput, db: Db, actor: Actor) -> dict[str, Any]:
    answer = db.scalar(
        select(StudentAnswer)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(StudentAnswer.id == answer_id, Submission.owner_id == actor.id)
    )
    if answer is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    submission = owned_submission(db, actor.id, answer.submission_id)
    assignment = owned_assignment(db, actor.id, submission.assignment_id)
    question = db.get(Question, answer.question_id)
    result = db.scalar(
        select(GradingResult)
        .where(GradingResult.student_answer_id == answer.id, GradingResult.status != "superseded")
        .order_by(GradingResult.created_at.desc())
    )
    if data.decision == "accepted":
        effective = (
            answer.corrected_text
            if answer.corrected_text is not None
            else (answer.recognized_text or "")
        )
        if (
            result is None
            or result.status != "suggested"
            or result.rubric_version_id != assignment.active_rubric_version_id
            or result.recognized_answer_snapshot != effective
        ):
            raise ApiProblem(
                409,
                "GRADING_RESULT_STALE",
                "答案或 Rubric 已变化，旧建议不能接受；请重新批改或人工评分",
            )
    score = (
        data.final_score
        if data.final_score is not None
        else (result.score if data.decision == "accepted" and result else None)
    )
    if data.decision in {"accepted", "modified", "manual_scored"} and score is None:
        raise ApiProblem(422, "FINAL_SCORE_REQUIRED", "确认结果必须包含最终分数")
    if score is not None and (
        question is None or question.max_score is None or score > Decimal(question.max_score)
    ):
        raise ApiProblem(422, "SCORE_OUT_OF_RANGE", "最终分数超出合法范围")
    review = db.scalar(select(TeacherReview).where(TeacherReview.student_answer_id == answer.id))
    if review is None:
        review = TeacherReview(
            student_answer_id=answer.id,
            grading_result_id=result.id if result else None,
            reviewer_id=actor.id,
            decision=data.decision,
            final_score=score,
        )
        db.add(review)
        db.flush()
    else:
        if review.final_score != score or review.final_feedback != data.final_feedback:
            db.add(
                ScoreRevision(
                    teacher_review_id=review.id,
                    student_answer_id=answer.id,
                    actor_id=actor.id,
                    previous_score=review.final_score,
                    new_score=score,
                    previous_feedback=review.final_feedback,
                    new_feedback=data.final_feedback,
                    reason=data.reason or "教师复核修改",
                )
            )
        review.decision, review.final_score = data.decision, score
    review.grading_result_id = (
        result.id
        if result
        and result.status == "suggested"
        and result.rubric_version_id == assignment.active_rubric_version_id
        else None
    )
    review.final_feedback, review.final_error_type, review.review_notes, review.confirmed_at = (
        data.final_feedback,
        data.final_error_type,
        data.review_notes,
        now_utc(),
    )
    if result and result.status == "suggested":
        result.status = (
            "accepted"
            if data.decision == "accepted"
            else "modified"
            if data.decision in {"modified", "manual_scored"}
            else "rejected"
        )
    answer.requires_review = data.decision == "needs_more_information"
    audit(db, actor.id, "grading.review", "student_answer", answer.id, {"decision": data.decision})
    db.commit()
    return {
        "id": str(review.id),
        "decision": review.decision,
        "final_score": str(review.final_score) if review.final_score is not None else None,
    }


def acceptance_eligibility(
    db: Session, answer: StudentAnswer, assignment: Assignment
) -> tuple[list[str], GradingResult | None]:
    reasons: list[str] = []
    result = db.scalar(
        select(GradingResult)
        .where(
            GradingResult.student_answer_id == answer.id,
            GradingResult.status != "superseded",
        )
        .order_by(GradingResult.created_at.desc())
    )
    if result is None:
        return ["RESULT_MISSING"], None
    if result.status != "suggested":
        reasons.append("RESULT_NOT_SUGGESTED")
    if result.score is None or result.score < 0 or result.score > result.max_score:
        reasons.append("SCORE_INVALID")
    if answer.status == "stale" or result.status == "stale":
        reasons.append("STALE")
    if result.rubric_version_id != assignment.active_rubric_version_id:
        reasons.append("RUBRIC_VERSION_MISMATCH")
    if answer.recognized_latex or answer.corrected_latex:
        reasons.append("FORMULA_UNAVAILABLE")
    threshold = Decimal(str(get_settings().grading_auto_accept_confidence))
    if result.confidence is None or result.confidence < threshold:
        reasons.append("CONFIDENCE_LOW")
    if answer.requires_review or result.requires_review:
        reasons.append("REQUIRES_REVIEW")
    if result.provider in {"fake", "unavailable"}:
        reasons.append("PROVIDER_NOT_ELIGIBLE")
    effective = (
        answer.corrected_text
        if answer.corrected_text is not None
        else (answer.recognized_text or "")
    )
    if result.recognized_answer_snapshot != effective:
        reasons.append("ANSWER_CHANGED")
    rubric = db.scalar(
        select(QuestionRubric).where(
            QuestionRubric.rubric_version_id == assignment.active_rubric_version_id,
            QuestionRubric.question_id == answer.question_id,
        )
    )
    expected = (
        db.scalar(
            select(func.count())
            .select_from(RubricItem)
            .where(RubricItem.question_rubric_id == rubric.id)
        )
        if rubric
        else 0
    ) or 0
    actual = (
        db.scalar(
            select(func.count())
            .select_from(GradingCriterionResult)
            .where(
                GradingCriterionResult.grading_result_id == result.id,
                GradingCriterionResult.status == "evaluated",
            )
        )
        or 0
    )
    if actual != expected:
        reasons.append("CRITERION_INCOMPLETE")
    question = db.get(Question, answer.question_id)
    evidence_count = (
        db.scalar(
            select(func.count())
            .select_from(GradingEvidence)
            .where(GradingEvidence.grading_result_id == result.id)
        )
        or 0
    )
    if (
        question
        and question.question_type
        not in {
            "single_choice",
            "multiple_choice",
            "true_false",
            "fill_blank",
        }
        and not evidence_count
    ):
        reasons.append("EVIDENCE_REQUIRED")
    return list(dict.fromkeys(reasons)), result


@router.get("/grading-batches/{batch_id}/bulk-accept-eligibility")
def bulk_accept_eligibility(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    answers = db.scalars(
        select(StudentAnswer)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(Submission.grading_batch_id == batch.id, Submission.owner_id == actor.id)
    ).all()
    assignment = owned_assignment(db, actor.id, batch.assignment_id)
    items = []
    reason_counts: dict[str, int] = {}
    for answer in answers:
        reasons, _ = acceptance_eligibility(db, answer, assignment)
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        items.append({"answer_id": str(answer.id), "eligible": not reasons, "reasons": reasons})
    return {
        "eligible_count": sum(item["eligible"] for item in items),
        "excluded_count": sum(not item["eligible"] for item in items),
        "reason_counts": reason_counts,
        "items": items,
    }


@router.get("/grading-batches/{batch_id}/review-workspace")
def review_workspace(
    batch_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    question_id: uuid.UUID | None = None,
    submission_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    submission_filters: list[Any] = [
        Submission.grading_batch_id == batch.id,
        Submission.owner_id == actor.id,
    ]
    if submission_id:
        submission_filters.append(Submission.id == submission_id)
    submissions = db.scalars(select(Submission).where(*submission_filters)).all()
    items: list[dict[str, Any]] = []
    for submission in submissions:
        pages = db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission.id)
            .order_by(SubmissionPage.page_number)
        ).all()
        answer_filters: list[Any] = [StudentAnswer.submission_id == submission.id]
        if question_id:
            answer_filters.append(StudentAnswer.question_id == question_id)
        answers = db.scalars(select(StudentAnswer).where(*answer_filters)).all()
        answer_items: list[dict[str, Any]] = []
        for answer in answers:
            question = db.get(Question, answer.question_id)
            result = db.scalar(
                select(GradingResult)
                .where(
                    GradingResult.student_answer_id == answer.id,
                    GradingResult.status != "superseded",
                )
                .order_by(GradingResult.created_at.desc())
            )
            review = db.scalar(
                select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
            )
            criteria = (
                db.scalars(
                    select(GradingCriterionResult).where(
                        GradingCriterionResult.grading_result_id == result.id
                    )
                ).all()
                if result
                else []
            )
            evidence = (
                db.scalars(
                    select(GradingEvidence).where(GradingEvidence.grading_result_id == result.id)
                ).all()
                if result
                else []
            )
            answer_items.append(
                {
                    **answer_json(answer),
                    "question": {
                        "id": str(question.id) if question else str(answer.question_id),
                        "number": question.question_number if question else "?",
                        "type": question.question_type if question else "unknown",
                        "content": question.content_text if question else None,
                        "max_score": str(question.max_score)
                        if question and question.max_score
                        else None,
                    },
                    "result": {
                        "id": str(result.id),
                        "status": result.status,
                        "rubric_version_id": str(result.rubric_version_id),
                        "score": str(result.score) if result.score is not None else None,
                        "provider": result.provider,
                        "provider_version": result.provider_version,
                        "confidence": str(result.confidence)
                        if result.confidence is not None
                        else None,
                        "requires_review": result.requires_review,
                        "reasoning": result.reasoning_summary,
                    }
                    if result
                    else None,
                    "review": {
                        "decision": review.decision,
                        "final_score": str(review.final_score)
                        if review.final_score is not None
                        else None,
                        "feedback": review.final_feedback,
                        "error_type": review.final_error_type,
                    }
                    if review
                    else None,
                    "criteria": [
                        {
                            "rubric_item_id": str(item.rubric_item_id),
                            "status": item.status,
                            "awarded_points": str(item.awarded_points)
                            if item.awarded_points is not None
                            else None,
                            "max_points": str(item.max_points),
                            "reason": item.reason,
                        }
                        for item in criteria
                    ],
                    "evidence": [
                        {
                            "id": str(item.id),
                            "submission_page_id": str(item.submission_page_id),
                            "quote": item.quote,
                            "x": str(item.x) if item.x is not None else None,
                            "y": str(item.y) if item.y is not None else None,
                            "width": str(item.width) if item.width is not None else None,
                            "height": str(item.height) if item.height is not None else None,
                        }
                        for item in evidence
                    ],
                }
            )
        page_items: list[dict[str, Any]] = []
        for page in pages:
            original = db.get(StoredFile, page.stored_file_id)
            page_items.append(
                {
                    "id": str(page.id),
                    "page_number": page.page_number,
                    "status": page.status,
                    "original_url": storage.presigned_get(original.storage_key, 900)
                    if original
                    else None,
                    "processed_url": storage.presigned_get(page.processed_storage_key, 900)
                    if page.processed_storage_key
                    else None,
                    "thumbnail_url": storage.presigned_get(page.thumbnail_storage_key, 900)
                    if page.thumbnail_storage_key
                    else None,
                }
            )
        items.append(
            {
                "submission_id": str(submission.id),
                "student_id": str(submission.student_id) if submission.student_id else None,
                "status": submission.status,
                "pages": page_items,
                "answers": answer_items,
            }
        )
    total_answers = sum(len(item["answers"]) for item in items)
    reviewed_answers = sum(
        sum(
            answer["review"] is not None
            and answer["review"]["final_score"] is not None
            and not answer["requires_review"]
            for answer in item["answers"]
        )
        for item in items
    )
    return {
        "batch": batch_json(db, batch),
        "items": items,
        "progress": {"total": total_answers, "reviewed": reviewed_answers},
        "provider_notice": "主观题评分 Provider unavailable 时必须教师人工评分",
    }


@router.post("/grading-batches/{batch_id}/bulk-accept")
def bulk_accept(batch_id: uuid.UUID, data: BulkAcceptInput, db: Db, actor: Actor) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    assignment = owned_assignment(db, actor.id, batch.assignment_id)
    answers = db.scalars(
        select(StudentAnswer)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(
            StudentAnswer.id.in_(data.answer_ids),
            Submission.grading_batch_id == batch.id,
            Submission.owner_id == actor.id,
        )
    ).all()
    if len(answers) != len(set(data.answer_ids)):
        raise ApiProblem(422, "BULK_ACCEPT_SCOPE_INVALID", "答案列表包含不存在或越权项目")
    excluded: list[dict[str, Any]] = []
    accepted: list[str] = []
    for answer in answers:
        reasons, result = acceptance_eligibility(db, answer, assignment)
        if reasons or result is None:
            excluded.append({"answer_id": str(answer.id), "reasons": reasons})
            continue
        review = db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
        )
        if review is None:
            review = TeacherReview(
                student_answer_id=answer.id,
                grading_result_id=result.id,
                reviewer_id=actor.id,
                decision="accepted",
                final_score=result.score,
                confirmed_at=now_utc(),
            )
            db.add(review)
        result.status = "accepted"
        accepted.append(str(answer.id))
    audit(
        db,
        actor.id,
        "grading.bulk_accept",
        "grading_batch",
        batch.id,
        {"accepted_count": len(accepted), "excluded_count": len(excluded)},
    )
    db.commit()
    return {"accepted_answer_ids": accepted, "excluded": excluded}


@router.get("/grading-batches/{batch_id}/questions/{question_id}/consistency")
def question_consistency(
    batch_id: uuid.UUID,
    question_id: uuid.UUID,
    db: Db,
    actor: Actor,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    rows = db.execute(
        select(StudentAnswer, GradingResult)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .join(GradingResult, GradingResult.student_answer_id == StudentAnswer.id)
        .where(
            Submission.grading_batch_id == batch.id,
            Submission.owner_id == actor.id,
            StudentAnswer.question_id == question_id,
            GradingResult.status.in_(["suggested", "accepted", "modified"]),
        )
        .order_by(StudentAnswer.id)
    ).all()
    grouped: dict[str, list[tuple[StudentAnswer, GradingResult]]] = {}
    for answer, result in rows:
        text = (
            answer.corrected_text
            if answer.corrected_text is not None
            else (answer.recognized_text or "")
        )
        normalized = "".join(text.casefold().split())
        grouped.setdefault(normalized, []).append((answer, result))
    items: list[dict[str, Any]] = []
    for normalized, group in grouped.items():
        scores = {str(result.score) for _, result in group}
        errors = {result.error_type for _, result in group}
        rubric_versions = {str(result.rubric_version_id) for _, result in group}
        criterion_signatures = {
            tuple(
                (str(item.rubric_item_id), str(item.awarded_points))
                for item in db.scalars(
                    select(GradingCriterionResult)
                    .where(GradingCriterionResult.grading_result_id == result.id)
                    .order_by(GradingCriterionResult.rubric_item_id)
                )
            )
            for _, result in group
        }
        items.append(
            {
                "normalized_answer": normalized,
                "answer_ids": [str(answer.id) for answer, _ in group],
                "rubric_version_ids": sorted(rubric_versions),
                "score_difference": len(scores) > 1 and len(rubric_versions) == 1,
                "error_type_difference": len(errors) > 1 and len(rubric_versions) == 1,
                "criterion_difference": len(criterion_signatures) > 1 and len(rubric_versions) == 1,
            }
        )
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
    }


@router.post("/grading-batches/{batch_id}/regrade")
def regrade_batch(batch_id: uuid.UUID, data: RegradeInput, db: Db, actor: Actor) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    filters: list[Any] = [Submission.grading_batch_id == batch.id, Submission.owner_id == actor.id]
    if data.submission_ids:
        filters.append(Submission.id.in_(data.submission_ids))
    if data.question_id:
        filters.append(StudentAnswer.question_id == data.question_id)
    if data.only_stale:
        filters.append(StudentAnswer.status == "stale")
    answers = db.scalars(select(StudentAnswer).join(Submission).where(*filters)).all()
    if data.only_unreviewed:
        reviewed_ids = set(db.scalars(select(TeacherReview.student_answer_id)).all())
        answers = [answer for answer in answers if answer.id not in reviewed_ids]
    result_ids: list[str] = []
    for answer in answers:
        outcome = grade_answer(answer.id, db, actor)
        result_ids.append(outcome["id"])
    audit(db, actor.id, "grading.regrade", "grading_batch", batch.id, {"count": len(result_ids)})
    db.commit()
    return {"count": len(result_ids), "grading_result_ids": result_ids}


@router.post("/submissions/{submission_id}/finalize")
def finalize_submission(submission_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    submission = owned_submission(db, actor.id, submission_id)
    assignment = owned_assignment(db, actor.id, submission.assignment_id)
    questions = db.scalars(
        select(Question).where(
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    ).all()
    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
    ).all()
    by_q = {a.question_id: a for a in answers}
    problems: list[dict[str, str]] = []
    details: list[dict[str, Any]] = []
    total = Decimal("0")
    maximum = Decimal("0")
    active_rubric = (
        db.get(RubricVersion, assignment.active_rubric_version_id)
        if assignment.active_rubric_version_id
        else None
    )
    if active_rubric is None or active_rubric.status != "confirmed":
        problems.append({"code": "RUBRIC_VERSION_NOT_CONFIRMED", "question_id": ""})
    for q in questions:
        if q.max_score is None or Decimal(q.max_score) <= 0:
            problems.append({"code": "QUESTION_SCORE_REQUIRED", "question_id": str(q.id)})
            continue
        maximum += Decimal(q.max_score)
        answer = by_q.get(q.id)
        if answer is None:
            problems.append({"code": "ANSWER_MISSING", "question_id": str(q.id)})
            continue
        review = db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
        )
        if review is None or review.final_score is None or answer.requires_review:
            problems.append({"code": "REVIEW_REQUIRED", "question_id": str(q.id)})
            continue
        if review.final_score < 0 or review.final_score > Decimal(q.max_score):
            problems.append({"code": "SCORE_OUT_OF_RANGE", "question_id": str(q.id)})
            continue
        current_question_rubric = db.scalar(
            select(QuestionRubric).where(
                QuestionRubric.rubric_version_id == assignment.active_rubric_version_id,
                QuestionRubric.question_id == q.id,
            )
        )
        if current_question_rubric is None:
            problems.append({"code": "RUBRIC_INCOMPLETE", "question_id": str(q.id)})
            continue
        latest_result = db.scalar(
            select(GradingResult)
            .where(
                GradingResult.student_answer_id == answer.id,
                GradingResult.status != "superseded",
            )
            .order_by(GradingResult.created_at.desc())
        )
        manually_reconfirmed = (
            review.decision in {"modified", "manual_scored"}
            and review.confirmed_at is not None
            and active_rubric is not None
            and review.confirmed_at >= active_rubric.created_at
        )
        if (
            latest_result
            and (
                latest_result.status == "stale"
                or latest_result.rubric_version_id != assignment.active_rubric_version_id
            )
            and not manually_reconfirmed
        ):
            problems.append({"code": "STALE_RUBRIC", "question_id": str(q.id)})
            continue
        active_result = db.scalar(
            select(GradingResult)
            .where(
                GradingResult.student_answer_id == answer.id,
                GradingResult.status.in_(["accepted", "modified"]),
            )
            .order_by(GradingResult.created_at.desc())
        )
        if active_result and active_result.rubric_version_id != assignment.active_rubric_version_id:
            problems.append({"code": "STALE_RUBRIC", "question_id": str(q.id)})
            continue
        total += Decimal(review.final_score)
        details.append(
            {
                "question_id": str(q.id),
                "question_number": q.question_number,
                "question_type": q.question_type,
                "student_answer_id": str(answer.id),
                "teacher_review_id": str(review.id),
                "score": str(review.final_score),
                "max_score": str(q.max_score),
                "error_type": review.final_error_type,
                "feedback": review.final_feedback,
                "final_error_type": review.final_error_type,
                "final_feedback": review.final_feedback,
                "knowledge_point_ids": [
                    str(value)
                    for value in db.scalars(
                        select(QuestionKnowledgePoint.knowledge_point_id).where(
                            QuestionKnowledgePoint.question_id == q.id
                        )
                    )
                ],
                "grading_method": active_result.grading_method if active_result else "manual",
                "finalized_at": now_utc().isoformat(),
            }
        )
    version = (
        db.scalar(
            select(func.max(SubmissionScoreSnapshot.version)).where(
                SubmissionScoreSnapshot.submission_id == submission.id
            )
        )
        or 0
    ) + 1
    if (
        not submission.student_id
        or not assignment.active_paper_version_id
        or not assignment.active_rubric_version_id
    ):
        problems.append({"code": "SUBMISSION_VERSION_INCOMPLETE", "question_id": ""})
    status = "incomplete" if problems else "complete"
    snapshot = SubmissionScoreSnapshot(
        submission_id=submission.id,
        assignment_id=assignment.id,
        student_id=submission.student_id,
        paper_version_id=assignment.active_paper_version_id,
        rubric_version_id=assignment.active_rubric_version_id,
        total_score=total if not problems else None,
        max_score=maximum,
        status=status,
        generated_by=actor.id,
        version=version,
        details=details,
    )
    db.add(snapshot)
    if not problems:
        submission.status, submission.finalized_at = "finalized", now_utc()
    audit(
        db,
        actor.id,
        "submission.finalize",
        "submission",
        submission.id,
        {"snapshot_status": status, "problems": problems},
    )
    db.commit()
    db.refresh(snapshot)
    return {
        "id": str(snapshot.id),
        "submission_id": str(submission.id),
        "status": status,
        "version": version,
        "total_score": str(snapshot.total_score) if snapshot.total_score is not None else None,
        "max_score": str(maximum),
        "details": details,
        "problems": problems,
    }


@router.get("/assignments/{assignment_id}/score-snapshots")
def score_snapshots(
    assignment_id: uuid.UUID,
    db: Db,
    actor: Actor,
    status: Literal["complete", "incomplete"] = "complete",
) -> list[dict[str, Any]]:
    owned_assignment(db, actor.id, assignment_id)
    rows = db.scalars(
        select(SubmissionScoreSnapshot)
        .join(Submission, Submission.id == SubmissionScoreSnapshot.submission_id)
        .where(
            SubmissionScoreSnapshot.assignment_id == assignment_id,
            Submission.owner_id == actor.id,
            SubmissionScoreSnapshot.status == status,
        )
        .order_by(SubmissionScoreSnapshot.generated_at.desc())
    ).all()
    return [
        {
            "id": str(x.id),
            "submission_id": str(x.submission_id),
            "student_id": str(x.student_id),
            "paper_version_id": str(x.paper_version_id),
            "rubric_version_id": str(x.rubric_version_id),
            "total_score": str(x.total_score) if x.total_score is not None else None,
            "max_score": str(x.max_score),
            "status": x.status,
            "version": x.version,
            "details": x.details,
        }
        for x in rows
    ]
