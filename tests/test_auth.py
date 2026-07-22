from datetime import timedelta

from app.api.auth import hash_password
from app.cli.create_teacher import create_teacher as initialize_teacher
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import Status, User, UserSession, now_utc
from fastapi.testclient import TestClient
from sqlalchemy import select


def create_teacher(email: str = "teacher@example.com", password: str = "secure-pass-123") -> User:
    with SessionLocal() as db:
        user = User(
            email=email,
            password_hash=hash_password(password),
            display_name="测试教师",
            status=Status.active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def test_login_me_csrf_logout_and_expiry() -> None:
    create_teacher()
    client = TestClient(app)
    login = client.post(
        "/auth/login", json={"email": "teacher@example.com", "password": "secure-pass-123"}
    )
    assert login.status_code == 200
    assert (
        "HttpOnly" in login.headers["set-cookie"] and "SameSite=lax" in login.headers["set-cookie"]
    )
    assert client.get("/auth/me").json()["email"] == "teacher@example.com"
    assert client.post("/auth/logout").status_code == 403
    csrf = client.cookies.get("ahamark_csrf")
    assert csrf and client.post("/auth/logout", headers={"x-csrf-token": csrf}).status_code == 204
    assert client.get("/auth/me").status_code == 401

    login = client.post(
        "/auth/login", json={"email": "teacher@example.com", "password": "secure-pass-123"}
    )
    with SessionLocal() as db:
        session = db.scalar(select(UserSession).where(UserSession.revoked_at.is_(None)))
        assert session is not None
        session.expires_at = now_utc() - timedelta(seconds=1)
        db.commit()
    assert client.get("/auth/me").status_code == 401


def test_production_never_falls_back_to_demo_actor() -> None:
    settings = get_settings()
    old_env, old_demo = settings.app_env, settings.demo_actor_enabled
    settings.app_env, settings.demo_actor_enabled = "production", True
    try:
        assert TestClient(app).get("/api/classes").status_code == 401
    finally:
        settings.app_env, settings.demo_actor_enabled = old_env, old_demo


def test_authenticated_teachers_are_isolated() -> None:
    create_teacher("one@example.com")
    create_teacher("two@example.com")
    one, two = TestClient(app), TestClient(app)
    for client, email in [(one, "one@example.com"), (two, "two@example.com")]:
        assert (
            client.post(
                "/auth/login", json={"email": email, "password": "secure-pass-123"}
            ).status_code
            == 200
        )
    csrf = one.cookies.get("ahamark_csrf")
    created = one.post(
        "/api/classes", headers={"x-csrf-token": csrf or ""}, json={"name": "隔离班级"}
    )
    assert created.status_code == 201
    assert two.get(f"/api/classes/{created.json()['id']}").status_code == 404


def test_admin_can_initialize_teacher_without_storing_plaintext() -> None:
    user = initialize_teacher("NEW@EXAMPLE.COM", "新教师", "not-plain-password")
    assert user.email == "new@example.com"
    assert user.password_hash != "not-plain-password"
    assert user.password_hash.startswith("scrypt$")
