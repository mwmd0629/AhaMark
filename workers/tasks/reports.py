import uuid

from app.db.session import SessionLocal
from app.results.jobs import run_report_job
from app.storage.dependencies import get_storage

from workers.celery_app import celery_app


@celery_app.task(name="ahamark.report.run", acks_late=True, reject_on_worker_lost=True)
def run_report(job_id: str) -> None:
    with SessionLocal() as db:
        run_report_job(db, get_storage(), uuid.UUID(job_id))
