"""Repeatable, non-concurrent HTTP latency smoke for the 50-student synthetic fixture."""

import http.cookiejar
import json
import statistics
import time
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8000"
MARKER = "performance50.synthetic.invalid"


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}"))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * fraction + 0.999) - 1))]


def main() -> None:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    scenarios = {
        "login": (
            "POST",
            "/auth/login",
            {
                "email": "synthetic-performance50@example.com",
                "password": "Synthetic-Performance-50!",
            },
            5,
        ),
        "class_list": ("GET", "/api/classes?page_size=20", None, 30),
        "student_list_50": (
            "GET",
            f"/api/classes/{uid('class-1')}/students?page_size=50&sort=number_asc",
            None,
            30,
        ),
        "assignment_list": ("GET", "/api/assignments?page_size=20", None, 30),
        "assignment_detail_20_questions": (
            "GET",
            f"/api/assignments/{uid('assignment-1')}",
            None,
            30,
        ),
    }
    results: dict[str, object] = {}
    for name, (method, path, payload, repetitions) in scenarios.items():
        elapsed: list[float] = []
        successes = 0
        request_ids: list[str] = []
        for _ in range(repetitions):
            data = json.dumps(payload).encode() if payload is not None else None
            request = urllib.request.Request(
                BASE + path,
                data=data,
                headers={"Content-Type": "application/json"},
                method=method,
            )
            started = time.perf_counter()
            with opener.open(request, timeout=10) as response:
                response.read()
                elapsed.append((time.perf_counter() - started) * 1000)
                successes += response.status < 400
                request_ids.append(response.headers.get("x-request-id", ""))
        results[name] = {
            "requests": repetitions,
            "success_rate": successes / repetitions,
            "p50_ms": round(statistics.median(elapsed), 2),
            "p95_ms": round(percentile(elapsed, 0.95), 2),
            "max_ms": round(max(elapsed), 2),
            "request_ids_present": all(request_ids),
        }
    output = Path("docs/performance-results.json")
    output.write_text(
        json.dumps(
            {
                "result": "passed",
                "scope": "single-client development latency smoke; async throughput not measured",
                "fixture": {
                    "classes": 2,
                    "students_per_class": 50,
                    "assignments": 2,
                    "questions_per_assignment": 20,
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"PERFORMANCE_SMOKE_PASSED output={output}")


if __name__ == "__main__":
    main()
