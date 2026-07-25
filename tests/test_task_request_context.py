import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from app.core.request_id import (
    bind_request_id,
    celery_request_headers,
    request_id_from_task_headers,
    reset_request_id,
)

from workers.task_context import run_traced_task


def test_valid_http_request_id_is_the_only_celery_header() -> None:
    token = bind_request_id("edge-request_123")
    try:
        assert celery_request_headers() == {"request_id": "edge-request_123"}
    finally:
        reset_request_id(token)


@pytest.mark.parametrize(
    "invalid",
    ["", "x" * 65, "line\nbreak", "tab\tvalue", "非-ascii", None],
)
def test_invalid_task_request_id_is_replaced(invalid: object) -> None:
    replacement = request_id_from_task_headers(
        {
            "request_id": invalid,
            "authorization": "Bearer must-not-propagate",
            "cookie": "session=must-not-propagate",
        }
    )
    assert replacement != invalid
    assert len(replacement) == 36
    assert "\n" not in replacement and "\t" not in replacement


def test_worker_logs_request_task_and_job_without_authentication_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr("workers.task_context.log", logger)
    task = SimpleNamespace(
        name="ahamark.report.run",
        request=SimpleNamespace(
            id="celery-task-1",
            task="ahamark.report.run",
            headers={
                "request_id": "edge-request-1",
                "authorization": "Bearer secret",
                "cookie": "session=secret",
                "csrf": "secret",
            },
        ),
    )

    assert run_traced_task(task, "report-job-1", lambda: "done") == "done"

    events = [call.kwargs for call in logger.info.call_args_list]
    assert [event["status"] for event in events] == ["started", "completed"]
    assert all(event["request_id"] == "edge-request-1" for event in events)
    assert all(event["task_id"] == "celery-task-1" for event in events)
    assert all(event["job_id"] == "report-job-1" for event in events)
    assert all(event["task_name"] == "ahamark.report.run" for event in events)
    encoded = json.dumps(events)
    assert "Bearer secret" not in encoded
    assert "session=secret" not in encoded
    assert '"csrf"' not in encoded


def test_worker_redelivery_safely_inherits_same_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr("workers.task_context.log", logger)
    task = SimpleNamespace(
        name="ahamark.report.run",
        request=SimpleNamespace(
            id="celery-task-redelivered",
            task="ahamark.report.run",
            headers={"request_id": "original-request"},
            delivery_info={"redelivered": True},
        ),
    )

    run_traced_task(task, "report-job-redelivered", lambda: None)

    assert {call.kwargs["request_id"] for call in logger.info.call_args_list} == {
        "original-request"
    }


def test_preproduction_nginx_healthcheck_uses_ipv4_loopback() -> None:
    compose = yaml.safe_load(
        (Path(__file__).parents[1] / "docker-compose.preproduction.yml").read_text(encoding="utf-8")
    )
    rendered_test = " ".join(compose["services"]["nginx"]["healthcheck"]["test"])
    assert "https://127.0.0.1:8443/health" in rendered_test
    assert "https://localhost:8443/health" not in rendered_test
