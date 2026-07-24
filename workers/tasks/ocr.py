import uuid
from typing import Any

from app.api.recognition import run_recognition_job
from app.db.session import SessionLocal
from app.models import RecognitionJob, RecognitionStatus, now_utc
from app.storage.dependencies import get_storage

from workers.celery_app import celery_app


@celery_app.task(
    name="ahamark.recognition.run", bind=True, acks_late=True, reject_on_worker_lost=True
)
def run_recognition(task: Any, job_id: str) -> None:
    with SessionLocal() as db:
        parsed_id = uuid.UUID(job_id)
        try:
            delivery = task.request.delivery_info or {}
            run_recognition_job(
                db,
                get_storage(),
                parsed_id,
                allow_running_resume=bool(delivery.get("redelivered")),
            )
        except Exception:
            db.rollback()
            job = db.get(RecognitionJob, parsed_id)
            if job is not None:
                job.status = RecognitionStatus.failed
                job.error_code = "RECOGNITION_FAILED"
                job.error_message = "Worker encountered an unexpected recognition error"
                job.failed_at = now_utc()
                db.commit()
            raise
