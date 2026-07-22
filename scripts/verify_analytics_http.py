"""Real HTTP verification for the idempotent Analytics 7.2 fixture."""

from __future__ import annotations

import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8000"
MARKER = "analytics72.synthetic.invalid"
PASSWORDS = {"a": "Synthetic-A-7.2!", "b": "Synthetic-B-7.2!"}
EMAILS = {"a": "synthetic-analytics72-a@example.com", "b": "synthetic-analytics72-b@example.com"}
records: list[dict[str, Any]] = []


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}"))


def session(teacher: str) -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    call(
        opener,
        jar,
        "POST",
        "/auth/login",
        {"email": EMAILS[teacher], "password": PASSWORDS[teacher]},
        200,
    )
    return opener, jar


def call(
    opener: urllib.request.OpenerDirector,
    jar: http.cookiejar.CookieJar,
    method: str,
    path: str,
    body: object | None = None,
    expected: int = 200,
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    csrf = next((cookie.value for cookie in jar if cookie.name == "ahamark_csrf"), None)
    if csrf and method not in {"GET", "HEAD", "OPTIONS"}:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        response = opener.open(request)
        status, raw, request_id = (
            response.status,
            response.read(),
            response.headers.get("x-request-id"),
        )
    except urllib.error.HTTPError as error:
        status, raw, request_id = error.code, error.read(), error.headers.get("x-request-id")
    payload = json.loads(raw) if raw else None
    records.append(
        {
            "method": method,
            "path": path,
            "status": status,
            "request_id": request_id,
            "schema": sorted(key for key in payload if key != "csrf_token")
            if isinstance(payload, dict)
            else type(payload).__name__,
            "core": core(payload),
        }
    )
    assert status == expected, (method, path, status, payload)
    assert request_id
    return payload


def core(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return {"count": len(payload)} if isinstance(payload, list) else payload
    return {
        key: payload[key]
        for key in ("code", "total", "pages", "participant_count", "status", "id")
        if key in payload
    }


def main() -> None:
    a, jar_a = session("a")
    latest = uid("analytics-3")
    question, kp1, kp2 = uid("q-3-1"), uid("kp-1"), uid("kp-2")
    class_id, student1, student3 = uid("class-a"), uid("student-1"), uid("student-3")
    band1 = call(
        a,
        jar_a,
        "GET",
        f"/api/analytics/{latest}/score-bands/90-100/students?page=1&page_size=1&sort=student_number",
        expected=200,
    )
    band2 = call(
        a,
        jar_a,
        "GET",
        f"/api/analytics/{latest}/score-bands/90-100/students?page=2&page_size=1&sort=student_number",
        expected=200,
    )
    assert band1["total"] == 2 and len(band1["items"]) == len(band2["items"]) == 1
    assert band1["items"][0]["student_number"] < band2["items"][0]["student_number"]
    question_rows = call(
        a,
        jar_a,
        "GET",
        f"/api/analytics/{latest}/questions/{question}/students?score_filter=full",
        expected=200,
    )
    assert question_rows["total"] == 3
    kp = call(a, jar_a, "GET", f"/api/analytics/{latest}/knowledge-points/{kp1}", expected=200)
    assert kp["total"] == 3 and "完整" in kp["scoring_rule"]
    errors = call(a, jar_a, "GET", f"/api/analytics/{latest}/errors/calculation", expected=200)
    assert errors["total"] == 3
    trends = call(a, jar_a, "GET", f"/api/classes/{class_id}/analytics/trends", expected=200)
    assert [x["participant_count"] for x in trends["items"]] == [3, 2, 3]
    assert [x["sample_changed"] for x in trends["items"]] == [False, True, True]
    assert all(0 <= x["average_score_rate"] <= 1 for x in trends["items"])
    student_trend = call(
        a, jar_a, "GET", f"/api/students/{student3}/analytics/trends", expected=200
    )
    assert len(student_trend["items"]) == 2
    class_kp = call(
        a, jar_a, "GET", f"/api/classes/{class_id}/knowledge-points/{kp2}/trend", expected=200
    )
    assert len(class_kp["items"]) == 2
    student_kp = call(
        a, jar_a, "GET", f"/api/students/{student1}/knowledge-points/{kp2}/trend", expected=200
    )
    assert len(student_kp["items"]) == 2
    detail = call(a, jar_a, "GET", f"/api/students/{student1}/analytics", expected=200)
    assert detail["student"]["student_number"] == "001" and len(detail["questions"]) == 3
    assert len(detail["score_revisions"]) == 1
    reports = call(a, jar_a, "GET", f"/api/students/{student1}/report-jobs", expected=200)
    assert {x["status"] for x in reports} >= {"completed", "failed"}
    recreated = call(
        a, jar_a, "POST", f"/api/report-jobs/{uid('report-failed')}/retry", expected=201
    )
    assert recreated["id"] != uid("report-failed")
    generated = call(a, jar_a, "POST", f"/api/analytics/{latest}/insights", expected=201)
    edited = call(
        a,
        jar_a,
        "PATCH",
        f"/api/teaching-insights/{generated['id']}",
        {"recommendations": ["Synthetic verified edit"]},
        200,
    )
    assert edited["status"] == "draft"
    confirmed = call(
        a, jar_a, "POST", f"/api/teaching-insights/{generated['id']}/confirm", expected=200
    )
    assert confirmed["status"] == "confirmed"
    replacement = call(
        a, jar_a, "POST", f"/api/teaching-insights/{generated['id']}/regenerate", expected=201
    )
    invalid = call(
        a, jar_a, "POST", f"/api/teaching-insights/{replacement['id']}/invalidate", expected=200
    )
    assert invalid["status"] == "invalid"
    call(
        a, jar_a, "GET", f"/api/analytics/{uuid.uuid4()}/score-bands/90-100/students", expected=404
    )
    call(
        a, jar_a, "GET", f"/api/analytics/{latest}/score-bands/90-100/students?page=0", expected=422
    )
    b, jar_b = session("b")
    forbidden = [
        ("GET", f"/api/analytics/{latest}/score-bands/90-100/students", None),
        ("GET", f"/api/analytics/{latest}/questions/{question}/students", None),
        ("GET", f"/api/analytics/{latest}/knowledge-points/{kp1}", None),
        ("GET", f"/api/analytics/{latest}/errors/calculation", None),
        ("GET", f"/api/classes/{class_id}/analytics/trends", None),
        ("GET", f"/api/students/{student1}/analytics/trends", None),
        ("GET", f"/api/students/{student1}/analytics", None),
        ("GET", f"/api/students/{student1}/report-jobs", None),
        ("GET", f"/api/report-jobs/{uid('report-completed')}", None),
        ("GET", f"/api/report-jobs/{uid('report-completed')}/download", None),
        ("PATCH", f"/api/teaching-insights/{uid('insight')}", {"recommendations": ["forbidden"]}),
        ("POST", f"/api/teaching-insights/{uid('insight')}/confirm", None),
        ("POST", f"/api/teaching-insights/{uid('insight')}/regenerate", None),
        ("POST", f"/api/report-jobs/{uid('report-failed')}/retry", None),
    ]
    for method, path, body in forbidden:
        payload = call(b, jar_b, method, path, body, 404)
        assert not any(
            term in json.dumps(payload)
            for term in ("Synthetic Student", "score_rate", "student_number")
        )
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/analytics72-http-verification.json")
    output.write_text(
        json.dumps(
            {
                "result": "passed",
                "assertions": {
                    "release_participants": [3, 2, 3],
                    "missing_assignment_not_zero": True,
                    "cross_teacher_hidden": True,
                },
                "requests": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"ANALYTICS_HTTP_VERIFICATION_PASSED requests={len(records)} output={output}")


if __name__ == "__main__":
    main()
