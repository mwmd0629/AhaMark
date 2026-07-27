"""Create and trace a real MathValidationJob through the HTTPS public API."""

import json
import os
import ssl
import time
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

BASE_URL = os.environ["PREPROD_BASE_URL"].rstrip("/")
EMAIL = os.environ["PREPROD_TEACHER_EMAIL"]
PASSWORD = os.environ["PREPROD_TEACHER_PASSWORD"]
FIXTURE = json.loads(Path(os.environ["STAGE3_FIXTURE"]).read_text(encoding="utf-8-sig"))


class Api:
    def __init__(self) -> None:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=context),
        )
        self.csrf = ""
        self.request_ids: list[str] = []

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[Any, int]:
        request_id = f"stage3-https-{uuid.uuid4().hex[:12]}"
        headers = {"Origin": BASE_URL, "X-Request-ID": request_id}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if method not in {"GET", "HEAD"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(
            f"{BASE_URL}{path}", data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read()
                returned_id = response.headers.get("X-Request-ID")
                if not returned_id and (path.startswith("/api/") or path.startswith("/auth/")):
                    raise RuntimeError("response omitted X-Request-ID")
                if returned_id:
                    self.request_ids.append(returned_id)
                content_type = response.headers.get("Content-Type", "")
                payload = (
                    json.loads(raw)
                    if raw and "json" in content_type
                    else raw.decode("utf-8", "replace")
                )
                return payload, response.status
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"{method} {path} returned {exc.code}: {exc.read().decode('utf-8', 'replace')}"
            ) from exc


def main() -> None:
    api = Api()
    health, health_status = api.request("GET", "/health")
    login, login_status = api.request("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
    api.csrf = login["csrf_token"]
    for route in ("/grading", "/assignments"):
        _, status = api.request("GET", route)
        if status != 200:
            raise RuntimeError(f"{route} returned {status}")
    idempotency_key = f"{FIXTURE['marker']}-https"
    job, create_status = api.request(
        "POST",
        "/api/math-validation/jobs",
        {
            "student_answer_id": FIXTURE["student_answer_id"],
            "rubric_version_id": FIXTURE["rubric_version_id"],
            "idempotency_key": idempotency_key,
        },
    )
    deadline = time.monotonic() + 90
    current = job
    while current["status"] not in {"completed", "failed", "stale"}:
        if time.monotonic() > deadline:
            raise TimeoutError(f"job {job['id']} did not finish")
        time.sleep(0.5)
        current, _ = api.request("GET", f"/api/math-validation/jobs/{job['id']}")
    if current["status"] != "completed":
        raise RuntimeError(f"job ended as {current['status']}")
    result = {
        "health_status": health_status,
        "health": health,
        "login_status": login_status,
        "create_status": create_status,
        "request_ids": api.request_ids,
        "validation_job_id": job["id"],
        "celery_task_id": job.get("task_id"),
        "status": current["status"],
        "engine_version": current["engine_version"],
        "config_version": current["config_version"],
        "suggested_total": current["suggested_total"],
        "results": current["results"],
        "https": True,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
