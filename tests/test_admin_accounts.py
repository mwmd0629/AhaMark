from app.api.auth import verify_password
from app.cli.create_admin import create_admin_account
from app.cli.create_teacher import create_teacher
from app.db.session import SessionLocal
from app.main import app
from app.models import AuditLog, Role, User, UserSession
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "secure-pass-123"


def login(client: TestClient, username: str, password: str = PASSWORD) -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    csrf = client.cookies.get("ahamark_csrf")
    assert csrf
    return csrf


def test_admin_lists_and_creates_all_three_account_types() -> None:
    admin = create_admin_account("root-admin", "平台主管", PASSWORD)
    client = TestClient(app)
    csrf = login(client, admin.username)

    for account_type in ("teacher", "student", "admin"):
        response = client.post(
            "/admin/accounts",
            headers={"x-csrf-token": csrf},
            json={
                "username": f"new-{account_type}",
                "display_name": f"新{account_type}",
                "password": "initial-pass-456",
                "account_type": account_type,
            },
        )
        assert response.status_code == 201
        assert response.json()["account_type"] == account_type
        assert "password" not in response.text

    listing = client.get("/admin/accounts")
    assert listing.status_code == 200
    body = listing.json()
    assert body["summary"] == {
        "teacher": {"total": 1, "active": 1},
        "student": {"total": 1, "active": 1},
        "admin": {"total": 2, "active": 2},
    }
    assert {item["username"] for item in body["items"]} == {
        "root-admin",
        "new-teacher",
        "new-student",
        "new-admin",
    }

    with SessionLocal() as db:
        created = db.scalar(select(User).where(User.username == "new-teacher"))
        assert created is not None
        assert not verify_password("wrong-password", created.password_hash)
        assert verify_password("initial-pass-456", created.password_hash)
        entries = list(
            db.scalars(select(AuditLog).where(AuditLog.action == "admin.account.create"))
        )
        assert len(entries) == 3
        assert "initial-pass-456" not in "".join(str(entry.metadata_) for entry in entries)


def test_non_admin_cannot_read_or_mutate_accounts() -> None:
    teacher = create_teacher("plain-teacher", "普通教师", PASSWORD)
    client = TestClient(app)
    csrf = login(client, teacher.username)
    assert client.get("/admin/accounts").status_code == 403
    assert (
        client.post(
            "/admin/accounts",
            headers={"x-csrf-token": csrf},
            json={
                "username": "not-allowed",
                "display_name": "不应创建",
                "password": PASSWORD,
                "account_type": "admin",
            },
        ).status_code
        == 403
    )


def test_password_reset_and_disable_revoke_existing_sessions() -> None:
    admin = create_admin_account("root-admin", "平台主管", PASSWORD)
    teacher = create_teacher("managed-teacher", "被管理教师", PASSWORD)
    admin_client, teacher_client = TestClient(app), TestClient(app)
    admin_csrf = login(admin_client, admin.username)
    login(teacher_client, teacher.username)

    reset = admin_client.post(
        f"/admin/accounts/{teacher.id}/reset-password",
        headers={"x-csrf-token": admin_csrf},
        json={"password": "replacement-pass-789"},
    )
    assert reset.status_code == 200
    assert reset.json()["sessions_revoked"] == 1
    assert teacher_client.get("/auth/me").status_code == 401
    assert (
        TestClient(app).post(
            "/auth/login", json={"username": teacher.username, "password": PASSWORD}
        ).status_code
        == 401
    )

    fresh_teacher = TestClient(app)
    login(fresh_teacher, teacher.username, "replacement-pass-789")
    disabled = admin_client.patch(
        f"/admin/accounts/{teacher.id}",
        headers={"x-csrf-token": admin_csrf},
        json={"status": "inactive"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "inactive"
    assert fresh_teacher.get("/auth/me").status_code == 401


def test_admin_self_lockout_and_last_admin_guards() -> None:
    admin = create_admin_account("root-admin", "平台主管", PASSWORD)
    client = TestClient(app)
    csrf = login(client, admin.username)
    assert (
        client.patch(
            f"/admin/accounts/{admin.id}",
            headers={"x-csrf-token": csrf},
            json={"status": "inactive"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/admin/accounts/{admin.id}/reset-password",
            headers={"x-csrf-token": csrf},
            json={"password": "another-pass-456"},
        ).status_code
        == 409
    )


def test_cli_accounts_receive_explicit_exclusive_roles() -> None:
    teacher = create_teacher("teacher-role", "教师", PASSWORD)
    admin = create_admin_account("admin-role", "管理员", PASSWORD)
    with SessionLocal() as db:
        teacher_roles = set(
            db.scalars(
                select(Role.name).join(Role.users).where(User.id == teacher.id)
            ).all()
        )
        admin_roles = set(
            db.scalars(select(Role.name).join(Role.users).where(User.id == admin.id)).all()
        )
        assert teacher_roles == {"teacher"}
        assert admin_roles == {"admin"}
        assert db.scalar(
            select(UserSession).where(UserSession.user_id == teacher.id)
        ) is None
