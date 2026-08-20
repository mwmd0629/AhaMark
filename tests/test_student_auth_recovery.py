import uuid

from app.api.actor import CurrentActor
from app.api.auth import hash_password
from app.api.student_portal import StudentAccountInput, create_student_account_link
from app.db.session import SessionLocal
from app.main import app
from app.models import AuthEmailChallenge, Status, Student, User, UserSession
from fastapi.testclient import TestClient
from sqlalchemy import select


def create_linked_student(
    *,
    student_number: str = "STU-RECOVERY-001",
    recovery_email: str | None = "student@example.com",
) -> tuple[User, Student]:
    with SessionLocal() as db:
        teacher = User(
            email=f"teacher-{uuid.uuid4()}@example.com",
            password_hash=hash_password("Teacher-password-123"),
            display_name="测试教师",
            status=Status.active,
        )
        db.add(teacher)
        db.flush()
        student = Student(
            owner_id=teacher.id,
            student_number=student_number,
            name="测试学生",
            email=recovery_email,
        )
        db.add(student)
        db.commit()
        db.refresh(student)
        result = create_student_account_link(
            student.id,
            StudentAccountInput(
                recovery_email=recovery_email,
                temporary_password="Temporary-password-123",
            ),
            db,
            CurrentActor(teacher.id, teacher.email),
        )
        user = db.get(User, uuid.UUID(result["user_id"]))
        assert user is not None
        db.expunge(user)
        db.expunge(student)
        return user, student


