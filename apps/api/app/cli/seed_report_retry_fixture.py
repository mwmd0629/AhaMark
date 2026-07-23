"""Create synthetic terminal ReportJob fixtures for the isolated E2E stack only."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import (
    Assignment,
    FileStatus,
    GradeRelease,
    ReportJob,
    ReportJobStudentScope,
    SchoolClass,
    StoredFile,
    Student,
    User,
    now_utc,
)


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> None:
    settings = get_settings()
    if settings.app_env != "test":
        raise SystemExit("report retry fixtures require APP_ENV=test")
    email = required("REPORT_RETRY_TEACHER_EMAIL")
    if not email.endswith(".synthetic.invalid"):
        raise SystemExit("REPORT_RETRY_TEACHER_EMAIL must use .synthetic.invalid")
    release_id = uuid.UUID(required("REPORT_RETRY_RELEASE_ID"))
    student_id = uuid.UUID(required("REPORT_RETRY_STUDENT_ID"))
    run_id = required("REPORT_RETRY_RUN_ID")
    if not run_id.startswith("report-retry-"):
        raise SystemExit("REPORT_RETRY_RUN_ID must use the report-retry namespace")

    with SessionLocal() as db:
        teacher = db.scalar(select(User).where(User.email == email))
        release = db.get(GradeRelease, release_id)
        student = db.get(Student, student_id)
        if teacher is None or release is None or student is None:
            raise SystemExit("fixture references missing teacher, release, or student")
        assignment = db.get(Assignment, release.assignment_id)
        school_class = db.get(SchoolClass, release.class_id)
        if (
            assignment is None
            or school_class is None
            or teacher.id != release.owner_id
            or student.owner_id != teacher.id
            or assignment.owner_id != teacher.id
            or school_class.owner_id != teacher.id
            or release.status != "released"
        ):
            raise SystemExit("fixture references cross-owner or inactive business objects")

        stored = StoredFile(
            owner_id=teacher.id,
            storage_key=f"{run_id}/expired-report.pdf",
            original_name=f"{run_id}-expired-report.pdf",
            content_type="application/pdf",
            size=1,
            checksum="0" * 64,
            status=FileStatus.ready,
        )
        db.add(stored)
        db.flush()
        failed = ReportJob(
            owner_id=teacher.id,
            assignment_id=assignment.id,
            class_id=school_class.id,
            grade_release_id=release.id,
            report_type="student_report_pdf",
            status="failed",
            progress=0,
            error_code="SYNTHETIC_PROVIDER_FAILURE",
            error_message="synthetic report retry fixture",
            idempotency_key=f"{run_id}:failed",
        )
        expired = ReportJob(
            owner_id=teacher.id,
            assignment_id=assignment.id,
            class_id=school_class.id,
            grade_release_id=release.id,
            report_type="student_report_pdf",
            status="completed",
            progress=100,
            stored_file_id=stored.id,
            idempotency_key=f"{run_id}:expired",
            completed_at=now_utc() - timedelta(hours=2),
            expires_at=now_utc() - timedelta(minutes=1),
        )
        db.add_all([failed, expired])
        db.flush()
        db.add_all(
            [
                ReportJobStudentScope(report_job_id=failed.id, student_id=student.id),
                ReportJobStudentScope(report_job_id=expired.id, student_id=student.id),
            ]
        )
        db.commit()
        print(
            f"report-retry fixture ready: failed={failed.id} expired={expired.id} "
            f"release={release.id} student={student.id}"
        )


if __name__ == "__main__":
    main()
