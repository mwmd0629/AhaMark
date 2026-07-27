import uuid
from typing import Any

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.recognition.answer_evidence import run_answer_evidence_phase
from app.storage.dependencies import get_storage

from workers.celery_app import celery_app
from workers.task_context import run_traced_task


@celery_app.task(
    name="ahamark.answer_recognition.run",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(),
    soft_time_limit=300,
    time_limit=330,
)
def run_answer_recognition(task: Any, job_id: str, region_id: str | None = None) -> None:
    def operation() -> None:
        with SessionLocal() as db:
            run_answer_evidence_phase(
                db,
                get_storage(),
                get_settings(),
                uuid.UUID(job_id),
                region_id=uuid.UUID(region_id) if region_id else None,
            )

    run_traced_task(task, job_id, operation)
