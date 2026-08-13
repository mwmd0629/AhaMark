from app.core.config import get_settings
from app.core.logging import configure_logging
from celery import Celery

s = get_settings()
configure_logging(s.log_level)
celery_app = Celery(
    "ahamark",
    broker=s.celery_broker_url,
    backend=s.celery_result_backend,
    include=[
        "workers.tasks.demo",
        "workers.tasks.ocr",
        "workers.tasks.reports",
        "workers.tasks.submission_ocr",
        "workers.tasks.submission_processing",
        "workers.tasks.answer_recognition",
        "workers.tasks.math_validation",
        "workers.tasks.ai_grading",
        "workers.tasks.wrong_question_ai",
        "workers.tasks.student_learning_analysis",
        "workers.tasks.assignment_generation",
    ],
)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=1800,
    task_soft_time_limit=1740,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    broker_transport_options={"visibility_timeout": s.celery_visibility_timeout},
)
