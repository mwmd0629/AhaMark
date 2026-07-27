import uuid
from typing import Any

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.recognition.submission_processing import run_submission_processing
from app.storage.dependencies import get_storage

from workers.celery_app import celery_app
from workers.task_context import run_traced_task


@celery_app.task(
    name="ahamark.submission_processing.run",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_submission_pages(task: Any, job_id: str, page_id: str | None = None) -> None:
    def operation() -> None:
        with SessionLocal() as db:
            run_submission_processing(
                db,
                get_storage(),
                get_settings(),
                uuid.UUID(job_id),
                uuid.UUID(page_id) if page_id else None,
            )

    run_traced_task(task, job_id, operation)
