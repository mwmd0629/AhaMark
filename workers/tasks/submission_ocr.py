import uuid
from typing import Any

from app.db.session import SessionLocal
from app.recognition.submission import run_submission_recognition_job
from app.storage.dependencies import get_storage

from workers.celery_app import celery_app
from workers.task_context import run_traced_task


@celery_app.task(
    name="ahamark.submission_recognition.run",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_submission_recognition(task: Any, job_id: str, page_id: str | None = None) -> None:
    def operation() -> None:
        with SessionLocal() as db:
            run_submission_recognition_job(
                db, get_storage(), uuid.UUID(job_id), uuid.UUID(page_id) if page_id else None
            )

    run_traced_task(task, job_id, operation)
