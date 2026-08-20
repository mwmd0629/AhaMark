from datetime import timedelta

import pytest
from app.api.actor import get_current_actor
from app.api.auth import hash_password
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import Status, User, UserSession, now_utc
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

OPERATIONS = {
    "list",
    "get",
    "create",
    "update",
    "archive",
    "restore",
    "delete",
    "upload",
    "metadata",
    "signed_url",
    "download",
    "retry",
    "reorder",
    "split",
    "merge",
    "match",
    "recognize",
    "regrade",
    "bulk_accept",
    "review",
    "finalize",
    "readiness",
    "release",
    "report_create",
    "analytics_create",
    "drilldown",
    "confirm",
    "regenerate",
    "invalidate",
}

RESOURCE_OPERATIONS = {
    "Session/User": {"get", "create", "delete"},
    "Class": {"list", "get", "create", "update", "archive", "restore"},
    "Student": {"list", "get", "create", "update", "delete"},
    "StudentGroup": {"list", "create", "update", "delete"},
    "ImportPreview/ImportJob": {"get", "create", "confirm"},
    "Assignment": {"list", "get", "create", "update", "archive", "restore"},
    "PaperVersion": {"get", "upload"},
    "PaperPage": {"get", "update", "reorder"},
    "Question": {"get", "create", "update", "delete", "reorder"},
    "QuestionRegion": {"get", "create", "delete"},
    "RubricVersion": {"get", "update"},
    "RubricItem": {"get", "update"},
    "KnowledgePoint": {"get", "create", "update"},
    "RecognitionJob/RecognitionBlock": {"get", "create", "retry", "recognize", "confirm"},
    "GradingBatch": {"list", "get", "create", "archive", "upload", "bulk_accept", "regrade"},
    "Submission": {"list", "get", "split", "merge", "match", "recognize", "finalize"},
    "SubmissionPage": {"get", "retry", "reorder", "split", "merge"},
    "SubmissionRecognitionJob": {"get", "create", "retry", "recognize"},
    "StudentAnswer": {"get", "create", "update", "review"},
    "GradingJob/GradingResult": {"get", "create", "regrade"},
    "TeacherReview/ScoreRevision": {"get", "create", "update", "review"},
    "SubmissionScoreSnapshot": {"list", "get", "create", "finalize"},
    "GradeRelease/GradeReleaseItem": {"list", "get", "create", "readiness", "release"},
    "ReportJob": {"list", "get", "create", "retry", "download", "report_create"},
    "StoredFile": {"get", "upload", "metadata", "signed_url", "download", "delete"},
    "AnalyticsSnapshot": {"get", "create", "analytics_create", "drilldown"},
    "TeachingInsight": {"get", "create", "update", "confirm", "regenerate", "invalidate"},
}

IDENTITIES = {
    "owner",
    "cross_teacher",
    "unauthenticated",
    "disabled_teacher",
    "expired_or_revoked_session",
    "missing_or_invalid_csrf",
}


def create_user(email: str, *, status: Status = Status.active) -> None:
    with SessionLocal() as db:
        db.add(
            User(
                email=email,
                password_hash=hash_password("Synthetic-security-only!"),
                display_name="合成安全教师",
                status=status,
            )
        )
        db.commit()


def login(email: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/login", json={"email": email, "password": "Synthetic-security-only!"}
    )
    assert response.status_code == 200
    return client


def test_matrix_declares_every_resource_operation_and_identity() -> None:
    assert len(RESOURCE_OPERATIONS) == 27
    assert len(OPERATIONS) == 29
    assert len(IDENTITIES) == 6
    assert all(operations <= OPERATIONS for operations in RESOURCE_OPERATIONS.values())
    cells = [
        (resource, operation, operation in applicable)
        for resource, applicable in RESOURCE_OPERATIONS.items()
        for operation in OPERATIONS
    ]
    assert len(cells) == 783
    assert sum(applicable for _, _, applicable in cells) == 117
    assert sum(not applicable for _, _, applicable in cells) == 666


def test_every_business_route_uses_the_shared_session_and_csrf_boundary() -> None:
    public = {
        "/health",
        "/ready",
        "/auth/login",
        "/auth/password-reset/request",
        "/auth/password-reset/confirm",
    }

    def has_actor(dependant: object) -> bool:
        dependencies = getattr(dependant, "dependencies", [])
        return any(
            getattr(item, "call", None) is get_current_actor or has_actor(item)
            for item in dependencies
        )

    unprotected = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in public:
            continue
        if not has_actor(route.dependant):
            unprotected.append(f"{','.join(sorted(route.methods or set()))} {route.path}")
    assert unprotected == []


def test_authentication_session_csrf_and_list_isolation_matrix() -> None:
    settings = get_settings()
    old_demo = settings.demo_actor_enabled
    settings.demo_actor_enabled = False
    try:
        create_user("a@security-matrix.synthetic.invalid")
        create_user("b@security-matrix.synthetic.invalid")
        create_user("disabled@security-matrix.synthetic.invalid", status=Status.inactive)
        assert TestClient(app).get("/api/classes").status_code == 401
        assert (
            TestClient(app)
            .post(
                "/auth/login",
                json={
                    "email": "disabled@security-matrix.synthetic.invalid",
                    "password": "Synthetic-security-only!",
                },
            )
            .status_code
            == 401
        )

        owner, other = (
            login("a@security-matrix.synthetic.invalid"),
            login("b@security-matrix.synthetic.invalid"),
        )
        assert owner.post("/api/classes", json={"name": "missing csrf"}).status_code == 403
        assert (
            owner.post(
                "/api/classes", headers={"x-csrf-token": "wrong"}, json={"name": "wrong csrf"}
            ).status_code
            == 403
        )
        csrf = owner.cookies.get("ahamark_csrf") or ""
        created = owner.post(
            "/api/classes", headers={"x-csrf-token": csrf}, json={"name": "A 的班级"}
        )
        assert created.status_code == 201
        class_id = created.json()["id"]
        assert other.get(f"/api/classes/{class_id}").status_code == 404
        assert all(item["id"] != class_id for item in other.get("/api/classes").json()["items"])

        with SessionLocal() as db:
            session = db.scalar(
                select(UserSession)
                .join(User)
                .where(User.email == "a@security-matrix.synthetic.invalid")
            )
            assert session is not None
            session.expires_at = now_utc() - timedelta(seconds=1)
            db.commit()
        assert owner.get("/api/classes").status_code == 401
    finally:
        settings.demo_actor_enabled = old_demo


@pytest.mark.parametrize("method", ["get", "head"])
def test_read_only_methods_do_not_require_csrf(method: str) -> None:
    settings = get_settings()
    old_demo = settings.demo_actor_enabled
    settings.demo_actor_enabled = False
    try:
        create_user(f"{method}@security-matrix.synthetic.invalid")
        client = login(f"{method}@security-matrix.synthetic.invalid")
        response = getattr(client, method)("/api/classes")
        assert response.status_code == (200 if method == "get" else 405)
    finally:
        settings.demo_actor_enabled = old_demo
