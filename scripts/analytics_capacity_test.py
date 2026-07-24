"""Exercise analytics generation, idempotency, and drill-downs at capacity scales."""

import http.cookiejar
import json
import statistics
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8800"
MARKER = "performance-capacity.synthetic.invalid"
PASSWORD = "Synthetic-Capacity-Only!"
SCALES = (
    ("s1", 1, 50, 20),
    ("s2-t1-c1", 2, 100, 50),
    ("s3-t1", 4, 200, 100),
)


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}"))


def request(
    opener: urllib.request.OpenerDirector,
    method: str,
    path: str,
    csrf: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], float]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=120) as response:
            body = json.loads(response.read() or b"{}")
            body["_http_status"] = response.status
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
        body["_http_status"] = exc.code
    return body, (time.perf_counter() - started) * 1000


def run_scale(
    scale: str,
    teacher_index: int,
    students: int,
    questions: int,
) -> dict[str, Any]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login, _ = request(
        opener,
        "POST",
        "/auth/login",
        payload={
            "email": f"capacity-teacher-{teacher_index}@{MARKER}",
            "password": PASSWORD,
        },
    )
    csrf = str(login["csrf_token"])
    release_id = uid(f"release-{scale}")
    generation_ids: list[str] = []
    generation_ms: list[float] = []
    generation_statuses: list[int] = []
    analytics: dict[str, Any] = {}
    for _ in range(5):
        analytics, elapsed = request(
            opener,
            "POST",
            f"/api/grade-releases/{release_id}/analytics",
            csrf,
        )
        generation_ids.append(str(analytics.get("id")))
        generation_ms.append(elapsed)
        generation_statuses.append(int(analytics["_http_status"]))
    analytics_id = generation_ids[0]
    drill_paths = (
        ("GET", f"/api/analytics/{analytics_id}/score-bands/90-100/students"),
        (
            "GET",
            f"/api/analytics/{analytics_id}/questions/{uid(f'question-{scale}-1')}/students",
        ),
        (
            "GET",
            f"/api/analytics/{analytics_id}/knowledge-points/{uid(f'kp-{teacher_index}-1')}",
        ),
        ("GET", f"/api/analytics/{analytics_id}/errors/concept"),
        ("GET", f"/api/classes/{uid(f'class-{scale}')}/analytics/trends"),
        (
            "GET",
            f"/api/students/{uid(f'student-{scale}-1')}/analytics/trends",
        ),
        ("GET", f"/api/students/{uid(f'student-{scale}-1')}/analytics"),
        ("POST", f"/api/analytics/{analytics_id}/insights"),
    )
    drills = []
    for method, path in drill_paths:
        body, elapsed = request(opener, method, path, csrf if method == "POST" else None)
        drills.append(
            {
                "method": method,
                "path": path,
                "status": body["_http_status"],
                "elapsed_ms": round(elapsed, 2),
                "bytes": len(json.dumps(body)),
            }
        )
    return {
        "scale": scale,
        "students": students,
        "questions": questions,
        "release_id": release_id,
        "snapshot_id": analytics_id,
        "source_snapshot_count": analytics.get("source_snapshot_count"),
        "generation_attempts": len(generation_ids),
        "unique_snapshot_ids": len(set(generation_ids)),
        "generation_statuses": generation_statuses,
        "generation_p50_ms": round(statistics.median(generation_ms), 2),
        "generation_max_ms": round(max(generation_ms), 2),
        "drills": drills,
    }


def concurrent_idempotency() -> dict[str, Any]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login, _ = request(
        opener,
        "POST",
        "/auth/login",
        payload={
            "email": f"capacity-teacher-4@{MARKER}",
            "password": PASSWORD,
        },
    )
    csrf = str(login["csrf_token"])
    release_id = uid("release-s3-t1")

    def generate(_index: int) -> tuple[str, int]:
        body, _ = request(
            opener,
            "POST",
            f"/api/grade-releases/{release_id}/analytics",
            csrf,
        )
        return str(body.get("id")), int(body["_http_status"])

    with ThreadPoolExecutor(max_workers=20) as executor:
        responses = list(executor.map(generate, range(20)))
    return {
        "attempts": len(responses),
        "statuses": [status for _, status in responses],
        "unique_snapshot_ids": len({snapshot_id for snapshot_id, _ in responses}),
        "snapshot_id": responses[0][0],
    }


def main() -> None:
    results = [run_scale(*scale) for scale in SCALES]
    concurrency = concurrent_idempotency()
    passed = (
        all(
            result["unique_snapshot_ids"] == 1
            and result["source_snapshot_count"] == result["students"]
            and all(drill["status"] in {200, 201} for drill in result["drills"])
            for result in results
        )
        and concurrency["unique_snapshot_ids"] == 1
    )
    evidence = {
        "schema_version": 1,
        "result": "passed" if passed else "failed",
        "scope": "50/100/200 distinct released students with 20/50/100 questions",
        "scales": results,
        "concurrent_idempotency": concurrency,
    }
    Path("docs/analytics-capacity-results.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
