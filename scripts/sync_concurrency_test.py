"""Concurrent HTTP capacity matrix for deterministic S1/S2/S3 fixtures."""

import argparse
import concurrent.futures
import http.cookiejar
import json
import math
import statistics
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

MARKER = "performance-capacity.synthetic.invalid"
PASSWORD = "Synthetic-Capacity-Only!"
CONCURRENCY = (1, 5, 10, 20)


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}"))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))]


def login(base: str, teacher_index: int) -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    payload = json.dumps(
        {
            "email": f"capacity-teacher-{teacher_index}@{MARKER}",
            "password": PASSWORD,
        }
    ).encode()
    request = urllib.request.Request(
        f"{base}/auth/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=10) as response:
        response.read()
    return opener


def one_request(
    opener: urllib.request.OpenerDirector, base: str, method: str, path: str
) -> dict[str, Any]:
    request = urllib.request.Request(base + path, method=method)
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=15) as response:
            body = response.read()
            return {
                "status": response.status,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "bytes": len(body),
                "request_id": bool(response.headers.get("x-request-id")),
            }
    except urllib.error.HTTPError as exc:
        exc.read()
        return {
            "status": exc.code,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "bytes": 0,
            "request_id": bool(exc.headers.get("x-request-id")),
        }
    except (TimeoutError, urllib.error.URLError):
        return {"status": 0, "elapsed_ms": 15000.0, "bytes": 0, "request_id": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8800")
    parser.add_argument("--requests-per-level", type=int, default=40)
    parser.add_argument("--output", default="docs/sync-capacity-results.json")
    args = parser.parse_args()
    scenarios = (
        ("auth_me", 1, "GET", "/auth/me", "read"),
        ("class_list_s1", 1, "GET", "/api/classes?page_size=20", "read"),
        (
            "students_50",
            1,
            "GET",
            f"/api/classes/{uid('class-s1')}/students?page=1&page_size=50&sort=number_asc",
            "read",
        ),
        (
            "assignment_20_questions",
            1,
            "GET",
            f"/api/assignments/{uid('assignment-s1')}",
            "read",
        ),
        (
            "students_100",
            2,
            "GET",
            f"/api/classes/{uid('class-s2-t1-c1')}/students?page=1&page_size=100&sort=number_asc",
            "read",
        ),
        (
            "assignment_50_questions",
            2,
            "GET",
            f"/api/assignments/{uid('assignment-s2-t1-c1')}",
            "read",
        ),
        (
            "students_200_page_1",
            4,
            "GET",
            f"/api/classes/{uid('class-s3-t1')}/students?page=1&page_size=100&sort=number_asc",
            "read",
        ),
        (
            "students_200_page_2",
            4,
            "GET",
            f"/api/classes/{uid('class-s3-t1')}/students?page=2&page_size=100&sort=number_asc",
            "read",
        ),
        (
            "assignment_100_questions",
            4,
            "GET",
            f"/api/assignments/{uid('assignment-s3-t1')}",
            "read",
        ),
        (
            "publish_check_100",
            4,
            "GET",
            f"/api/assignments/{uid('assignment-s3-t1')}/publish-check",
            "read",
        ),
    )
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    rows: list[dict[str, Any]] = []
    authenticated_by_teacher: dict[int, urllib.request.OpenerDirector] = {}
    for name, teacher, method, path, kind in scenarios:
        for concurrency in CONCURRENCY:
            if teacher not in authenticated_by_teacher:
                authenticated_by_teacher[teacher] = login(args.base, teacher)
            authenticated = authenticated_by_teacher[teacher]
            openers = [authenticated] * concurrency
            wall_start = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                results = list(
                    pool.map(
                        lambda _, method=method, path=path, openers=openers: one_request(
                            openers[_ % len(openers)], args.base, method, path
                        ),
                        range(args.requests_per_level),
                    )
                )
            wall_seconds = time.perf_counter() - wall_start
            elapsed = [float(item["elapsed_ms"]) for item in results]
            statuses = Counter(str(item["status"]) for item in results)
            expected_successes = sum(200 <= int(item["status"]) < 400 for item in results)
            rows.append(
                {
                    "scenario": name,
                    "kind": kind,
                    "requests": len(results),
                    "concurrency": concurrency,
                    "success_rate": round(expected_successes / len(results), 6),
                    "p50_ms": round(statistics.median(elapsed), 2),
                    "p95_ms": round(percentile(elapsed, 0.95), 2),
                    "p99_ms": round(percentile(elapsed, 0.99), 2),
                    "max_ms": round(max(elapsed), 2),
                    "timeouts": statuses.get("0", 0),
                    "status_counts": dict(statuses),
                    "throughput_rps": round(len(results) / wall_seconds, 2),
                    "response_bytes_mean": round(
                        statistics.mean(float(item["bytes"]) for item in results), 2
                    ),
                    "request_ids_present": all(bool(item["request_id"]) for item in results),
                }
            )
            print(
                f"{name} c={concurrency} success={expected_successes}/{len(results)} "
                f"p95={rows[-1]['p95_ms']}ms"
            )
    unexplained_5xx = sum(
        count
        for row in rows
        for status, count in row["status_counts"].items()
        if 500 <= int(status) < 600
    )
    passed = all(row["success_rate"] >= 0.99 and row["p95_ms"] <= 500 for row in rows)
    evidence = {
        "schema_version": 1,
        "result": "passed" if passed and unexplained_5xx == 0 else "failed",
        "scope": "isolated single-API development capacity; not production capacity",
        "thresholds": {
            "success_rate_min": 0.99,
            "read_p95_ms_max": 500,
            "unexplained_5xx_max": 0,
        },
        "started_at": started_at,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "requests_per_level": args.requests_per_level,
        "concurrency_levels": list(CONCURRENCY),
        "unexplained_5xx": unexplained_5xx,
        "results": rows,
    }
    Path(args.output).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
