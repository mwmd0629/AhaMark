"""Synthetic, guarded 7C business-path probe for one isolated recovery run."""

import argparse
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from minio import Minio
from sqlalchemy import func, select

from app.api.actor import digest
from app.cli.reconcile_recovery import (
    derived_storage_references,
    stable_digest,
    stable_table_rows,
)
from app.cli.recovery_v7_guard import require_recovery_environment
from app.cli.seed_capacity_demo import uid
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import (
    AnalyticsSnapshot,
    PageProcessingResult,
    QuestionCandidate,
    RecognitionBlock,
    RecognitionJob,
    RecognitionStatus,
    ReportJob,
    ReportJobStudentScope,
    StoredFile,
    TeachingInsight,
    UserSession,
    now_utc,
)
from workers.celery_app import celery_app

BASE_URL = "http://localhost:8000"


def structured_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, default=str, sort_keys=True)


def immutable_grade_hash(db: Any) -> str:
    return stable_digest(
        {
            table: stable_table_rows(db, table)
            for table in (
                "grade_releases",
                "grade_release_items",
                "submission_score_snapshots",
            )
        }
    )


def teaching_insight_bindings_hash(db: Any) -> str:
    rows = list(
        db.execute(
            select(TeachingInsight.id, TeachingInsight.analytics_snapshot_id).order_by(
                TeachingInsight.id
            )
        ).all()
    )
    return stable_digest(rows)


def decode_response(status_code: int, content: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        payload = {}
    return {"status_code": status_code, "payload": payload}


def api_auth() -> dict[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with SessionLocal.begin() as db:
        db.add(
            UserSession(
                user_id=uid("teacher-1"),
                token_hash=digest(token),
                csrf_hash=digest(csrf),
                expires_at=now_utc() + timedelta(minutes=30),
            )
        )
    return {
        "Cookie": f"{get_settings().auth_cookie_name}={token}; ahamark_csrf={csrf}",
        "x-csrf-token": csrf,
    }


def api_request(
    method: str,
    path: str,
    auth: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", **(auth or api_auth())}
    request = Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload or {}).encode() if method not in {"GET", "HEAD"} else None,
        headers=headers,
        method=method,
    )
    try:
        with build_opener().open(request, timeout=30) as response:
            return decode_response(response.status, response.read())
    except HTTPError as exc:
        return decode_response(exc.code, exc.read())


def concurrent_requests(
    method: str, path: str, *, count: int, concurrency: int
) -> list[dict[str, Any]]:
    auth = api_auth()

    def request_once(_: int) -> dict[str, Any]:
        return api_request(method, path, auth)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(request_once, range(count)))


def find_recognition(key: str) -> dict[str, Any]:
    with SessionLocal() as db:
        job = db.scalar(select(RecognitionJob).where(RecognitionJob.idempotency_key == key))
        if job is None:
            return {"found": False}
        page_count = db.scalar(
            select(func.count())
            .select_from(PageProcessingResult)
            .where(PageProcessingResult.recognition_job_id == job.id)
        )
        block_count = db.scalar(
            select(func.count())
            .select_from(RecognitionBlock)
            .where(RecognitionBlock.recognition_job_id == job.id)
        )
        candidate_count = db.scalar(
            select(func.count())
            .select_from(QuestionCandidate)
            .where(QuestionCandidate.recognition_job_id == job.id)
        )
        return {
            "found": True,
            "id": str(job.id),
            "status": str(job.status),
            "attempt": job.attempt,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "failed_at": job.failed_at,
            "error_code": job.error_code,
            "page_count": int(page_count or 0),
            "block_count": int(block_count or 0),
            "candidate_count": int(candidate_count or 0),
        }


def find_report(key: str) -> dict[str, Any]:
    with SessionLocal() as db:
        job = db.scalar(select(ReportJob).where(ReportJob.idempotency_key == key))
        if job is None:
            return {"found": False}
        scope = db.get(ReportJobStudentScope, job.id)
        return {
            "found": True,
            "id": str(job.id),
            "status": job.status,
            "stored_file_id": str(job.stored_file_id) if job.stored_file_id else None,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "grade_release_id": str(job.grade_release_id),
            "assignment_id": str(job.assignment_id),
            "class_id": str(job.class_id),
            "report_type": job.report_type,
            "student_id": str(scope.student_id) if scope else None,
        }


