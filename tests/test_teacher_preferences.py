from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models import AuditLog, Role, SchoolClass, Status, User
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "secure-pass-123"


def create_account(username: str, role_name: str) -> User:
    with SessionLocal() as db:
        role = db.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            role = Role(name=role_name, description=f"synthetic {role_name}")
        user = User(
            username=username,
            email=f"{username}@ahamark.local",
            password_hash=hash_password(PASSWORD),
            display_name=f"{role_name} account",
            status=Status.active,
        )
        user.roles.append(role)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def login(username: str) -> tuple[TestClient, str]:
    client = TestClient(app)
    response = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    csrf = client.cookies.get("ahamark_csrf")
    assert csrf
    return client, csrf


def test_teacher_preferences_persist_with_version_and_tenant_checks() -> None:
    teacher = create_account("teacher-pref", "teacher")
    other = create_account("teacher-other", "teacher")
    with SessionLocal() as db:
        own_class = SchoolClass(owner_id=teacher.id, name="高一一班")
        foreign_class = SchoolClass(owner_id=other.id, name="其他教师班级")
        db.add_all([own_class, foreign_class])
        db.commit()
        own_class_id = str(own_class.id)
        foreign_class_id = str(foreign_class.id)

    client, csrf = login(teacher.username)
    initial = client.get("/auth/preferences")
    assert initial.status_code == 200
    assert initial.json()["revision"] == 0
    assert initial.json()["profile"]["username"] == teacher.username
    assert initial.json()["server_managed"]["ai_configuration_editable"] is False
    assert "api_key" not in str(initial.json()).lower()

    payload = {
        "expected_revision": 0,
        "display_name": "王老师",
        "preferences": {
            "default_class_id": own_class_id,
            "rubric_status_filter": "draft",
            "rubric_page_size": 50,
            "compact_rubric_cards": True,
        },
    }
    saved = client.put("/auth/preferences", headers={"x-csrf-token": csrf}, json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["profile"]["display_name"] == "王老师"
    assert saved.json()["preferences"] == payload["preferences"]

    with SessionLocal() as db:
        persisted_user = db.get(User, teacher.id)
        latest = db.scalar(
            select(AuditLog)
            .where(
                AuditLog.actor_id == teacher.id,
                AuditLog.action == "user_preferences.update",
            )
            .order_by(AuditLog.created_at.desc())
        )
        assert persisted_user is not None and persisted_user.display_name == "王老师"
        assert latest is not None and latest.metadata_["revision"] == 1

    stale = client.put("/auth/preferences", headers={"x-csrf-token": csrf}, json=payload)
    assert stale.status_code == 409
    assert stale.json()["code"] == "PREFERENCES_VERSION_CONFLICT"

    sensitive = client.put(
        "/auth/preferences",
        headers={"x-csrf-token": csrf},
        json={**payload, "expected_revision": 1, "openai_api_key": "forbidden"},
    )
    assert sensitive.status_code == 422

    wrong_tenant = client.put(
        "/auth/preferences",
        headers={"x-csrf-token": csrf},
        json={
            **payload,
            "expected_revision": 1,
            "preferences": {
                **payload["preferences"],
                "default_class_id": foreign_class_id,
            },
        },
    )
    assert wrong_tenant.status_code == 422
    assert wrong_tenant.json()["code"] == "DEFAULT_CLASS_NOT_AVAILABLE"


def test_student_cannot_use_teacher_preferences() -> None:
    student = create_account("student-pref", "student")
    client, _csrf = login(student.username)
    response = client.get("/auth/preferences")
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_ROLE_REQUIRED"
