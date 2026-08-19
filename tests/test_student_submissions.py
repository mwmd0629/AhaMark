import io
import uuid

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentParticipantSnapshot,
    AssignmentStatus,
    GradingBatch,
    Role,
    SchoolClass,
    Status,
    Student,
    Submission,
    SubmissionPage,
    User,
    UserRole,
    now_utc,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from test_assignments import FakeStorage


def png_bytes(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(output, format="PNG")
    return output.getvalue()


def test_linked_student_submits_versioned_files_to_teacher_grading_batch() -> None:
    storage = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        TestClient(app).get("/api/classes")
        with SessionLocal() as db:
            teacher = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
            assert teacher is not None
            role = Role(name="student", description="学生")
            account = User(
                username=f"submit-{uuid.uuid4().hex[:8]}",
                email=f"submit-{uuid.uuid4().hex[:8]}@example.com",
                display_name="提交学生",
                password_hash=hash_password("student-pass-123"),
                status=Status.active,
            )
            school_class = SchoolClass(
                owner_id=teacher.id,
                name="在线提交班",
                status=ArchiveStatus.active,
            )
            db.add_all([role, account, school_class])
            db.flush()
            student = Student(
                owner_id=teacher.id,
                user_id=account.id,
                student_number="ONLINE-1",
                name="提交学生",
            )
            assignment = Assignment(
                owner_id=teacher.id,
                title="在线作业",
                status=AssignmentStatus.published,
                published_at=now_utc(),
            )
            db.add_all([student, assignment])
            db.flush()
            db.add_all(
                [
                    UserRole(user_id=account.id, role_id=role.id),
                    AssignmentParticipantSnapshot(
                        assignment_id=assignment.id,
                        class_id=school_class.id,
                        student_id=student.id,
                        student_number=student.student_number,
                        student_name=student.name,
                        membership_joined_at=now_utc(),
                    ),
                ]
            )
            db.commit()
            assignment_id = assignment.id
            teacher_id = teacher.id
            email = account.email

        student_client = TestClient(app)
        login = student_client.post(
            "/auth/login", json={"email": email, "password": "student-pass-123"}
        )
        csrf = student_client.cookies.get("ahamark_csrf")
        assert login.status_code == 200 and csrf
        available = student_client.get("/api/student/open-assignments")
        assert available.status_code == 200, available.text
        assert available.json()[0]["assignment_id"] == str(assignment_id)
        assert available.json()[0]["attempts"] == []

        first_content = png_bytes("white")
        first = student_client.post(
            f"/api/student/open-assignments/{assignment_id}/submissions",
            headers={"x-csrf-token": csrf},
            files={"files": ("answer.png", first_content, "image/png")},
        )
        assert first.status_code == 201, first.text
        assert first.json()["attempt_number"] == 1
        second = student_client.post(
            f"/api/student/open-assignments/{assignment_id}/submissions",
            headers={"x-csrf-token": csrf},
            files={"files": ("answer-v2.png", png_bytes("blue"), "image/png")},
        )
        assert second.status_code == 201, second.text
        assert second.json()["attempt_number"] == 2

        refreshed = student_client.get("/api/student/open-assignments").json()[0]
        assert [item["attempt_number"] for item in refreshed["attempts"]] == [2, 1]
        with SessionLocal() as db:
            batch = db.scalar(
                select(GradingBatch).where(
                    GradingBatch.assignment_id == assignment_id,
                    GradingBatch.name == "学生在线提交",
                )
            )
            assert batch is not None and batch.owner_id == teacher_id
            assert batch.submission_count == 2
            submissions = list(
                db.scalars(
                    select(Submission)
                    .where(Submission.grading_batch_id == batch.id)
                    .order_by(Submission.attempt_number)
                )
            )
            assert [item.source for item in submissions] == ["student_upload", "student_upload"]
            assert db.scalar(select(func.count(SubmissionPage.id))) == 2
        assert len(storage.objects) == 2
    finally:
        app.dependency_overrides.clear()
