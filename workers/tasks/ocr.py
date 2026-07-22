import uuid

from app.api.recognition import run_recognition_job
from app.db.session import SessionLocal
from app.storage.dependencies import get_storage

from workers.celery_app import celery_app


@celery_app.task(name="ahamark.recognition.run", acks_late=True, reject_on_worker_lost=True)
def run_recognition(job_id: str) -> None:
    with SessionLocal() as db:
        run_recognition_job(db, get_storage(), uuid.UUID(job_id))
