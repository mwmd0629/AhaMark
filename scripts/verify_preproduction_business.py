"""Create and verify the Part 8 production-safe synthetic dataset through public APIs."""

import io
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

BASE_URL = os.environ["PREPROD_BASE_URL"].rstrip("/")
EMAIL = os.environ["PREPROD_TEACHER_EMAIL"]
PASSWORD = os.environ["PREPROD_TEACHER_PASSWORD"]
ORIGIN = BASE_URL


def printed_png(lines: list[str]) -> bytes:
    image = Image.new("RGB", (1400, 900), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 72)
    y = 140
    for line in lines:
        draw.text((120, y), line, fill="black", font=font)
        y += 130
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


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
        self.sequence = 0

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        files: list[tuple[str, str, bytes, str]] | None = None,
        request_id: str | None = None,
    ) -> tuple[Any, str]:
        self.sequence += 1
        headers = {
            "Origin": ORIGIN,
            "X-Request-ID": request_id or f"business-{self.sequence:03d}",
        }
        data = None
        if files:
            boundary = f"----AhaMark{uuid.uuid4().hex}"
            chunks: list[bytes] = []
            for field, filename, content, content_type in files:
                chunks.extend(
                    [
                        f"--{boundary}\r\n".encode(),
                        (
                            f'Content-Disposition: form-data; name="{field}"; '
                            f'filename="{filename}"\r\n'
                        ).encode(),
                        f"Content-Type: {content_type}\r\n\r\n".encode(),
                        content,
                        b"\r\n",
                    ]
                )
            chunks.append(f"--{boundary}--\r\n".encode())
            data = b"".join(chunks)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
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
                response_request_id = response.headers.get("X-Request-ID", "")
                payload = json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} returned {exc.code}: {detail}") from exc
        if not response_request_id:
            raise RuntimeError(f"{method} {path} omitted X-Request-ID")
        return payload, response_request_id

    def login(self) -> None:
        payload, _ = self.request("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
        self.csrf = payload["csrf_token"]


def wait_job(api: Api, path: str, terminal: set[str], timeout: int = 240) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload, _ = api.request("GET", path)
        if payload["status"] in terminal:
            return payload
        time.sleep(1)
    raise TimeoutError(path)


def main() -> None:
    if not EMAIL.endswith(".synthetic.invalid"):
        raise RuntimeError("synthetic teacher domain required")
    api = Api()
    api.login()
    classes, _ = api.request("GET", "/api/classes?page_size=100")
    school_class = next(
        (item for item in classes["items"] if item["name"] == "Part 8 Final Synthetic Class"),
        None,
    )
    if school_class is None:
        school_class, _ = api.request(
            "POST",
            "/api/classes",
            {
                "name": "Part 8 Final Synthetic Class",
                "grade": "8",
                "subject": "Mathematics",
                "academic_year": "2026",
                "semester": "Synthetic",
            },
        )
    existing_students, _ = api.request(
        "GET", f"/api/classes/{school_class['id']}/students?page_size=100"
    )
    students_by_number = {
        student["student_number"]: student for student in existing_students["items"]
    }
    students = []
    for number, name in [
        ("F001", "Synthetic Completed One"),
        ("F002", "Synthetic Completed Two"),
        ("F003", "Synthetic Missing Three"),
    ]:
        student = students_by_number.get(number)
        if student is None:
            student, _ = api.request(
                "POST",
                f"/api/classes/{school_class['id']}/students",
                {"student_number": number, "name": name},
            )
        students.append(student)
    assignments, _ = api.request("GET", "/api/assignments?page_size=100")
    assignment = next(
        (
            item
            for item in assignments["items"]
            if item["title"] == "Part 8 Objective Synthetic Assignment"
        ),
        None,
    )
    if assignment is None:
        assignment, _ = api.request(
            "POST",
            "/api/assignments",
            {
                "title": "Part 8 Objective Synthetic Assignment",
                "subject": "Mathematics",
                "grade": "8",
                "total_score": 10,
                "class_ids": [school_class["id"]],
            },
        )
        api.request(
            "POST",
            f"/api/assignments/{assignment['id']}/files",
            files=[
                (
                    "file",
                    "synthetic-paper.png",
                    printed_png(["1. Select A", "2. Select B"]),
                    "image/png",
                )
            ],
        )
        questions = []
        for number, answer in [("1", "A"), ("2", "B")]:
            question, _ = api.request(
                "POST",
                f"/api/assignments/{assignment['id']}/questions",
                {
                    "question_number": number,
                    "question_type": "single_choice",
                    "max_score": 5,
                    "content_text": f"Synthetic objective question {number}",
                },
            )
            api.request(
                "PUT",
                f"/api/assignments/{assignment['id']}/rubrics/{question['id']}",
                {
                    "standard_answer": answer,
                    "items": [{"title": "Correct objective answer", "points": 5}],
                },
            )
            questions.append(question)
        api.request("POST", f"/api/assignments/{assignment['id']}/publish")
    else:
        assignment, _ = api.request("GET", f"/api/assignments/{assignment['id']}")
        questions = assignment["paper_version"]["questions"]
    batches, _ = api.request(
        "GET", f"/api/assignments/{assignment['id']}/grading-batches?page_size=100"
    )
    batch = next(
        (item for item in batches["items"] if item.get("name") == "Part 8 Final Synthetic Batch"),
        None,
    )
    if batch is None:
        batch, _ = api.request(
            "POST",
            f"/api/assignments/{assignment['id']}/grading-batches",
            {"class_id": school_class["id"], "name": "Part 8 Final Synthetic Batch"},
        )
    existing_submissions, _ = api.request("GET", f"/api/grading-batches/{batch['id']}/submissions")
    existing_student_ids = {
        submission["student_id"]
        for submission in existing_submissions
        if submission.get("student_id")
    }
    uploads = []
    for student, answers in zip(students[:2], [["A", "B"], ["A", "A"]], strict=True):
        if student["id"] in existing_student_ids:
            continue
        number = student["student_number"]
        files = [
            (
                "files",
                f"{number}-q{index}.png",
                printed_png([f"Student {number}", f"Question {index}", f"Answer {answer}"]),
                "image/png",
            )
            for index, answer in enumerate(answers, 1)
        ]
        uploaded, _ = api.request("POST", f"/api/grading-batches/{batch['id']}/files", files=files)
        uploads.extend(uploaded["items"])
    submissions, _ = api.request("GET", f"/api/grading-batches/{batch['id']}/submissions")
    completed_student_ids = {student["id"] for student in students[:2]}
    submission_ids = sorted(
        row["id"] for row in submissions if row.get("student_id") in completed_student_ids
    )
    if len(submission_ids) != 2:
        raise RuntimeError(f"expected two matched submissions, got {submission_ids}")
    submission_status = {row["id"]: row["status"] for row in submissions}
    recognition_jobs = []
    for submission_id in submission_ids:
        if submission_status[submission_id] == "finalized":
            continue
        job, _ = api.request(
            "POST",
            f"/api/submissions/{submission_id}/recognition-jobs",
            {"idempotency_key": f"part8-final-{submission_id}"},
        )
        completed = wait_job(
            api,
            f"/api/submissions/{submission_id}/recognition-jobs/{job['id']}",
            {"completed", "partially_completed", "failed"},
        )
        if completed["status"] != "completed" or completed["provider"] != "rapidocr":
            raise RuntimeError(f"real OCR did not complete: {completed}")
        recognition_jobs.append(completed["id"])
    workspace, _ = api.request("GET", f"/api/grading-batches/{batch['id']}/review-workspace")
    intended = {
        submission_ids[0]: {"1": "A", "2": "B"},
        submission_ids[1]: {"1": "A", "2": "A"},
    }
    for item in workspace["items"]:
        submission_id = item["submission_id"]
        if submission_id not in intended:
            continue
        if item["status"] == "finalized":
            continue
        for answer in item["answers"]:
            corrected = intended[submission_id][answer["question"]["number"]]
            api.request(
                "PATCH",
                f"/api/student-answers/{answer['id']}",
                {"corrected_text": corrected},
            )
            graded, _ = api.request("POST", f"/api/student-answers/{answer['id']}/grade")
            if graded["status"] != "suggested":
                raise RuntimeError(f"objective grading failed: {graded}")
            api.request(
                "PUT",
                f"/api/student-answers/{answer['id']}/review",
                {
                    "decision": "accepted",
                    "final_feedback": "Synthetic objective verification",
                },
            )
        snapshot, _ = api.request("POST", f"/api/submissions/{submission_id}/finalize")
        if snapshot["status"] != "complete":
            raise RuntimeError(f"incomplete snapshot: {snapshot}")
    readiness, _ = api.request(
        "GET",
        (f"/api/assignments/{assignment['id']}/classes/{school_class['id']}/grade-readiness"),
    )
    if readiness["releasable_count"] != 2 or len(readiness["missing_student_ids"]) != 1:
        raise RuntimeError(f"readiness denominator mismatch: {readiness}")
    release, _ = api.request(
        "POST",
        "/api/grade-releases",
        {
            "assignment_id": assignment["id"],
            "class_id": school_class["id"],
            "release_mode": "score_and_feedback",
            "idempotency_key": f"part8-final-release-{assignment['id']}",
        },
    )
    analytics, _ = api.request("POST", f"/api/grade-releases/{release['id']}/analytics")
    if analytics["metrics"]["participant_count"] != 2:
        raise RuntimeError(f"missing student entered analytics: {analytics['metrics']}")
    report_request_id = f"report-{uuid.uuid4().hex}"
    query = urllib.parse.urlencode(
        {
            "report_type": "gradebook_xlsx",
            "idempotency_key": (f"part8-final-report-{release['id']}-{report_request_id[-12:]}"),
        }
    )
    report, echoed_request_id = api.request(
        "POST",
        f"/api/grade-releases/{release['id']}/reports?{query}",
        request_id=report_request_id,
    )
    if not echoed_request_id:
        raise RuntimeError("Nginx/API request ID missing")
    report_request_id = echoed_request_id
    report = wait_job(
        api,
        f"/api/report-jobs/{report['id']}",
        {"completed", "partially_completed", "failed"},
    )
    if report["status"] != "completed":
        raise RuntimeError(f"report failed: {report}")
    download, _ = api.request("GET", f"/api/report-jobs/{report['id']}/download")
    if not download.get("url"):
        raise RuntimeError("report download metadata missing")
    evidence = {
        "class_id": school_class["id"],
        "student_ids": [student["id"] for student in students],
        "missing_student_id": students[2]["id"],
        "assignment_id": assignment["id"],
        "question_ids": [question["id"] for question in questions],
        "grading_batch_id": batch["id"],
        "submission_ids": submission_ids,
        "recognition_job_ids": recognition_jobs,
        "score_snapshot_ids": [item["score_snapshot_id"] for item in release["items"]],
        "grade_release_id": release["id"],
        "grade_release_items": release["items"],
        "analytics_snapshot_id": analytics["id"],
        "analytics_participant_count": analytics["metrics"]["participant_count"],
        "analytics_average_score": analytics["metrics"]["average_score"],
        "report_job_id": report["id"],
        "report_status": report["status"],
        "report_stored_file_id": report["stored_file_id"],
        "report_request_id": report_request_id,
        "synthetic_domain": True,
        "real_rapidocr": True,
    }
    encoded = json.dumps(evidence, sort_keys=True)
    output_path = os.environ.get("PREPROD_EVIDENCE_OUTPUT")
    if output_path:
        Path(output_path).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
