import statistics
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, cast

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.db.session import get_db
from app.models import (
    AnalyticsSnapshot,
    Assignment,
    AssignmentClass,
    ClassStudent,
    GradeRelease,
    GradeReleaseItem,
    KnowledgePoint,
    ReportJob,
    ReportJobStudentScope,
    SchoolClass,
    ScoreRevision,
    StoredFile,
    Student,
    Submission,
    SubmissionScoreSnapshot,
    TeachingInsight,
    now_utc,
)
from app.results.services import FinalScoreService, create_analytics, release_scores
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["results"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


class ReleaseInput(BaseModel):
    assignment_id: uuid.UUID
    class_id: uuid.UUID
    release_mode: Literal["score_and_feedback", "feedback_only", "score_only", "internal_only"] = (
        "score_and_feedback"
    )
    exclude_student_ids: list[uuid.UUID] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    notes: str | None = Field(None, max_length=2000)
    idempotency_key: str | None = Field(None, max_length=100)


class InsightEdit(BaseModel):
    recommendations: list[str] = Field(min_length=1, max_length=20)


def owned_analytics(db: Session, actor_id: uuid.UUID, snapshot_id: uuid.UUID) -> AnalyticsSnapshot:
    snapshot = db.scalar(
        select(AnalyticsSnapshot).where(
            AnalyticsSnapshot.id == snapshot_id,
            AnalyticsSnapshot.owner_id == actor_id,
            AnalyticsSnapshot.status == "complete",
        )
    )
    if snapshot is None:
        raise ApiProblem(404, "ANALYTICS_SNAPSHOT_NOT_FOUND", "分析快照不存在")
    release = db.scalar(
        select(GradeRelease).where(
            GradeRelease.id == snapshot.grade_release_id,
            GradeRelease.owner_id == actor_id,
            GradeRelease.assignment_id == snapshot.assignment_id,
            GradeRelease.class_id == snapshot.class_id,
            GradeRelease.status == "released",
        )
    )
    if release is None:
        raise ApiProblem(409, "ANALYTICS_SOURCE_INVALID", "分析快照引用的发布版本无效")
    return snapshot


def page_result(items: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise ApiProblem(422, "ANALYTICS_DRILLDOWN_INVALID", "分页参数无效")
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


def released_rows(db: Session, snapshot: AnalyticsSnapshot) -> list[Any]:
    try:
        return release_scores(db, snapshot.grade_release_id)
    except ValueError as exc:
        raise ApiProblem(409, "ANALYTICS_SOURCE_INVALID", "发布成绩来源无效") from exc


def owned_release(db: Session, actor_id: uuid.UUID, release_id: uuid.UUID) -> GradeRelease:
    release = db.scalar(
        select(GradeRelease).where(GradeRelease.id == release_id, GradeRelease.owner_id == actor_id)
    )
    if release is None:
        raise ApiProblem(404, "GRADE_RELEASE_NOT_FOUND", "成绩发布批次不存在")
    return release


def released_release(db: Session, actor_id: uuid.UUID, release_id: uuid.UUID) -> GradeRelease:
    release = owned_release(db, actor_id, release_id)
    if release.status != "released":
        raise ApiProblem(409, "GRADE_RELEASE_NOT_ACTIVE", "只有已发布版本可生成报告或分析")
    return release


@router.get("/assignments/{assignment_id}/classes/{class_id}/grade-readiness")
def readiness(
    assignment_id: uuid.UUID, class_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.owner_id == actor.id)
    )
    school_class = db.scalar(
        select(SchoolClass).where(SchoolClass.id == class_id, SchoolClass.owner_id == actor.id)
    )
    linked = db.scalar(
        select(AssignmentClass).where(
            AssignmentClass.assignment_id == assignment_id, AssignmentClass.class_id == class_id
        )
    )
    if assignment is None or school_class is None or linked is None:
        raise ApiProblem(404, "ASSIGNMENT_CLASS_NOT_FOUND", "作业或班级不存在或未关联")
    student_ids = set(
        db.scalars(
            select(ClassStudent.student_id).where(
                ClassStudent.class_id == class_id, ClassStudent.status == "active"
            )
        )
    )
    submissions = db.scalars(
        select(Submission).where(
            Submission.owner_id == actor.id,
            Submission.assignment_id == assignment_id,
            Submission.class_id == class_id,
        )
    ).all()
    submissions_by_student: dict[uuid.UUID, list[Submission]] = {}
    for submission in submissions:
        if submission.student_id in student_ids:
            submissions_by_student.setdefault(submission.student_id, []).append(submission)
    valid, invalid = [], []
    service = FinalScoreService(db, actor.id)
    latest_complete_by_student: dict[uuid.UUID, Any] = {}
    for score_row in service.latest(assignment_id, class_id):
        student_id = score_row.payload.student_id
        if student_id not in student_ids:
            continue
        current = latest_complete_by_student.get(student_id)
        if current is None or (
            score_row.snapshot.generated_at,
            score_row.snapshot.version,
            str(score_row.snapshot.id),
        ) > (
            current.snapshot.generated_at,
            current.snapshot.version,
            str(current.snapshot.id),
        ):
            latest_complete_by_student[student_id] = score_row
    for student_id, student_submissions in submissions_by_student.items():
        row = latest_complete_by_student.get(student_id)
        try:
            if row is None:
                raise ValueError("COMPLETE_SNAPSHOT_MISSING")
            valid.append(
                {
                    "student_id": str(student_id),
                    "submission_id": str(row.submission.id),
                    "score_snapshot_id": str(row.snapshot.id),
                }
            )
        except ValueError as exc:
            latest_submission = max(
                student_submissions, key=lambda item: (item.created_at, str(item.id))
            )
            invalid.append(
                {
                    "code": str(exc).split(":", 1)[0],
                    "student_id": str(student_id),
                    "submission_id": str(latest_submission.id),
                    "reason": str(exc),
                }
            )
    missing = sorted(str(x) for x in student_ids - set(submissions_by_student))
    audit(
        db,
        actor.id,
        "grade_release.check",
        "assignment",
        assignment_id,
        {"ready": len(valid), "invalid": len(invalid), "missing": len(missing)},
    )
    db.commit()
    return {
        "releasable_count": len(valid),
        "unreleasable_count": len(invalid) + len(missing),
        "ready": valid,
        "errors": invalid,
        "missing_student_ids": missing,
    }


@router.post("/grade-releases", status_code=201)
def create_release(data: ReleaseInput, db: Db, actor: Actor) -> dict[str, Any]:
    if data.idempotency_key:
        existing = db.scalar(
            select(GradeRelease).where(
                GradeRelease.idempotency_key == data.idempotency_key,
                GradeRelease.owner_id == actor.id,
            )
        )
        if existing:
            return release_view(db, existing)
    check = readiness(data.assignment_id, data.class_id, db, actor)
    excluded = set(data.exclude_student_ids)
    ready = [x for x in check["ready"] if uuid.UUID(x["student_id"]) not in excluded]
    if not ready:
        raise ApiProblem(422, "NO_RELEASABLE_SCORES", "没有可发布的完整成绩")
    version = (
        db.scalar(
            select(func.max(GradeRelease.version)).where(
                GradeRelease.assignment_id == data.assignment_id,
                GradeRelease.class_id == data.class_id,
            )
        )
        or 0
    ) + 1
    release = GradeRelease(
        owner_id=actor.id,
        assignment_id=data.assignment_id,
        class_id=data.class_id,
        version=version,
        status="scheduled" if data.scheduled_at else "released",
        release_mode=data.release_mode,
        scheduled_at=data.scheduled_at,
        released_at=None if data.scheduled_at else now_utc(),
        created_by=actor.id,
        notes=data.notes,
        idempotency_key=data.idempotency_key,
    )
    db.add(release)
    db.flush()
    for row in ready:
        db.add(
            GradeReleaseItem(
                grade_release_id=release.id,
                student_id=uuid.UUID(row["student_id"]),
                submission_id=uuid.UUID(row["submission_id"]),
                score_snapshot_id=uuid.UUID(row["score_snapshot_id"]),
            )
        )
    audit(
        db,
        actor.id,
        "grade_release.create",
        "grade_release",
        release.id,
        {"version": version, "item_count": len(ready), "status": release.status},
    )
    db.commit()
    return release_view(db, release)


def release_view(db: Session, release: GradeRelease) -> dict[str, Any]:
    items = db.scalars(
        select(GradeReleaseItem).where(GradeReleaseItem.grade_release_id == release.id)
    ).all()
    return {
        "id": str(release.id),
        "assignment_id": str(release.assignment_id),
        "class_id": str(release.class_id),
        "version": release.version,
        "status": release.status,
        "release_mode": release.release_mode,
        "released_at": release.released_at,
        "scheduled_at": release.scheduled_at,
        "meaning": "已确认发布数据，尚未发送到学生端。",
        "items": [
            {
                "student_id": str(x.student_id),
                "submission_id": str(x.submission_id),
                "score_snapshot_id": str(x.score_snapshot_id),
                "status": x.status,
            }
            for x in items
        ],
    }


@router.get("/grade-releases")
def list_releases(
    db: Db, actor: Actor, assignment_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    query = select(GradeRelease).where(GradeRelease.owner_id == actor.id)
    if assignment_id:
        query = query.where(GradeRelease.assignment_id == assignment_id)
    return [release_view(db, x) for x in db.scalars(query.order_by(GradeRelease.created_at.desc()))]


@router.get("/grade-releases/{release_id}")
def get_release(release_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return release_view(db, owned_release(db, actor.id, release_id))


@router.post("/grade-releases/{release_id}/cancel")
def cancel_release(release_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    release = owned_release(db, actor.id, release_id)
    if release.status != "scheduled":
        raise ApiProblem(409, "RELEASE_IMMUTABLE", "只能取消尚未执行的定时发布")
    release.status = "cancelled"
    audit(db, actor.id, "grade_release.cancel", "grade_release", release.id, {})
    db.commit()
    return release_view(db, release)


@router.post("/grade-releases/{release_id}/reports", status_code=201)
def create_report(
    release_id: uuid.UUID,
    report_type: Literal["gradebook_xlsx", "student_report_pdf", "batch_student_reports"],
    idempotency_key: str,
    db: Db,
    actor: Actor,
    student_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    release = released_release(db, actor.id, release_id)
    existing = db.scalar(
        select(ReportJob).where(
            ReportJob.owner_id == actor.id, ReportJob.idempotency_key == idempotency_key
        )
    )
    if existing:
        return report_view(db, existing)
    if report_type == "student_report_pdf":
        included = db.scalar(
            select(GradeReleaseItem.id).where(
                GradeReleaseItem.grade_release_id == release.id,
                GradeReleaseItem.student_id == student_id,
                GradeReleaseItem.status == "included",
            )
        )
        if student_id is None or included is None:
            raise ApiProblem(422, "REPORT_STUDENT_NOT_RELEASED", "学生不在该发布版本中")
    job = ReportJob(
        owner_id=actor.id,
        assignment_id=release.assignment_id,
        class_id=release.class_id,
        grade_release_id=release.id,
        report_type=report_type,
        idempotency_key=idempotency_key,
        expires_at=now_utc() + timedelta(days=30),
    )
    db.add(job)
    db.flush()
    if student_id is not None:
        db.add(ReportJobStudentScope(report_job_id=job.id, student_id=student_id))
    audit(
        db,
        actor.id,
        "report.generate",
        "report_job",
        job.id,
        {"type": report_type, "status": job.status},
    )
    db.commit()
    try:
        from workers.celery_app import celery_app

        celery_app.send_task("ahamark.report.run", args=[str(job.id)])
    except Exception as exc:
        job.status, job.error_code, job.error_message = (
            "failed",
            "WORKER_UNAVAILABLE",
            type(exc).__name__,
        )
        db.commit()
    return report_view(db, job)


@router.get("/grade-releases/{release_id}/reports")
def list_reports(release_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    release = released_release(db, actor.id, release_id)
    jobs = db.scalars(
        select(ReportJob)
        .where(
            ReportJob.owner_id == actor.id,
            ReportJob.grade_release_id == release.id,
        )
        .order_by(ReportJob.created_at.desc())
    ).all()
    return [report_view(db, job) for job in jobs]


def report_is_expired(job: ReportJob) -> bool:
    if job.expires_at is None:
        return False
    current = now_utc()
    if job.expires_at.tzinfo is None:
        current = current.replace(tzinfo=None)
    return job.expires_at <= current


def report_view(db: Session, job: ReportJob) -> dict[str, Any]:
    scope = db.get(ReportJobStudentScope, job.id)
    effective_status = (
        "expired"
        if job.status in {"completed", "partially_completed"} and report_is_expired(job)
        else job.status
    )
    return {
        "id": str(job.id),
        "report_type": job.report_type,
        "student_id": str(scope.student_id) if scope else None,
        "status": effective_status,
        "progress": job.progress,
        "stored_file_id": str(job.stored_file_id) if job.stored_file_id else None,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "expires_at": job.expires_at,
        "grade_release_id": str(job.grade_release_id),
    }


@router.get("/report-jobs/{job_id}")
def get_report(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    job = db.scalar(select(ReportJob).where(ReportJob.id == job_id, ReportJob.owner_id == actor.id))
    if job is None:
        raise ApiProblem(404, "REPORT_JOB_NOT_FOUND", "报告任务不存在")
    return report_view(db, job)


@router.post("/report-jobs/{job_id}/retry", status_code=201)
def retry_report(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    job = db.scalar(select(ReportJob).where(ReportJob.id == job_id, ReportJob.owner_id == actor.id))
    if job is None:
        raise ApiProblem(404, "REPORT_JOB_NOT_FOUND", "报告任务不存在")
    effective = report_view(db, job)["status"]
    if effective not in {"failed", "expired", "partially_completed"}:
        raise ApiProblem(409, "REPORT_JOB_NOT_RETRYABLE", "当前报告状态不可重新生成")
    scope = db.get(ReportJobStudentScope, job.id)
    replacement = create_report(
        job.grade_release_id,
        cast(
            Literal["gradebook_xlsx", "student_report_pdf", "batch_student_reports"],
            job.report_type,
        ),
        f"retry:{job.id}:{uuid.uuid4()}",
        db,
        actor,
        scope.student_id if scope else None,
    )
    audit(
        db,
        actor.id,
        "report.recreate",
        "report_job",
        job.id,
        {"replacement_id": replacement["id"], "previous_status": effective},
    )
    db.commit()
    return replacement


@router.get("/report-jobs/{job_id}/download")
def download_report(job_id: uuid.UUID, db: Db, actor: Actor, storage: Storage) -> dict[str, str]:
    job = db.scalar(
        select(ReportJob).where(
            ReportJob.id == job_id,
            ReportJob.owner_id == actor.id,
            ReportJob.status.in_(["completed", "partially_completed"]),
        )
    )
    stored = db.get(StoredFile, job.stored_file_id) if job else None
    if stored is None:
        raise ApiProblem(404, "REPORT_FILE_NOT_FOUND", "报告文件不存在")
    assert job is not None
    if report_is_expired(job):
        raise ApiProblem(409, "REPORT_JOB_EXPIRED", "报告任务已过期，请创建新的重试任务")
    audit(db, actor.id, "report.download", "report_job", job.id, {})
    db.commit()
    return {"url": storage.presigned_get(stored.storage_key, 900)}


@router.post("/grade-releases/{release_id}/analytics", status_code=201)
def generate_analytics(release_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    release = released_release(db, actor.id, release_id)
    snapshot = create_analytics(db, release)
    audit(
        db,
        actor.id,
        "analytics.generate",
        "analytics_snapshot",
        snapshot.id,
        {"source_count": snapshot.source_snapshot_count},
    )
    db.commit()
    db.refresh(snapshot)
    return {
        "id": str(snapshot.id),
        "grade_release_id": str(release.id),
        "schema_version": snapshot.schema_version,
        "status": snapshot.status,
        "source_snapshot_count": snapshot.source_snapshot_count,
        "generated_at": snapshot.generated_at,
        "metrics": snapshot.metrics,
    }


@router.post("/analytics/{analytics_id}/insights", status_code=201)
def generate_insight(analytics_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    snapshot = owned_analytics(db, actor.id, analytics_id)
    questions = sorted(
        snapshot.metrics.get("questions", []),
        key=lambda x: x.get("score_rate") if x.get("score_rate") is not None else 2,
    )[:3]
    evidence = [
        {
            "metric": "question_score_rate",
            "question_id": x["question_id"],
            "value": x["score_rate"],
            "participants": x["participants"],
        }
        for x in questions
    ]
    content = {
        "title": "课堂讲评建议",
        "generation_method": "rule_based",
        "disclaimer": "这是基于固定 AnalyticsSnapshot 的规则型教学建议，不是 AI 自动评分或诊断。",
        "rules_version": "rules-v1",
        "sample_warning": snapshot.source_snapshot_count < 5,
        "recommendations": [
            "优先讲评第"
            f"{x['question_number']}题（平均得分率 {x['score_rate']:.1%}，"
            f"样本 {x['participants']} 人）"
            for x in questions
        ],
    }
    insight = TeachingInsight(
        owner_id=actor.id, analytics_snapshot_id=snapshot.id, content=content, evidence=evidence
    )
    db.add(insight)
    db.flush()
    audit(
        db,
        actor.id,
        "teaching_insight.generate",
        "teaching_insight",
        insight.id,
        {"provider": "rule_based"},
    )
    db.commit()
    return {
        "id": str(insight.id),
        "provider": "rule_based",
        "status": insight.status,
        "content": content,
        "evidence": evidence,
    }


@router.get("/analytics/{snapshot_id}/score-bands/{band}/students")
def score_band_students(
    snapshot_id: uuid.UUID,
    band: str,
    db: Db,
    actor: Actor,
    page: int = 1,
    page_size: int = 20,
    sort: Literal["score_desc", "score_asc", "student_number"] = "score_desc",
) -> dict[str, Any]:
    snapshot = owned_analytics(db, actor.id, snapshot_id)
    boundaries = {
        "0-59": (0, 60),
        "60-69": (60, 70),
        "70-79": (70, 80),
        "80-89": (80, 90),
        "90-100": (90, 101),
    }
    if band not in boundaries:
        raise ApiProblem(422, "ANALYTICS_DRILLDOWN_INVALID", "未知分数段")
    low, high = boundaries[band]
    students = {x.id: x for x in db.scalars(select(Student).where(Student.owner_id == actor.id))}
    items = []
    for row in released_rows(db, snapshot):
        rate = float(row.payload.total_score / row.payload.max_score) * 100
        if low <= rate < high:
            student = students.get(row.payload.student_id)
            if student:
                items.append(
                    {
                        "student_id": str(student.id),
                        "student_name": student.name,
                        "student_number": student.student_number,
                        "total_score": float(row.payload.total_score),
                        "max_score": float(row.payload.max_score),
                        "score_rate": rate / 100,
                        "score_snapshot_id": str(row.snapshot.id),
                        "submission_id": str(row.submission.id),
                        "release_status": "released",
                    }
                )
    key = (
        (lambda x: x["student_number"])
        if sort == "student_number"
        else (lambda x: (x["score_rate"], x["student_number"]))
    )
    items.sort(key=key, reverse=sort == "score_desc")
    return page_result(items, page, page_size)


@router.get("/analytics/{snapshot_id}/questions/{question_id}/students")
def question_students(
    snapshot_id: uuid.UUID,
    question_id: uuid.UUID,
    db: Db,
    actor: Actor,
    page: int = 1,
    page_size: int = 20,
    score_filter: Literal["all", "full", "zero", "partial"] = "all",
    error_type: str | None = None,
    layer: str | None = None,
) -> dict[str, Any]:
    snapshot = owned_analytics(db, actor.id, snapshot_id)
    students = {x.id: x for x in db.scalars(select(Student).where(Student.owner_id == actor.id))}
    items = []
    for row in released_rows(db, snapshot):
        total_rate = float(row.payload.total_score / row.payload.max_score)
        student_layer = (
            "A"
            if total_rate >= 0.85
            else "B"
            if total_rate >= 0.70
            else "C"
            if total_rate >= 0.50
            else "D"
        )
        for detail in row.payload.details:
            if detail.question_id != question_id:
                continue
            state = (
                "full"
                if detail.score == detail.max_score
                else "zero"
                if detail.score == 0
                else "partial"
            )
            if (
                (score_filter != "all" and state != score_filter)
                or (error_type and detail.final_error_type != error_type)
                or (layer and student_layer != layer)
            ):
                continue
            student = students.get(row.payload.student_id)
            if student:
                items.append(
                    {
                        "student_id": str(student.id),
                        "student_name": student.name,
                        "student_number": student.student_number,
                        "score": float(detail.score),
                        "max_score": float(detail.max_score),
                        "score_rate": float(detail.score / detail.max_score),
                        "final_error_type": detail.final_error_type,
                        "final_feedback_summary": (detail.final_feedback or "")[:160],
                        "teacher_review_id": str(detail.teacher_review_id),
                        "score_snapshot_id": str(row.snapshot.id),
                        "layer": student_layer,
                    }
                )
    items.sort(key=lambda x: (x["student_number"], x["score_snapshot_id"]))
    return page_result(items, page, page_size)


@router.get("/analytics/{snapshot_id}/knowledge-points/{knowledge_point_id}")
def knowledge_point_drilldown(
    snapshot_id: uuid.UUID,
    knowledge_point_id: uuid.UUID,
    db: Db,
    actor: Actor,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    snapshot = owned_analytics(db, actor.id, snapshot_id)
    point = db.get(KnowledgePoint, knowledge_point_id)
    if point is None:
        raise ApiProblem(404, "KNOWLEDGE_POINT_TREND_NOT_FOUND", "知识点不存在")
    students = {x.id: x for x in db.scalars(select(Student).where(Student.owner_id == actor.id))}
    items: list[dict[str, Any]] = []
    questions: dict[str, dict[str, Any]] = {}
    for row in released_rows(db, snapshot):
        matched = [d for d in row.payload.details if knowledge_point_id in d.knowledge_point_ids]
        if not matched:
            continue
        score, maximum = sum((d.score for d in matched), 0), sum((d.max_score for d in matched), 0)
        student = students.get(row.payload.student_id)
        if student:
            items.append(
                {
                    "student_id": str(student.id),
                    "student_name": student.name,
                    "student_number": student.student_number,
                    "score": float(score),
                    "max_score": float(maximum),
                    "score_rate": float(score / maximum),
                }
            )
        for d in matched:
            q = questions.setdefault(
                str(d.question_id),
                {
                    "question_id": str(d.question_id),
                    "question_number": d.question_number,
                    "score": 0.0,
                    "max_score": 0.0,
                },
            )
            q["score"] += float(d.score)
            q["max_score"] += float(d.max_score)
    for q in questions.values():
        q["score_rate"] = q["score"] / q["max_score"]
    result = page_result(sorted(items, key=lambda x: x["student_number"]), page, page_size)
    result.update(
        {
            "knowledge_point_id": str(point.id),
            "knowledge_point_name": point.name,
            "questions": list(questions.values()),
            "grade_release_id": str(snapshot.grade_release_id),
            "scoring_rule": "一题关联多个知识点时，该题完整得分与满分分别计入每个知识点。",
            "sample_warning": len(items) < 5,
        }
    )
    return result


@router.get("/analytics/{snapshot_id}/errors/{error_type}")
def error_drilldown(
    snapshot_id: uuid.UUID,
    error_type: str,
    db: Db,
    actor: Actor,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    snapshot = owned_analytics(db, actor.id, snapshot_id)
    students = {x.id: x for x in db.scalars(select(Student).where(Student.owner_id == actor.id))}
    revision_reviews = set(db.scalars(select(ScoreRevision.teacher_review_id)))
    items = []
    for row in released_rows(db, snapshot):
        for detail in row.payload.details:
            if detail.final_error_type == error_type and (
                student := students.get(row.payload.student_id)
            ):
                items.append(
                    {
                        "student_id": str(student.id),
                        "student_name": student.name,
                        "student_number": student.student_number,
                        "question_id": str(detail.question_id),
                        "question_number": detail.question_number,
                        "final_error_type": error_type,
                        "score": float(detail.score),
                        "max_score": float(detail.max_score),
                        "teacher_review_id": str(detail.teacher_review_id),
                        "score_revision_status": "revised"
                        if detail.teacher_review_id in revision_reviews
                        else "unchanged",
                        "grade_release_id": str(snapshot.grade_release_id),
                    }
                )
    items.sort(key=lambda x: (x["student_number"], x["question_number"]))
    return page_result(items, page, page_size)


def latest_release_snapshots(
    db: Session,
    owner_id: uuid.UUID,
    *,
    class_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
) -> list[AnalyticsSnapshot]:
    query = (
        select(AnalyticsSnapshot)
        .join(GradeRelease, GradeRelease.id == AnalyticsSnapshot.grade_release_id)
        .where(
            AnalyticsSnapshot.owner_id == owner_id,
            AnalyticsSnapshot.status == "complete",
            GradeRelease.status == "released",
        )
    )
    if class_id:
        query = query.where(AnalyticsSnapshot.class_id == class_id)
    snapshots = db.scalars(query).all()
    latest: dict[uuid.UUID, AnalyticsSnapshot] = {}
    release_order: dict[uuid.UUID, tuple[datetime, int, datetime, str]] = {}
    for snapshot in snapshots:
        release = db.get(GradeRelease, snapshot.grade_release_id)
        if release is None:
            continue
        released_at = release.released_at or release.created_at
        order = (released_at, release.version, snapshot.generated_at, str(snapshot.id))
        current_order = release_order.get(snapshot.assignment_id)
        if current_order is None or order > current_order:
            latest[snapshot.assignment_id] = snapshot
            release_order[snapshot.assignment_id] = order
    if student_id is not None:
        latest = {
            key: value
            for key, value in latest.items()
            if any(row.payload.student_id == student_id for row in released_rows(db, value))
        }
    return sorted(
        latest.values(),
        key=lambda snapshot: release_order[snapshot.assignment_id],
    )


@router.get("/classes/{class_id}/analytics/trends")
def class_trends(
    class_id: uuid.UUID,
    db: Db,
    actor: Actor,
    page: int = 1,
    page_size: int = 50,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    if (
        db.scalar(
            select(SchoolClass.id).where(
                SchoolClass.id == class_id, SchoolClass.owner_id == actor.id
            )
        )
        is None
    ):
        raise ApiProblem(404, "ANALYTICS_TREND_NOT_AVAILABLE", "班级趋势不存在")
    points = []
    previous = None
    for snapshot in latest_release_snapshots(db, actor.id, class_id=class_id):
        release, assignment = (
            db.get(GradeRelease, snapshot.grade_release_id),
            db.get(Assignment, snapshot.assignment_id),
        )
        released_at = release.released_at if release else None
        if (
            not release
            or not assignment
            or (date_from and released_at and released_at < date_from)
            or (date_to and released_at and released_at > date_to)
        ):
            continue
        rows = released_rows(db, snapshot)
        ratios = [float(x.payload.total_score / x.payload.max_score) for x in rows]
        active = (
            db.scalar(
                select(func.count())
                .select_from(ClassStudent)
                .where(ClassStudent.class_id == class_id, ClassStudent.status == "active")
            )
            or 0
        )
        sample_changed = previous is not None and previous != len(rows)
        previous = len(rows)
        points.append(
            {
                "analytics_snapshot_id": str(snapshot.id),
                "assignment_id": str(assignment.id),
                "assignment_name": assignment.title,
                "grade_release_id": str(release.id),
                "released_at": released_at,
                "participant_count": len(rows),
                "average_score_rate": statistics.fmean(ratios),
                "median_score_rate": statistics.median(ratios),
                "highest_score_rate": max(ratios),
                "lowest_score_rate": min(ratios),
                "incomplete_count": max(0, active - len(rows)),
                "sample_changed": sample_changed,
            }
        )
    return page_result(points, page, page_size)


@router.get("/students/{student_id}/analytics/trends")
def student_trends(
    student_id: uuid.UUID, db: Db, actor: Actor, page: int = 1, page_size: int = 50
) -> dict[str, Any]:
    student = db.scalar(
        select(Student).where(Student.id == student_id, Student.owner_id == actor.id)
    )
    if student is None:
        raise ApiProblem(404, "STUDENT_ANALYTICS_NOT_FOUND", "学生分析不存在")
    points = []
    for snapshot in latest_release_snapshots(db, actor.id, student_id=student_id):
        release, assignment, school_class = (
            db.get(GradeRelease, snapshot.grade_release_id),
            db.get(Assignment, snapshot.assignment_id),
            db.get(SchoolClass, snapshot.class_id),
        )
        row = next(
            (x for x in released_rows(db, snapshot) if x.payload.student_id == student_id), None
        )
        if row and release and assignment:
            errors = sum(bool(x.final_error_type) for x in row.payload.details)
            points.append(
                {
                    "student_id": str(student.id),
                    "class_id": str(snapshot.class_id),
                    "class_name": school_class.name if school_class else None,
                    "assignment_id": str(assignment.id),
                    "assignment_name": assignment.title,
                    "grade_release_id": str(release.id),
                    "total_score": float(row.payload.total_score),
                    "max_score": float(row.payload.max_score),
                    "score_rate": float(row.payload.total_score / row.payload.max_score),
                    "released_at": release.released_at,
                    "final_error_type_count": errors,
                    "score_snapshot_id": str(row.snapshot.id),
                }
            )
    return page_result(points, page, page_size)


@router.get("/students/{student_id}/knowledge-points/{knowledge_point_id}/trend")
def student_knowledge_trend(
    student_id: uuid.UUID, knowledge_point_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    if (
        db.scalar(select(Student.id).where(Student.id == student_id, Student.owner_id == actor.id))
        is None
    ):
        raise ApiProblem(404, "STUDENT_ANALYTICS_NOT_FOUND", "学生分析不存在")
    point = db.get(KnowledgePoint, knowledge_point_id)
    if point is None:
        raise ApiProblem(404, "KNOWLEDGE_POINT_TREND_NOT_FOUND", "知识点趋势不存在")
    points = []
    for snapshot in latest_release_snapshots(db, actor.id, student_id=student_id):
        release, assignment = (
            db.get(GradeRelease, snapshot.grade_release_id),
            db.get(Assignment, snapshot.assignment_id),
        )
        row = next(
            (x for x in released_rows(db, snapshot) if x.payload.student_id == student_id), None
        )
        details = (
            [d for d in row.payload.details if knowledge_point_id in d.knowledge_point_ids]
            if row
            else []
        )
        if details and release and assignment:
            score, maximum = (
                sum((d.score for d in details), 0),
                sum((d.max_score for d in details), 0),
            )
            points.append(
                {
                    "knowledge_point_id": str(point.id),
                    "knowledge_point_name": point.name,
                    "assignment_id": str(assignment.id),
                    "assignment_name": assignment.title,
                    "grade_release_id": str(release.id),
                    "question_ids": [str(d.question_id) for d in details],
                    "participant_count": 1,
                    "score": float(score),
                    "max_score": float(maximum),
                    "mastery_rate": float(score / maximum),
                    "released_at": release.released_at,
                    "sample_warning": True,
                }
            )
    return {"items": points, "scoring_rule": "一题多知识点时完整计入每个知识点；缺失作业不记零。"}


@router.get("/classes/{class_id}/knowledge-points/{knowledge_point_id}/trend")
def class_knowledge_trend(
    class_id: uuid.UUID, knowledge_point_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    if (
        db.scalar(
            select(SchoolClass.id).where(
                SchoolClass.id == class_id, SchoolClass.owner_id == actor.id
            )
        )
        is None
    ):
        raise ApiProblem(404, "KNOWLEDGE_POINT_TREND_NOT_FOUND", "知识点趋势不存在")
    point = db.get(KnowledgePoint, knowledge_point_id)
    if point is None:
        raise ApiProblem(404, "KNOWLEDGE_POINT_TREND_NOT_FOUND", "知识点趋势不存在")
    points = []
    for snapshot in latest_release_snapshots(db, actor.id, class_id=class_id):
        release, assignment = (
            db.get(GradeRelease, snapshot.grade_release_id),
            db.get(Assignment, snapshot.assignment_id),
        )
        rows = released_rows(db, snapshot)
        matched = [
            d
            for row in rows
            for d in row.payload.details
            if knowledge_point_id in d.knowledge_point_ids
        ]
        if matched and release and assignment:
            score, maximum = (
                sum((d.score for d in matched), 0),
                sum((d.max_score for d in matched), 0),
            )
            participants = sum(
                any(knowledge_point_id in d.knowledge_point_ids for d in row.payload.details)
                for row in rows
            )
            points.append(
                {
                    "knowledge_point_id": str(point.id),
                    "knowledge_point_name": point.name,
                    "assignment_id": str(assignment.id),
                    "assignment_name": assignment.title,
                    "grade_release_id": str(release.id),
                    "question_ids": sorted({str(d.question_id) for d in matched}),
                    "participant_count": participants,
                    "score": float(score),
                    "max_score": float(maximum),
                    "mastery_rate": float(score / maximum),
                    "released_at": release.released_at,
                    "sample_warning": participants < 5,
                }
            )
    return {
        "items": points,
        "scoring_rule": "只按相同 KnowledgePoint ID 比较；一题多知识点时完整计入每个知识点。",
    }


@router.get("/students/{student_id}/analytics")
def student_analytics(student_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    student = db.scalar(
        select(Student).where(Student.id == student_id, Student.owner_id == actor.id)
    )
    if student is None:
        raise ApiProblem(404, "STUDENT_ANALYTICS_NOT_FOUND", "学生分析不存在")
    trends = student_trends(student_id, db, actor, 1, 100)["items"]
    current = trends[-1] if trends else None
    questions, revisions, comments = [], [], []
    if current:
        snapshot = db.get(SubmissionScoreSnapshot, uuid.UUID(current["score_snapshot_id"]))
        submission = db.get(Submission, snapshot.submission_id) if snapshot else None
        row = (
            FinalScoreService(db, actor.id).validate(snapshot, submission)
            if snapshot and submission
            else None
        )
        if row:
            kp_ids = {kp for detail in row.payload.details for kp in detail.knowledge_point_ids}
            kp_names = {
                str(x.id): x.name
                for x in db.scalars(select(KnowledgePoint).where(KnowledgePoint.id.in_(kp_ids)))
            }
            questions = [
                {
                    "question_id": str(d.question_id),
                    "question_number": d.question_number,
                    "question_type": d.question_type,
                    "score": float(d.score),
                    "max_score": float(d.max_score),
                    "final_error_type": d.final_error_type,
                    "final_feedback": d.final_feedback,
                    "knowledge_points": [
                        {"id": str(k), "name": kp_names.get(str(k), str(k))}
                        for k in d.knowledge_point_ids
                    ],
                    "teacher_review_id": str(d.teacher_review_id),
                }
                for d in row.payload.details
            ]
            comments = [
                {"question_number": q["question_number"], "feedback": q["final_feedback"]}
                for q in questions
                if q["final_feedback"]
            ]
    history_review_ids: list[uuid.UUID] = []
    for history in trends:
        history_snapshot = db.get(SubmissionScoreSnapshot, uuid.UUID(history["score_snapshot_id"]))
        history_submission = (
            db.get(Submission, history_snapshot.submission_id) if history_snapshot else None
        )
        if history_snapshot and history_submission:
            history_row = FinalScoreService(db, actor.id).validate(
                history_snapshot, history_submission
            )
            history_review_ids.extend(d.teacher_review_id for d in history_row.payload.details)
    revisions = [
        {
            "id": str(x.id),
            "teacher_review_id": str(x.teacher_review_id),
            "previous_score": float(x.previous_score) if x.previous_score is not None else None,
            "new_score": float(x.new_score) if x.new_score is not None else None,
            "reason": x.reason,
            "actor_id": str(x.actor_id),
            "created_at": x.created_at,
        }
        for x in db.scalars(
            select(ScoreRevision)
            .where(ScoreRevision.teacher_review_id.in_(history_review_ids))
            .order_by(ScoreRevision.created_at.desc())
        )
    ]
    memberships = db.execute(
        select(ClassStudent, SchoolClass)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(ClassStudent.student_id == student.id, SchoolClass.owner_id == actor.id)
        .order_by(ClassStudent.joined_at.desc())
    ).all()
    return {
        "student": {
            "id": str(student.id),
            "name": student.name,
            "student_number": student.student_number,
            "status": str(student.status),
            "current_class": memberships[0][1].name if memberships else None,
        },
        "current": current,
        "history": trends,
        "questions": questions,
        "teacher_comments": comments,
        "score_revisions": revisions,
        "report_jobs": student_report_jobs(student_id, db, actor),
    }


@router.get("/students/{student_id}/report-jobs")
def student_report_jobs(student_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    if (
        db.scalar(select(Student.id).where(Student.id == student_id, Student.owner_id == actor.id))
        is None
    ):
        raise ApiProblem(404, "STUDENT_ANALYTICS_NOT_FOUND", "学生不存在")
    jobs = db.scalars(
        select(ReportJob)
        .join(ReportJobStudentScope, ReportJobStudentScope.report_job_id == ReportJob.id)
        .where(ReportJob.owner_id == actor.id, ReportJobStudentScope.student_id == student_id)
        .order_by(ReportJob.created_at.desc())
    ).all()
    return [
        {
            **report_view(db, job),
            "grade_release_id": str(job.grade_release_id),
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "expires_at": job.expires_at,
            "download_available": job.status in {"completed", "partially_completed"}
            and job.expires_at is not None
            and job.expires_at > now_utc(),
        }
        for job in jobs
    ]


def owned_insight(db: Session, actor_id: uuid.UUID, insight_id: uuid.UUID) -> TeachingInsight:
    insight = db.scalar(
        select(TeachingInsight).where(
            TeachingInsight.id == insight_id, TeachingInsight.owner_id == actor_id
        )
    )
    if insight is None:
        raise ApiProblem(404, "TEACHING_INSIGHT_NOT_FOUND", "教学建议不存在")
    return insight


def insight_view(insight: TeachingInsight) -> dict[str, Any]:
    return {
        "id": str(insight.id),
        "analytics_snapshot_id": str(insight.analytics_snapshot_id),
        "provider": insight.provider,
        "provider_label": "规则型教学建议",
        "status": insight.status,
        "content": insight.content,
        "evidence": insight.evidence,
        "created_at": insight.created_at,
        "updated_at": insight.updated_at,
    }


@router.get("/teaching-insights/{insight_id}")
def get_insight(insight_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return insight_view(owned_insight(db, actor.id, insight_id))


@router.patch("/teaching-insights/{insight_id}")
def edit_insight(insight_id: uuid.UUID, data: InsightEdit, db: Db, actor: Actor) -> dict[str, Any]:
    insight = owned_insight(db, actor.id, insight_id)
    if insight.status == "confirmed":
        raise ApiProblem(409, "TEACHING_INSIGHT_ALREADY_CONFIRMED", "已确认建议不可静默修改")
    if insight.status in {"stale", "superseded", "invalid"}:
        raise ApiProblem(409, "TEACHING_INSIGHT_STALE", "过期建议不可编辑")
    content = dict(insight.content)
    history = list(content.get("edit_history", []))
    history.append(
        {
            "recommendations": content.get("recommendations", []),
            "actor_id": str(actor.id),
            "edited_at": now_utc().isoformat(),
        }
    )
    content.setdefault("original_recommendations", content.get("recommendations", []))
    content["recommendations"], content["edit_history"] = data.recommendations, history
    insight.content, insight.status = content, "draft"
    audit(
        db,
        actor.id,
        "teaching_insight.edit",
        "teaching_insight",
        insight.id,
        {"recommendation_count": len(data.recommendations)},
    )
    db.commit()
    db.refresh(insight)
    return insight_view(insight)


@router.post("/teaching-insights/{insight_id}/confirm")
def confirm_insight(insight_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    insight = owned_insight(db, actor.id, insight_id)
    if insight.status == "confirmed":
        raise ApiProblem(409, "TEACHING_INSIGHT_ALREADY_CONFIRMED", "教学建议已确认")
    if insight.status in {"stale", "superseded", "invalid"}:
        raise ApiProblem(409, "TEACHING_INSIGHT_STALE", "过期建议不可确认")
    snapshot = owned_analytics(db, actor.id, insight.analytics_snapshot_id)
    question_metrics = {str(x["question_id"]): x for x in snapshot.metrics.get("questions", [])}
    kp_metrics = {
        str(x["knowledge_point_id"]): x for x in snapshot.metrics.get("knowledge_points", [])
    }
    for evidence in insight.evidence:
        question = question_metrics.get(str(evidence.get("question_id")))
        point = (
            kp_metrics.get(str(evidence.get("knowledge_point_id")))
            if evidence.get("knowledge_point_id")
            else None
        )
        if question is None and point is None:
            raise ApiProblem(422, "TEACHING_INSIGHT_EVIDENCE_INVALID", "建议证据不属于固定分析快照")
        source = question or point or {}
        expected = source.get("score_rate", source.get("mastery_rate"))
        if evidence.get("value") != expected or evidence.get("participants") != source.get(
            "participants", source.get("sample_count")
        ):
            raise ApiProblem(
                422, "TEACHING_INSIGHT_EVIDENCE_INVALID", "建议证据数值与分析快照不一致"
            )
    content = dict(insight.content)
    content["confirmed_by"] = str(actor.id)
    content["confirmed_at"] = now_utc().isoformat()
    insight.content, insight.status = content, "confirmed"
    audit(db, actor.id, "teaching_insight.confirm", "teaching_insight", insight.id, {})
    db.commit()
    db.refresh(insight)
    return insight_view(insight)


@router.post("/teaching-insights/{insight_id}/regenerate", status_code=201)
def regenerate_insight(insight_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    old = owned_insight(db, actor.id, insight_id)
    old.status = "superseded"
    result = generate_insight(old.analytics_snapshot_id, db, actor)
    audit(
        db,
        actor.id,
        "teaching_insight.regenerate",
        "teaching_insight",
        old.id,
        {"replacement_id": result["id"]},
    )
    db.commit()
    return result


@router.post("/teaching-insights/{insight_id}/invalidate")
def invalidate_insight(insight_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    insight = owned_insight(db, actor.id, insight_id)
    insight.status = "invalid"
    audit(db, actor.id, "teaching_insight.invalidate", "teaching_insight", insight.id, {})
    db.commit()
    db.refresh(insight)
    return insight_view(insight)
