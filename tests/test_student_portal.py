import uuid

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    GradeRelease,
    GradeReleaseItem,
    Role,
    Status,
    Student,
    Submission,
    User,
    UserRole,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_confirm_results_contract import _confirm, _confirmable_case, _readiness


def _student_account(email: str = "student@example.com") -> User:
    with SessionLocal() as db:
        role = Role(name="student", description="学生端账号")
        user = User(
            email=email,
            display_name="合成学生账号",
            password_hash=hash_password("student-pass-123"),
            status=Status.active,
        )
        db.add_all([role, user])
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def test_student_only_sees_explicitly_linked_and_published_formal_grade() -> None:
    with _confirmable_case() as case:
        submission = case.db.get(Submission, case.submission_id)
        assert submission is not None and submission.student_id is not None
        student = case.db.get(Student, submission.student_id)
        assert student is not None
        account = _student_account()
        student.email = account.email
        case.db.commit()

        linked = TestClient(app).post(f"/api/students/{student.id}/account-link")
        assert linked.status_code == 200, linked.text
        assert linked.json()["account_linked"] is True

        readiness = _readiness(case)
        confirmed = _confirm(
            case,
            key=f"student-portal-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert confirmed.status_code == 201, confirmed.text
        release_id = confirmed.json()["grade_release_id"]

        student_client = TestClient(app)
        login = student_client.post(
            "/auth/login",
            json={"email": account.email, "password": "student-pass-123"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["roles"] == ["student"]
        assert student_client.get("/api/student/me").status_code == 200
        assert student_client.get("/api/student/assignments").json() == []
        assert student_client.get(f"/api/student/assignments/{release_id}").status_code == 404

        published = TestClient(app).post(f"/api/grade-releases/{release_id}/publish-to-students")
        assert published.status_code == 200, published.text
        assert published.json()["student_visible"] is True
        repeated = TestClient(app).post(f"/api/grade-releases/{release_id}/publish-to-students")
        assert repeated.status_code == 200
        assert repeated.json()["student_visible_at"] == published.json()["student_visible_at"]

        assignments = student_client.get("/api/student/assignments")
        assert assignments.status_code == 200, assignments.text
        assert len(assignments.json()) == 1
        assert assignments.json()[0]["release_id"] == release_id

        detail = student_client.get(f"/api/student/assignments/{release_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["student_id"] == str(student.id)
        assert detail.json()["questions"][0]["score"] == detail.json()["total_score"]
        assert detail.json()["questions"][0]["max_score"] == detail.json()["max_score"]
        assert "provider" not in detail.text.lower()

        report = student_client.get(f"/api/student/assignments/{release_id}/report.pdf")
        assert report.status_code == 200
        assert report.headers["content-type"] == "application/pdf"
        assert report.content.startswith(b"%PDF")

        with SessionLocal() as db:
            release = db.get(GradeRelease, uuid.UUID(release_id))
            assert release is not None
            visible_at = release.student_visible_at
        assert visible_at is not None


def test_older_release_cannot_be_published_after_newer_formal_version() -> None:
    with _confirmable_case() as case:
        submission = case.db.get(Submission, case.submission_id)
        assert submission is not None and submission.student_id is not None
        student = case.db.get(Student, submission.student_id)
        assert student is not None
        account = _student_account("out-of-order@example.com")
        student.email = account.email
        case.db.commit()
        assert TestClient(app).post(f"/api/students/{student.id}/account-link").status_code == 200

        readiness = _readiness(case)
        confirmed = _confirm(
            case,
            key=f"out-of-order-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert confirmed.status_code == 201, confirmed.text
        release_v1 = case.db.get(GradeRelease, uuid.UUID(confirmed.json()["grade_release_id"]))
        assert release_v1 is not None
        item_v1 = case.db.scalar(
            select(GradeReleaseItem).where(GradeReleaseItem.grade_release_id == release_v1.id)
        )
        assert item_v1 is not None
        release_v2 = GradeRelease(
            owner_id=release_v1.owner_id,
            assignment_id=release_v1.assignment_id,
            class_id=release_v1.class_id,
            version=2,
            status="released",
            release_mode=release_v1.release_mode,
            released_at=release_v1.released_at,
            created_by=release_v1.created_by,
            notes="synthetic newer formal version",
        )
        case.db.add(release_v2)
        case.db.flush()
        case.db.add(
            GradeReleaseItem(
                grade_release_id=release_v2.id,
                student_id=item_v1.student_id,
                submission_id=item_v1.submission_id,
                score_snapshot_id=item_v1.score_snapshot_id,
            )
        )
        case.db.commit()

        publish_v2 = TestClient(app).post(
            f"/api/grade-releases/{release_v2.id}/publish-to-students"
        )
        assert publish_v2.status_code == 200, publish_v2.text
        publish_v1 = TestClient(app).post(
            f"/api/grade-releases/{release_v1.id}/publish-to-students"
        )
        assert publish_v1.status_code == 409
        assert publish_v1.json()["code"] == "GRADE_RELEASE_SUPERSEDED"

        student_client = TestClient(app)
        assert (
            student_client.post(
                "/auth/login",
                json={"email": account.email, "password": "student-pass-123"},
            ).status_code
            == 200
        )
        assignments = student_client.get("/api/student/assignments")
        assert assignments.status_code == 200
        assert [item["release_id"] for item in assignments.json()] == [str(release_v2.id)]


def test_unlinked_account_cannot_probe_student_portal() -> None:
    account = _student_account("unlinked@example.com")
    student_client = TestClient(app)
    assert (
        student_client.post(
            "/auth/login",
            json={"email": account.email, "password": "student-pass-123"},
        ).status_code
        == 200
    )
    response = student_client.get("/api/student/assignments")
    assert response.status_code == 403
    assert response.json()["code"] == "STUDENT_ACCOUNT_NOT_LINKED"


def test_teacher_cannot_link_an_account_without_student_role() -> None:
    with SessionLocal() as db:
        teacher = User(
            email="not-student@example.com",
            display_name="非学生账号",
            password_hash=hash_password("teacher-pass-123"),
            status=Status.active,
        )
        owner = User(
            email="owner@example.com",
            display_name="档案所有者",
            password_hash=hash_password("owner-pass-123"),
            status=Status.active,
        )
        db.add_all([teacher, owner])
        db.flush()
        student = Student(
            owner_id=owner.id,
            student_number="S-1",
            name="合成档案",
            email=teacher.email,
        )
        db.add(student)
        db.commit()
        student_id = student.id

    owner_client = TestClient(app)
    login = owner_client.post(
        "/auth/login",
        json={"email": "owner@example.com", "password": "owner-pass-123"},
    )
    csrf = owner_client.cookies.get("ahamark_csrf")
    assert login.status_code == 200 and csrf
    response = owner_client.post(
        f"/api/students/{student_id}/account-link",
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ACCOUNT_NOT_STUDENT"
