import uuid

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


def test_bulk_import_creates_valid_rows_and_reports_row_errors_without_passwords() -> None:
    admin = create_admin_account("root-admin", "平台主管", PASSWORD)
    client = TestClient(app)
    csrf = login(client, admin.username)
    response = client.post(
        "/admin/accounts/bulk",
        headers={"x-csrf-token": csrf},
        json={
            "rows": [
                {
                    "username": "teacher-bulk-01",
                    "display_name": "批量教师",
                    "password": "bulk-pass-123",
                    "account_type": "teacher",
                },
                {
                    "username": "student-bulk-01",
                    "display_name": "批量学生",
                    "password": "bulk-pass-456",
                    "account_type": "student",
                },
                {
                    "username": "teacher-bulk-01",
                    "display_name": "重复教师",
                    "password": "bulk-pass-789",
                    "account_type": "teacher",
                },
                {
                    "username": "bad name",
                    "display_name": "错误账号",
                    "password": "bulk-pass-000",
                    "account_type": "student",
                },
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["requested_count"] == 4
    assert {item["username"] for item in body["created"]} == {
        "teacher-bulk-01",
        "student-bulk-01",
    }
    assert [item["row_number"] for item in body["errors"]] == [4, 5]
    assert "bulk-pass" not in response.text

    audit_response = client.get("/admin/accounts/audit")
    assert audit_response.status_code == 200
    audit_body = audit_response.json()
    assert audit_body["total"] == 3
    bulk_entry = next(
        item for item in audit_body["items"] if item["action"] == "admin.account.bulk_create"
    )
    assert bulk_entry["details"] == {
        "requested_count": 4,
        "created_count": 2,
        "error_count": 2,
    }
    assert "bulk-pass" not in audit_response.text


def test_bulk_import_rejects_admin_rows_and_non_admin_audit_access() -> None:
    admin = create_admin_account("root-admin", "平台主管", PASSWORD)
    teacher = create_teacher("plain-teacher", "普通教师", PASSWORD)
    admin_client, teacher_client = TestClient(app), TestClient(app)
    admin_csrf = login(admin_client, admin.username)
    login(teacher_client, teacher.username)
    assert (
        admin_client.post(
            "/admin/accounts/bulk",
            headers={"x-csrf-token": admin_csrf},
            json={
                "rows": [
                    {
                        "username": "bulk-admin",
                        "display_name": "批量管理员",
                        "password": PASSWORD,
                        "account_type": "admin",
                    }
                ]
            },
        ).status_code
        == 422
    )
    assert teacher_client.get("/admin/accounts/audit").status_code == 403


def test_bulk_actions_apply_partial_results_revoke_sessions_and_keep_an_admin() -> None:
    admin = create_admin_account("root-admin", "平台主管", PASSWORD)
    second_admin = create_admin_account("backup-admin", "备用管理员", PASSWORD)
    teacher = create_teacher("bulk-action-teacher", "批量教师", PASSWORD)
    admin_client, teacher_client = TestClient(app), TestClient(app)
    csrf = login(admin_client, admin.username)
    login(teacher_client, teacher.username)
    missing_id = uuid.uuid4()

    unconfirmed = admin_client.post(
        "/admin/accounts/bulk-actions",
        headers={"x-csrf-token": csrf},
        json={
            "account_ids": [str(teacher.id)],
            "action": "deactivate",
            "confirmed": False,
        },
    )
    assert unconfirmed.status_code == 422

    response = admin_client.post(
        "/admin/accounts/bulk-actions",
        headers={"x-csrf-token": csrf},
        json={
            "account_ids": [str(teacher.id), str(admin.id), str(missing_id)],
            "action": "deactivate",
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requested_count"] == 3
    assert body["processed"] == [
        {
            "account_id": str(teacher.id),
            "username": teacher.username,
            "status": "inactive",
            "changed": True,
            "sessions_revoked": 1,
        }
    ]
    assert {item["account_id"] for item in body["errors"]} == {
        str(admin.id),
        str(missing_id),
    }
    assert teacher_client.get("/auth/me").status_code == 401

    activate = admin_client.post(
        "/admin/accounts/bulk-actions",
        headers={"x-csrf-token": csrf},
        json={
            "account_ids": [str(teacher.id)],
            "action": "activate",
            "confirmed": True,
        },
    )
    assert activate.status_code == 200
    assert activate.json()["processed"][0]["status"] == "active"

    deactivate_backup = admin_client.post(
        "/admin/accounts/bulk-actions",
        headers={"x-csrf-token": csrf},
        json={
            "account_ids": [str(second_admin.id)],
            "action": "deactivate",
            "confirmed": True,
        },
    )
    assert deactivate_backup.status_code == 200
    assert deactivate_backup.json()["processed"][0]["status"] == "inactive"

    with SessionLocal() as db:
        actions = set(db.scalars(select(AuditLog.action)).all())
        assert "admin.account.bulk_deactivate" in actions
        assert "admin.account.bulk_activate" in actions
        assert "admin.account.bulk_action" in actions


def test_account_export_is_filtered_utf8_and_spreadsheet_safe() -> None:
    admin = create_admin_account("root-admin", "平台主管", PASSWORD)
    create_teacher("formula-teacher", '=HYPERLINK("https://invalid")', PASSWORD)
    client = TestClient(app)
    login(client, admin.username)

    response = client.get("/admin/accounts/export.csv?account_type=teacher")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="ahamark-accounts.csv"'
    assert response.content.startswith(b"\xef\xbb\xbf")
    text = response.content.decode("utf-8-sig")
    assert "username,display_name,account_type,status,active_sessions,last_seen_at" in text
    assert "formula-teacher" in text
    assert "'=HYPERLINK" in text
    assert "root-admin" not in text
    assert PASSWORD not in text


def test_security_overview_tracks_known_failures_and_revokes_selected_session() -> None:
    admin = create_admin_account("root-admin", "平台主管", PASSWORD)
    teacher = create_teacher("session-teacher", "会话教师", PASSWORD)
    admin_client = TestClient(app)
    csrf = login(admin_client, admin.username)
    assert (
        TestClient(app).post(
            "/auth/login",
            json={"username": teacher.username, "password": "wrong-pass-123"},
        ).status_code
        == 401
    )
    teacher_clients = [TestClient(app), TestClient(app)]
    for client in teacher_clients:
        login(client, teacher.username)

    overview = admin_client.get("/admin/accounts/security")
    assert overview.status_code == 200
    assert "wrong-pass-123" not in overview.text
    body = overview.json()
    assert body["failed_logins_24h"] == 1
    assert body["active_sessions"] == 3
    assert body["accounts_with_multiple_sessions"] == 1
    teacher_sessions = [
        session for session in body["sessions"] if session["username"] == teacher.username
    ]
    assert len(teacher_sessions) == 2
    current_session = next(session for session in body["sessions"] if session["is_current"])
    assert (
        admin_client.post(
            f"/admin/accounts/sessions/{current_session['id']}/revoke",
            headers={"x-csrf-token": csrf},
        ).status_code
        == 409
    )

    revoked = admin_client.post(
        f"/admin/accounts/sessions/{teacher_sessions[0]['id']}/revoke",
        headers={"x-csrf-token": csrf},
    )
    assert revoked.status_code == 200
    assert revoked.json()["username"] == teacher.username
    assert sum(client.get("/auth/me").status_code == 401 for client in teacher_clients) == 1
    with SessionLocal() as db:
        failed = db.scalar(select(AuditLog).where(AuditLog.action == "auth.login.failed"))
        assert failed is not None
        assert failed.resource_id == str(teacher.id)
        assert "wrong-pass-123" not in str(failed.metadata_)


def test_non_admin_cannot_use_bulk_export_or_security_operations() -> None:
    teacher = create_teacher("plain-teacher", "普通教师", PASSWORD)
    client = TestClient(app)
    csrf = login(client, teacher.username)
    assert client.get("/admin/accounts/export.csv").status_code == 403
    assert client.get("/admin/accounts/security").status_code == 403
    assert (
        client.post(
            "/admin/accounts/bulk-actions",
            headers={"x-csrf-token": csrf},
            json={
                "account_ids": [str(teacher.id)],
                "action": "revoke_sessions",
                "confirmed": True,
            },
        ).status_code
        == 403
    )
