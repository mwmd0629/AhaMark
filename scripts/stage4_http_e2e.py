"""Run the Stage 4 API -> Celery chain against the isolated business stack."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import uuid
from http.cookiejar import CookieJar
from typing import Any

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import (
    GradeRelease,
    QuestionRecognitionEvidence,
    StructuredRubricVersion,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
)
from sqlalchemy import select

BASE_URL = os.environ.get("STAGE4_BASE_URL", "http://localhost:8800").rstrip("/")
ORIGIN = os.environ.get("STAGE4_ORIGIN", BASE_URL).rstrip("/")


def protected_hashes(db: Any) -> dict[str, str]:
    groups = (
        db.execute(
            select(TeacherReview.id, TeacherReview.final_score).order_by(TeacherReview.id)
        ).all(),
        db.execute(
            select(
                SubmissionScoreSnapshot.id,
                SubmissionScoreSnapshot.total_score,
                SubmissionScoreSnapshot.version,
            ).order_by(SubmissionScoreSnapshot.id)
        ).all(),
        db.execute(select(GradeRelease.id, GradeRelease.status).order_by(GradeRelease.id)).all(),
    )
    names = ("teacher_final_scores", "score_snapshots", "grade_releases")
    return {
        name: hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
        for name, rows in zip(names, groups, strict=True)
    }


class Api:
    def __init__(self) -> None:
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = ""
        self.request_ids: list[str] = []

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[Any, int]:
        headers = {
            "Origin": ORIGIN,
            "X-Request-ID": f"stage4-http-{uuid.uuid4().hex[:16]}",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if method not in {"GET", "HEAD"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(
            f"{BASE_URL}{path}", data=data, headers=headers, method=method
        )
        with self.opener.open(request, timeout=60) as response:
            request_id = response.headers.get("X-Request-ID")
            if request_id:
                self.request_ids.append(request_id)
            return json.load(response), response.status


def main() -> None:
    marker = f"stage4_http_e2e_{uuid.uuid4().hex[:12]}"
    temporary_password = f"Stage4-{uuid.uuid4().hex}!"
    with SessionLocal() as db:
        answer = db.scalar(
            select(StudentAnswer)
            .join(Submission, Submission.id == StudentAnswer.submission_id)
            .join(
                QuestionRecognitionEvidence,
                QuestionRecognitionEvidence.student_answer_id == StudentAnswer.id,
            )
            .where(
                Submission.finalized_at.is_(None),
                QuestionRecognitionEvidence.status == "confirmed",
                QuestionRecognitionEvidence.stale_at.is_(None),
            )
            .order_by(QuestionRecognitionEvidence.created_at.desc())
        )
        if not answer:
            raise RuntimeError("no confirmed synthetic answer")
        submission = db.get(Submission, answer.submission_id)
        if not submission:
            raise RuntimeError("submission missing")
        rubric = db.scalar(
            select(StructuredRubricVersion)
            .where(
                StructuredRubricVersion.question_id == answer.question_id,
                StructuredRubricVersion.status == "confirmed",
            )
            .order_by(StructuredRubricVersion.rubric_version.desc())
        )
        user = db.get(User, submission.owner_id)
        if not rubric or not user:
            raise RuntimeError("rubric or synthetic owner missing")
        old_password_hash = user.password_hash
        before = protected_hashes(db)
        user.password_hash = hash_password(temporary_password)
        db.commit()
        email = user.email
        answer_id = answer.id
        rubric_id = rubric.id

    try:
        api = Api()
        login, login_status = api.request(
            "POST", "/auth/login", {"email": email, "password": temporary_password}
        )
        api.csrf = login["csrf_token"]
        job, create_status = api.request(
            "POST",
            "/api/ai-grading/jobs",
            {
                "student_answer_id": str(answer_id),
                "rubric_version_id": str(rubric_id),
                "idempotency_key": marker,
            },
        )
        deadline = time.monotonic() + 60
        while job["status"] not in {
            "completed",
            "partially_completed",
            "abstained",
            "failed",
            "stale",
        }:
            if time.monotonic() > deadline:
                raise TimeoutError("AI scoring job did not finish")
            time.sleep(0.5)
            job, _ = api.request("GET", f"/api/ai-grading/jobs/{job['id']}")
        reviews = []
        for suggestion in job["suggestions"][:1]:
            review, review_status = api.request(
                "POST",
                f"/api/ai-grading/suggestions/{suggestion['id']}/review",
                {
                    "action": "accepted",
                    "reason": f"{marker}: audit-only acceptance",
                },
            )
            reviews.append({"status": review_status, "body": review})
        with SessionLocal() as db:
            after = protected_hashes(db)
        result = {
            "marker": marker,
            "login_status": login_status,
            "create_status": create_status,
            "job_id": job["id"],
            "status": job["status"],
            "provider": job["provider"],
            "suggestion_count": len(job["suggestions"]),
            "reviews": reviews,
            "request_ids": api.request_ids,
            "protected_hashes_before": before,
            "protected_hashes_after": after,
            "protected_state_unchanged": before == after,
            "automatic_finalize": False,
            "automatic_snapshot": False,
            "automatic_grade_release": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        with SessionLocal() as db:
            user = db.get(User, submission.owner_id)
            if user:
                user.password_hash = old_password_hash
                db.commit()


if __name__ == "__main__":
    main()