def dispatch_task(name: str, job_id: str, count: int, concurrency: int) -> dict[str, Any]:
    def send(_: int) -> None:
        celery_app.send_task(name, args=[job_id])

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(send, range(count)))
    return {"task": name, "job_id": job_id, "dispatched": count}


def duplicate_groups(db: Any, model: Any, columns: tuple[Any, ...]) -> int:
    query = select(*columns).select_from(model).group_by(*columns).having(func.count() > 1)
    return len(list(db.execute(query).all()))


def final_audit() -> dict[str, Any]:
    identity = require_recovery_environment()
    settings = get_settings()
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    objects = {
        item.object_name
        for item in client.list_objects(settings.minio_bucket, recursive=True)
        if item.object_name
    }
    with SessionLocal() as db:
        stored = list(db.scalars(select(StoredFile)))
        stored_keys = {row.storage_key for row in stored}
        derived = set(derived_storage_references(db))
        recognition_queued = db.scalar(
            select(func.count())
            .select_from(RecognitionJob)
            .where(RecognitionJob.status == RecognitionStatus.queued)
        )
        recognition_running = db.scalar(
            select(func.count())
            .select_from(RecognitionJob)
            .where(RecognitionJob.status == RecognitionStatus.running)
        )
        report_queued = db.scalar(
            select(func.count()).select_from(ReportJob).where(ReportJob.status == "queued")
        )
        report_running = db.scalar(
            select(func.count()).select_from(ReportJob).where(ReportJob.status == "running")
        )
        report_key_duplicates = duplicate_groups(db, ReportJob, (ReportJob.idempotency_key,))
        analytics_duplicates = duplicate_groups(
            db,
            AnalyticsSnapshot,
            (
                AnalyticsSnapshot.owner_id,
                AnalyticsSnapshot.grade_release_id,
                AnalyticsSnapshot.schema_version,
                AnalyticsSnapshot.status,
            ),
        )
        page_duplicates = duplicate_groups(
            db,
            PageProcessingResult,
            (PageProcessingResult.recognition_job_id, PageProcessingResult.paper_page_id),
        )
        block_duplicates = duplicate_groups(
            db,
            RecognitionBlock,
            (
                RecognitionBlock.recognition_job_id,
                RecognitionBlock.paper_page_id,
                RecognitionBlock.display_order,
            ),
        )
        candidate_duplicates = duplicate_groups(
            db,
            QuestionCandidate,
            (QuestionCandidate.recognition_job_id, QuestionCandidate.temporary_number),
        )
        stored_reference_duplicates = len(
            list(
                db.execute(
                    select(ReportJob.stored_file_id)
                    .where(ReportJob.stored_file_id.is_not(None))
                    .group_by(ReportJob.stored_file_id)
                    .having(func.count() > 1)
                ).all()
            )
        )
        insight_mismatch = db.scalar(
            select(func.count())
            .select_from(TeachingInsight)
            .join(AnalyticsSnapshot, TeachingInsight.analytics_snapshot_id == AnalyticsSnapshot.id)
            .where(TeachingInsight.owner_id != AnalyticsSnapshot.owner_id)
        )
        immutable_hash = immutable_grade_hash(db)
        insight_bindings_hash = teaching_insight_bindings_hash(db)
    inspect = celery_app.control.inspect(timeout=5)
    active = inspect.active() or {}
    reserved = inspect.reserved() or {}
    return {
        "run_id": identity.run_id,
        "recognition_queued": int(recognition_queued or 0),
        "recognition_running": int(recognition_running or 0),
        "report_queued": int(report_queued or 0),
        "report_running": int(report_running or 0),
        "celery_active": sum(len(value) for value in active.values()),
        "celery_reserved": sum(len(value) for value in reserved.values()),
        "duplicate_report_idempotency_keys": report_key_duplicates,
        "duplicate_analytics_snapshots": analytics_duplicates,
        "duplicate_ocr_pages": page_duplicates,
        "duplicate_ocr_blocks": block_duplicates,
        "duplicate_candidates": candidate_duplicates,
        "duplicate_stored_file_report_references": stored_reference_duplicates,
        "teaching_insight_owner_mismatch": int(insight_mismatch or 0),
        "immutable_grade_hash": immutable_hash,
        "teaching_insight_bindings_hash": insight_bindings_hash,
        "database_records_missing_object": sorted(stored_keys - objects),
        "object_missing_database": sorted(objects - stored_keys - derived),
        "legitimate_derived_objects": sorted(objects & derived),
        "current_run_unknown_orphans": sorted(
            key
            for key in objects - stored_keys - derived
            if identity.run_id in key or key.startswith("reports/")
        ),
        "unable_to_classify": [],
        "source_objects": len(objects),
        "stored_files": len(stored_keys),
    }


