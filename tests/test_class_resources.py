import io
import uuid

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    ArchiveStatus,
    ClassResource,
    ClassStudent,
    MembershipStatus,
    PaperPage,
    Role,
    SchoolClass,
    Status,
    Student,
    User,
    UserRole,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from test_assignments import FakeStorage

client = TestClient(app)


def png_bytes(color: str = "white") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(output, format="PNG")
    return output.getvalue()


def setup_class_and_assignment() -> tuple[str, str]:
    client.get("/api/classes")
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
        assert actor is not None
        klass = SchoolClass(owner_id=actor.id, name="资料库测试班", status=ArchiveStatus.active)
        db.add(klass)
        db.commit()
        class_id = str(klass.id)
    response = client.post(
        "/api/assignments",
        json={"title": "资料作业", "total_score": 10, "class_ids": [class_id]},
    )
    assert response.status_code == 201
    return class_id, response.json()["id"]


def test_upload_list_and_copy_class_resource_to_draft_assignment() -> None:
    storage = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        class_id, assignment_id = setup_class_and_assignment()
        uploaded = client.post(
            f"/api/classes/{class_id}/resources",
            data={"title": "第一章习题", "resource_type": "exercise"},
            files={"file": ("exercise.png", png_bytes(), "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        resource = uploaded.json()
        assert resource["title"] == "第一章习题"
        assert resource["page_count"] == 1
        assert len(client.get(f"/api/classes/{class_id}/resources").json()) == 1
        assert (
            len(client.get(f"/api/assignments/{assignment_id}/available-class-resources").json())
            == 1
        )

        copied = client.post(
            f"/api/assignments/{assignment_id}/class-resources",
            json={"resource_ids": [resource["id"]]},
        )
        assert copied.status_code == 200, copied.text
        assert copied.json() == {"files_created": 1, "pages_created": 1}
        with SessionLocal() as db:
            assert db.scalar(select(func.count(ClassResource.id))) == 1
            assert db.scalar(select(func.count(PaperPage.id))) == 1
        assert len(storage.objects) == 2
    finally:
        app.dependency_overrides.clear()


def test_duplicate_and_cross_class_resource_selection_are_rejected() -> None:
    storage = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        class_id, assignment_id = setup_class_and_assignment()
        content = png_bytes()
        uploaded = client.post(
            f"/api/classes/{class_id}/resources",
            data={"resource_type": "exercise"},
            files={"file": ("exercise.png", content, "image/png")},
        )
        assert uploaded.status_code == 201
        duplicate = client.post(
            f"/api/classes/{class_id}/resources",
            data={"resource_type": "exercise"},
            files={"file": ("copy.png", content, "image/png")},
        )
        assert duplicate.status_code == 409
        with SessionLocal() as db:
            actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
            assert actor is not None
            other = SchoolClass(owner_id=actor.id, name="其他资料班", status=ArchiveStatus.active)
            db.add(other)
            db.commit()
            other_id = str(other.id)
        foreign = client.post(
            f"/api/classes/{other_id}/resources",
            data={"resource_type": "handout"},
            files={"file": ("other.png", png_bytes("black"), "image/png")},
        )
        assert foreign.status_code == 201
        rejected = client.post(
            f"/api/assignments/{assignment_id}/class-resources",
            json={"resource_ids": [foreign.json()["id"]]},
        )
        assert rejected.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_teacher_explicitly_publishes_resource_to_linked_active_student() -> None:
    storage = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        class_id, _assignment_id = setup_class_and_assignment()
        uploaded = client.post(
            f"/api/classes/{class_id}/resources",
            data={"title": "学生讲义", "resource_type": "handout"},
            files={"file": ("handout.png", png_bytes("blue"), "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        resource = uploaded.json()
        assert resource["student_visible"] is False
        with SessionLocal() as db:
            teacher = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
            assert teacher is not None
            role = Role(name="student", description="学生")
            account = User(
                username=f"resource-student-{uuid.uuid4().hex[:8]}",
                email=f"resource-{uuid.uuid4().hex[:8]}@example.com",
                display_name="资料学生",
                password_hash=hash_password("student-pass-123"),
                status=Status.active,
            )
            db.add_all([role, account])
            db.flush()
            student = Student(
                owner_id=teacher.id,
                user_id=account.id,
                student_number="RESOURCE-1",
                name="资料学生",
            )
            db.add(student)
            db.flush()
            db.add_all(
                [
                    UserRole(user_id=account.id, role_id=role.id),
                    ClassStudent(
                        class_id=uuid.UUID(class_id),
                        student_id=student.id,
                        status=MembershipStatus.active,
                    ),
                ]
            )
            db.commit()
            email = account.email

        student_client = TestClient(app)
        assert (
            student_client.post(
                "/auth/login", json={"email": email, "password": "student-pass-123"}
            ).status_code
            == 200
        )
        assert student_client.get("/api/student/resources").json() == []
        published = client.patch(
            f"/api/classes/{class_id}/resources/{resource['id']}/publication",
            json={"student_visible": True},
        )
        assert published.status_code == 200, published.text
        assert published.json()["student_visible"] is True
        resources = student_client.get("/api/student/resources")
        assert resources.status_code == 200
        assert [item["id"] for item in resources.json()] == [resource["id"]]
        download = student_client.get(f"/api/student/resources/{resource['id']}/download")
        assert download.status_code == 200
        assert download.content == png_bytes("blue")
        hidden = client.patch(
            f"/api/classes/{class_id}/resources/{resource['id']}/publication",
            json={"student_visible": False},
        )
        assert hidden.status_code == 200
        hidden_download = student_client.get(
            f"/api/student/resources/{resource['id']}/download"
        )
        assert hidden_download.status_code == 404
    finally:
        app.dependency_overrides.clear()
