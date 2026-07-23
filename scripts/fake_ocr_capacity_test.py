"""Run Fake OCR orchestration capacity stages through API and Celery."""

import http.cookiejar
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8800"
MARKER = "performance-capacity.synthetic.invalid"
PASSWORD = "Synthetic-Capacity-Only!"
SCALES = (150, 200, 250)


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}"))


def request(
    opener: urllib.request.OpenerDirector,
    method: str,
    path: str,
    csrf: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[Any, float]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=30) as response:
            body = json.loads(response.read() or b"{}")
            if isinstance(body, dict):
                body["_http_status"] = response.status
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
        body["_http_status"] = exc.code
    return body, (time.perf_counter() - started) * 1000


def main() -> None:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login, _ = request(
        opener,
        "POST",
        "/auth/login",
        payload={
            "email": f"capacity-teacher-1@{MARKER}",
            "password": PASSWORD,
        },
    )
    csrf = str(login["csrf_token"])
    stages = []
    for pages in SCALES:
        assignment_id = uid(f"fake-ocr-assignment-{pages}")
        paper_id = uid(f"fake-ocr-paper-{pages}")
        path = f"/api/assignments/{assignment_id}/recognition/jobs"
        job, create_ms = request(
            opener,
            "POST",
            path,
            csrf,
            {
                "paper_version_id": paper_id,
                "idempotency_key": f"capacity-fake-ocr-v2-{pages}",
            },
        )
        job_id = str(job["id"])
        queued_at = time.perf_counter()
        first_running_ms: float | None = None
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            current, _ = request(opener, "GET", f"{path}/{job_id}")
            if current["status"] == "running" and first_running_ms is None:
                first_running_ms = (time.perf_counter() - queued_at) * 1000
            if current["status"] in {"completed", "partially_completed", "failed"}:
                break
            time.sleep(0.2)
        total_ms = (time.perf_counter() - queued_at) * 1000
        pages_result, _ = request(opener, "GET", f"{path}/{job_id}/pages")
        stages.append(
            {
                "pages": pages,
                "assignment_id": assignment_id,
                "paper_version_id": paper_id,
                "job_id": job_id,
                "create_api_ms": round(create_ms, 2),
                "queue_wait_ms": round(first_running_ms or 0, 2),
                "total_ms": round(total_ms, 2),
                "per_page_average_ms": round(total_ms / pages, 2),
                "status": current["status"],
                "page_summary": current["page_summary"],
                "page_result_count": len(pages_result),
            }
        )
    evidence = {
        "schema_version": 1,
        "result": (
            "passed"
            if all(
                stage["status"] == "completed"
                and stage["page_summary"]["completed"] == stage["pages"]
                and stage["page_result_count"] == stage["pages"]
                for stage in stages
            )
            else "failed"
        ),
        "scope": "Fake OCR orchestration only; not OCR performance or accuracy",
        "stages": stages,
    }
    Path("docs/ocr-orchestration-capacity.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
