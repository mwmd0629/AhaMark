import uuid
from typing import Any

from app.api.recognition import (
    _claim_recognition_attempt,
    _mark_recognition_failed,
    run_recognition_job,
)
from app.db.session import SessionLocal
from app.storage.dependencies import get_storage

from workers.celery_app import celery_app
from workers.task_context import run_traced_task


@celery_app.task(
    name="ahamark.recognition.run", bind=True, acks_late=True, reject_on_worker_lost=True
)
def run_recognition(task: Any, job_id: str) -> None:
    def operation() -> None:
        with SessionLocal() as db:
            parsed_id = uuid.UUID(job_id)
            delivery = task.request.delivery_info or {}
            claim = _claim_recognition_attempt(
                db,
                parsed_id,
                allow_running_resume=bool(delivery.get("redelivered")),
            )
            if claim is None:
                return
            try:
                storage = get_storage()
                run_recognition_job(
                    db,
                    storage,
                    parsed_id,
                    claimed_attempt=claim,
                )
            except Exception:
                _mark_recognition_failed(
                    db,
                    parsed_id,
                    claim.attempt,
                    "RECOGNITION_FAILED",
                    "Worker encountered an unexpected recognition error",
                )
                raise

    run_traced_task(task, job_id, operation)
