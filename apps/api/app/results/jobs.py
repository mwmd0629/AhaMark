import hashlib
import io
import uuid
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    FileStatus,
    GradeRelease,
    ReportJob,
    ReportJobStudentScope,
    StoredFile,
    Student,
    now_utc,
)
from app.results.services import gradebook_xlsx, release_scores, student_report_pdf
from app.storage.base import ObjectStorage


def run_report_job(db: Session, storage: ObjectStorage, job_id: uuid.UUID) -> None:
    job = db.get(ReportJob, job_id)
    if job is None or job.status in {"completed", "partially_completed"}:
        return
    if job.status == "running":
        return
    job.status, job.started_at, job.progress = "running", now_utc(), 5
    db.commit()
    try:
        release = db.get(GradeRelease, job.grade_release_id)
        if release is None:
            raise ValueError("GradeRelease 不存在")
        font_path = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansSC-VF.ttf"
        if job.report_type == "gradebook_xlsx":
            content = gradebook_xlsx(db, release)
            filename = f"gradebook-v{release.version}.xlsx"
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif job.report_type == "student_report_pdf":
            scope = db.get(ReportJobStudentScope, job.id)
            if scope is None:
                raise ValueError("个人 PDF 报告必须指定学生")
            content = student_report_pdf(db, release, scope.student_id, font_path)
            filename = f"student-report-v{release.version}.pdf"
            content_type = "application/pdf"
        elif job.report_type == "batch_student_reports":
            output = io.BytesIO()
            failures = 0
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for row in release_scores(db, release.id):
                    try:
                        pdf = student_report_pdf(db, release, row.payload.student_id, font_path)
                        student = db.get(Student, row.payload.student_id)
                        safe_number = "".join(
                            character
                            for character in (
                                student.student_number if student else str(row.payload.student_id)
                            )
                            if character.isalnum() or character in {"-", "_"}
                        )[:64]
                        archive.writestr(f"{safe_number or row.payload.student_id}.pdf", pdf)
                    except Exception:
                        failures += 1
            content = output.getvalue()
            filename = f"student-reports-v{release.version}.zip"
            content_type = "application/zip"
            if failures:
                job.status, job.error_code, job.error_message = (
                    "partially_completed",
                    "SOME_REPORTS_FAILED",
                    f"{failures} 份学生报告生成失败",
                )
        else:
            raise ValueError("不支持的报告类型")
        extension = Path(filename).suffix
        key = (
            f"reports/{job.owner_id}/{release.id}/"
            f"{job.report_type}-v{release.version}-{job.id}{extension}"
        )
        storage.put(
            key,
            io.BytesIO(content),
            len(content),
            content_type,
        )
        stored = StoredFile(
            owner_id=job.owner_id,
            storage_key=key,
            original_name=filename,
            content_type=content_type,
            size=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
            status=FileStatus.ready,
        )
        db.add(stored)
        db.flush()
        job.stored_file_id, job.status, job.progress, job.completed_at = (
            stored.id,
            "partially_completed" if job.status == "partially_completed" else "completed",
            100,
            now_utc(),
        )
        if job.status == "completed":
            job.error_code = job.error_message = None
    except Exception as exc:
        job.status, job.error_code, job.error_message = (
            "failed",
            "REPORT_GENERATION_FAILED",
            type(exc).__name__,
        )
    db.add(
        AuditLog(
            actor_id=job.owner_id,
            action="report.worker.complete",
            resource_type="report_job",
            resource_id=str(job.id),
            metadata_={"type": job.report_type, "status": job.status},
        )
    )
    db.commit()
