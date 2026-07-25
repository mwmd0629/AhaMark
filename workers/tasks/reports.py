import uuid
from typing import Any

from app.db.session import SessionLocal
from app.results.jobs import run_report_job
from app.storage.dependencies import get_storage

from workers.celery_app import celery_app
from workers.task_context import run_traced_task


@celery_app.task(name="ahamark.report.run", bind=True, acks_late=True, reject_on_worker_lost=True)
def run_report(task: Any, job_id: str) -> None:
    def operation() -> None:
        with SessionLocal() as db:
            delivery = task.request.delivery_info or {}
            run_report_job(
                db,
                get_storage(),
                uuid.UUID(job_id),
                allow_running_resume=bool(delivery.get("redelivered")),
            )

    run_traced_task(task, job_id, operation)
