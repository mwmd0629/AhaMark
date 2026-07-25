import time
from collections.abc import Callable
from typing import Any, TypeVar

import structlog
from app.core.request_id import request_id_from_task_headers

Result = TypeVar("Result")
log = structlog.get_logger()


def run_traced_task(
    task: Any,
    job_id: str,
    operation: Callable[[], Result],
) -> Result:
    request = task.request
    request_id = request_id_from_task_headers(getattr(request, "headers", None))
    task_id = str(getattr(request, "id", "") or "unknown")
    task_name = str(getattr(request, "task", "") or getattr(task, "name", "") or "unknown")
    started = time.monotonic()
    context = {
        "service": "worker",
        "request_id": request_id,
        "task_id": task_id,
        "job_id": job_id,
        "task_name": task_name,
    }
    log.info("worker_task_started", **context, status="started")
    try:
        result = operation()
    except Exception:
        log.exception(
            "worker_task_finished",
            **context,
            status="failed",
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    log.info(
        "worker_task_finished",
        **context,
        status="completed",
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return result