def main() -> None:
    identity = require_recovery_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "context",
            "create-recognition",
            "retry-recognition",
            "get-recognition",
            "find-recognition",
            "dispatch-recognition",
            "create-report",
            "retry-report",
            "get-report",
            "find-report",
            "dispatch-report",
            "analytics",
            "final-audit",
        ),
    )
    parser.add_argument("--key")
    parser.add_argument("--job-id")
    parser.add_argument("--release", choices=("s1", "s2"), default="s1")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    assignment_id = uid("assignment-s1")
    paper_id = uid("paper-s1")
    release_id = uid("release-s1" if args.release == "s1" else "release-s2-t1-c1")

    if args.action == "context":
        with SessionLocal() as db:
            immutable_hash = immutable_grade_hash(db)
            insight_bindings_hash = teaching_insight_bindings_hash(db)
        result: dict[str, Any] = {
            "run_id": identity.run_id,
            "assignment_id": str(assignment_id),
            "paper_version_id": str(paper_id),
            "release_s1_id": str(uid("release-s1")),
            "release_s2_id": str(uid("release-s2-t1-c1")),
            "immutable_grade_hash": immutable_hash,
            "teaching_insight_bindings_hash": insight_bindings_hash,
        }
    elif args.action == "create-recognition":
        result = api_request(
            "POST",
            f"/api/assignments/{assignment_id}/recognition/jobs",
            payload={"paper_version_id": str(paper_id), "idempotency_key": args.key},
        )
    elif args.action == "retry-recognition":
        result = api_request(
            "POST",
            f"/api/assignments/{assignment_id}/recognition/jobs/{args.job_id}/retry",
        )
    elif args.action == "get-recognition":
        result = api_request(
            "GET", f"/api/assignments/{assignment_id}/recognition/jobs/{args.job_id}"
        )
    elif args.action == "find-recognition":
        result = find_recognition(str(args.key))
    elif args.action == "dispatch-recognition":
        result = dispatch_task(
            "ahamark.recognition.run", str(args.job_id), args.count, args.concurrency
        )
    elif args.action == "create-report":
        path = (
            f"/api/grade-releases/{release_id}/reports"
            f"?report_type=gradebook_xlsx&idempotency_key={args.key}"
        )
        responses = concurrent_requests(
            "POST", path, count=args.count, concurrency=args.concurrency
        )
        result = {"responses": responses}
    elif args.action == "retry-report":
        result = api_request("POST", f"/api/report-jobs/{args.job_id}/retry")
    elif args.action == "get-report":
        result = api_request("GET", f"/api/report-jobs/{args.job_id}")
    elif args.action == "find-report":
        result = find_report(str(args.key))
    elif args.action == "dispatch-report":
        result = dispatch_task("ahamark.report.run", str(args.job_id), args.count, args.concurrency)
    elif args.action == "analytics":
        responses = concurrent_requests(
            "POST",
            f"/api/grade-releases/{release_id}/analytics",
            count=args.count,
            concurrency=args.concurrency,
        )
        result = {"responses": responses}
    else:
        result = final_audit()
    print(structured_json(result))


if __name__ == "__main__":
    main()