def login(client: TestClient, identifier: str, password: str) -> dict[str, object]:
    response = client.post(
        "/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_student_uses_student_number_instead_of_recovery_email_to_login() -> None:
    user, student = create_linked_student()
    client = TestClient(app)

    logged_in = login(client, student.student_number, "Temporary-password-123")
    assert logged_in["login_name"] == student.student_number.casefold()
    assert logged_in["must_change_password"] is True
    assert logged_in["landing_surface"] == "change_password"
    assert logged_in["recovery_email_verified"] is False

    email_login = TestClient(app).post(
        "/auth/login",
        json={"identifier": user.email, "password": "Temporary-password-123"},
    )
    assert email_login.status_code == 401
    assert email_login.json()["message"] == "账号或密码错误"


def test_student_account_can_be_created_without_recovery_email(monkeypatch) -> None:
    user, student = create_linked_student(student_number="STU-NO-RECOVERY-001", recovery_email=None)
    monkeypatch.setattr("app.api.auth.send_auth_code", lambda *_args: True)
    assert user.email is None
    assert student.email is None

    client = TestClient(app)
    logged_in = login(client, student.student_number, "Temporary-password-123")
    assert logged_in["email"] is None
    assert logged_in["recovery_email"] is None
    assert logged_in["recovery_email_verified"] is False

    csrf = client.cookies.get("ahamark_csrf") or ""
    verification = client.post(
        "/auth/email-verification/request",
        headers={"x-csrf-token": csrf},
    )
    assert verification.status_code == 409
    assert verification.json()["message"] == "请先设置安全邮箱"

    reset = TestClient(app).post(
        "/auth/password-reset/request",
        json={
            "identifier": student.student_number,
            "recovery_email": "unknown@example.com",
        },
    )
    assert reset.status_code == 202
    assert reset.json()["development_code"] is None


def test_student_can_set_replace_and_clear_recovery_email(monkeypatch) -> None:
    user, student = create_linked_student(
        student_number="STU-RECOVERY-UPDATE-001", recovery_email=None
    )
    monkeypatch.setattr("app.api.auth.send_auth_code", lambda *_args: True)
    client = TestClient(app)
    login(client, student.student_number, "Temporary-password-123")
    csrf = client.cookies.get("ahamark_csrf") or ""
    headers = {"x-csrf-token": csrf}

    wrong_password = client.put(
        "/auth/recovery-email",
        headers=headers,
        json={
            "recovery_email": "first-recovery@example.com",
            "current_password": "Wrong-password-123",
        },
    )
    assert wrong_password.status_code == 401

    configured = client.put(
        "/auth/recovery-email",
        headers=headers,
        json={
            "recovery_email": "FIRST-RECOVERY@EXAMPLE.COM",
            "current_password": "Temporary-password-123",
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["recovery_email"] == "first-recovery@example.com"
    assert configured.json()["recovery_email_verified"] is False

    requested = client.post("/auth/email-verification/request", headers=headers)
    assert requested.status_code == 200, requested.text
    old_challenge = requested.json()

    replaced = client.put(
        "/auth/recovery-email",
        headers=headers,
        json={
            "recovery_email": "second-recovery@example.com",
            "current_password": "Temporary-password-123",
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["recovery_email"] == "second-recovery@example.com"
    assert replaced.json()["recovery_email_verified"] is False
    stale_confirmation = client.post(
        "/auth/email-verification/confirm",
        headers=headers,
        json={
            "challenge_id": old_challenge["challenge_id"],
            "code": old_challenge["development_code"],
        },
    )
    assert stale_confirmation.status_code == 422

    current_challenge = client.post("/auth/email-verification/request", headers=headers).json()
    confirmed = client.post(
        "/auth/email-verification/confirm",
        headers=headers,
        json={
            "challenge_id": current_challenge["challenge_id"],
            "code": current_challenge["development_code"],
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["recovery_email_verified"] is True

    cleared = client.put(
        "/auth/recovery-email",
        headers=headers,
        json={
            "recovery_email": None,
            "current_password": "Temporary-password-123",
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["email"] is None
    assert cleared.json()["recovery_email"] is None
    assert cleared.json()["recovery_email_verified"] is False

    with SessionLocal() as db:
        stored_user = db.get(User, user.id)
        stored_student = db.get(Student, student.id)
        assert stored_user is not None and stored_user.email is None
        assert stored_user.email_verified_at is None
        # Recovery email is account-owned, independent of the teacher-maintained profile.
        assert stored_student is not None and stored_student.email is None
        challenges = db.scalars(
            select(AuthEmailChallenge).where(AuthEmailChallenge.user_id == user.id)
        ).all()
        assert challenges
        assert all(challenge.consumed_at is not None for challenge in challenges)


def test_recovery_email_must_be_unique_across_accounts() -> None:
    first_user, _ = create_linked_student(
        student_number="STU-RECOVERY-UNIQUE-001",
        recovery_email="unique-recovery@example.com",
    )
    _second_user, second_student = create_linked_student(
        student_number="STU-RECOVERY-UNIQUE-002",
        recovery_email=None,
    )
    client = TestClient(app)
    login(client, second_student.student_number, "Temporary-password-123")
    response = client.put(
        "/auth/recovery-email",
        headers={"x-csrf-token": client.cookies.get("ahamark_csrf") or ""},
        json={
            "recovery_email": first_user.email,
            "current_password": "Temporary-password-123",
        },
    )
    assert response.status_code == 409
    assert response.json()["message"] == "该安全邮箱已被其他账号使用"


def test_verified_recovery_email_resets_password_and_revokes_sessions(monkeypatch) -> None:
    user, student = create_linked_student(
        student_number="STU-RECOVERY-002", recovery_email="recovery-two@example.com"
    )
    monkeypatch.setattr("app.api.auth.send_auth_code", lambda *_args: True)

    primary = TestClient(app)
    login(primary, student.student_number, "Temporary-password-123")
    csrf = primary.cookies.get("ahamark_csrf") or ""
    changed = primary.post(
        "/auth/change-password",
        headers={"x-csrf-token": csrf},
        json={
            "current_password": "Temporary-password-123",
            "new_password": "Student-password-456",
        },
    )
    assert changed.status_code == 200

    assert primary.post("/auth/email-verification/request").status_code == 403
    requested = primary.post(
        "/auth/email-verification/request",
        headers={"x-csrf-token": csrf},
        json={},
    )
    assert requested.status_code == 200, requested.text
    verification = requested.json()
    assert verification["development_code"]
    confirmed = primary.post(
        "/auth/email-verification/confirm",
        headers={"x-csrf-token": csrf},
        json={
            "challenge_id": verification["challenge_id"],
            "code": verification["development_code"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["recovery_email_verified"] is True

    secondary = TestClient(app)
    login(secondary, student.student_number, "Student-password-456")
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(UserSession)
                .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
                .limit(1)
            )
            is not None
        )

    reset_request = TestClient(app).post(
        "/auth/password-reset/request",
        json={
            "identifier": student.student_number,
            "recovery_email": user.email,
        },
    )
    assert reset_request.status_code == 202, reset_request.text
    reset = reset_request.json()
    assert reset["development_code"]
    reset_confirm = TestClient(app).post(
        "/auth/password-reset/confirm",
        json={
            "challenge_id": reset["challenge_id"],
            "code": reset["development_code"],
            "new_password": "Recovered-password-789",
        },
    )
    assert reset_confirm.status_code == 200, reset_confirm.text
    assert primary.get("/auth/me").status_code == 401
    assert secondary.get("/auth/me").status_code == 401
    assert (
        TestClient(app)
        .post(
            "/auth/login",
            json={
                "identifier": student.student_number,
                "password": "Student-password-456",
            },
        )
        .status_code
        == 401
    )
    login(TestClient(app), student.student_number, "Recovered-password-789")

    with SessionLocal() as db:
        challenges = db.scalars(
            select(AuthEmailChallenge).where(AuthEmailChallenge.user_id == user.id)
        ).all()
        assert challenges
        plaintext_codes = {verification["development_code"], reset["development_code"]}
        assert all(item.code_hash not in plaintext_codes for item in challenges)


def test_password_reset_request_does_not_reveal_unknown_or_unverified_account(monkeypatch) -> None:
    _user, student = create_linked_student(
        student_number="STU-RECOVERY-003", recovery_email="recovery-three@example.com"
    )
    monkeypatch.setattr("app.api.auth.send_auth_code", lambda *_args: True)

    known = TestClient(app).post(
        "/auth/password-reset/request",
        json={
            "identifier": student.student_number,
            "recovery_email": "recovery-three@example.com",
        },
    )
    unknown = TestClient(app).post(
        "/auth/password-reset/request",
        json={
            "identifier": "UNKNOWN-STUDENT",
            "recovery_email": "nobody@example.com",
        },
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json()["message"] == unknown.json()["message"]
    assert known.json()["development_code"] is None
    assert unknown.json()["development_code"] is None


def test_student_login_name_is_globally_unique_across_teachers() -> None:
    create_linked_student(student_number="GLOBAL-001", recovery_email="global-first@example.com")
    with SessionLocal() as db:
        teacher = User(
            email="global-second-teacher@example.com",
            password_hash=hash_password("Teacher-password-123"),
            display_name="第二位教师",
            status=Status.active,
        )
        db.add(teacher)
        db.flush()
        student = Student(
            owner_id=teacher.id,
            student_number="global-001",
            name="冲突学生",
            email="global-second@example.com",
        )
        db.add(student)
        db.commit()

        from app.api.domain import ApiProblem

        try:
            create_student_account_link(
                student.id,
                StudentAccountInput(
                    recovery_email=student.email or "",
                    temporary_password="Temporary-password-123",
                ),
                db,
                CurrentActor(teacher.id, teacher.email),
            )
        except ApiProblem as exc:
            assert exc.code == "STUDENT_LOGIN_ID_CONFLICT"
        else:  # pragma: no cover - documents the safety boundary
            raise AssertionError("duplicate global student login name was accepted")
