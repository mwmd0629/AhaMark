import io
import uuid

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    AssignmentParticipantSnapshot,
    ClassStudent,
    GradingCollaborator,
    GradingQuestionAssignment,
    MembershipStatus,
    Student,
    User,
    now_utc,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from test_assignments import FakeStorage, active_class

client = TestClient(app)


def actor_and_db():
    db = SessionLocal()
    client.get("/api/classes")
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor is not None
    return actor, db


def png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 160), "white").save(output, "PNG")
    return output.getvalue()


def add_student(db, actor_id, school_class, number: str, name: str) -> Student:
    student = Student(owner_id=actor_id, student_number=number, name=name)
    db.add(student)
    db.flush()
    db.add(
        ClassStudent(
            class_id=school_class.id,
            student_id=student.id,
            status=MembershipStatus.active,
            joined_at=now_utc(),
        )
    )
    db.commit()
    return student


def test_joint_exam_requires_multiple_populated_non_overlapping_classes():
    actor, db = actor_and_db()
    first = active_class(db, actor.id, "联考一班")
    second = active_class(db, actor.id, "联考二班")

    one_class = client.post(
        "/api/assignments",
        json={
            "title": "单班伪联考",
            "delivery_mode": "joint_exam",
            "class_ids": [str(first.id)],
        },
    )
    assert one_class.status_code == 422

    shared = add_student(db, actor.id, first, "J001", "重复学生")
    db.add(
        ClassStudent(
            class_id=second.id,
            student_id=shared.id,
            status=MembershipStatus.active,
            joined_at=now_utc(),
        )
    )
    db.commit()
    assignment = client.post(
        "/api/assignments",
        json={
            "title": "名单冲突联考",
            "delivery_mode": "joint_exam",
            "class_ids": [str(first.id), str(second.id)],
        },
    )
    assert assignment.status_code == 201
    codes = {issue["code"] for issue in assignment.json()["completeness"]["issues"]}
    assert "JOINT_EXAM_DUPLICATE_STUDENT" in codes


def test_joint_exam_freezes_roster_and_ensures_one_batch_per_class():
    actor, db = actor_and_db()
    first = active_class(db, actor.id, "高数联考一班")
    second = active_class(db, actor.id, "高数联考二班")
    first_student = add_student(db, actor.id, first, "J101", "甲同学")
    second_student = add_student(db, actor.id, second, "J201", "乙同学")
    storage = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        created = client.post(
            "/api/assignments",
            json={
                "title": "高等数学联考",
                "delivery_mode": "joint_exam",
                "total_score": 10,
                "class_ids": [str(first.id), str(second.id)],
            },
        )
        assert created.status_code == 201, created.text
        assignment_id = created.json()["id"]
        uploaded = client.post(
            f"/api/assignments/{assignment_id}/files",
            files={"file": ("paper.png", png(), "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        question = client.post(
            f"/api/assignments/{assignment_id}/questions",
            json={
                "question_number": "1",
                "question_type": "calculation",
                "max_score": 10,
                "content_text": "计算",
            },
        )
        assert question.status_code == 201, question.text
        rubric = client.put(
            f"/api/assignments/{assignment_id}/rubrics/{question.json()['id']}",
            json={"standard_answer": "答案", "items": [{"title": "正确", "points": 10}]},
        )
        assert rubric.status_code == 200, rubric.text
        readiness = client.get(f"/api/assignments/{assignment_id}/manual-publish-readiness").json()
        published = client.post(
            f"/api/assignments/{assignment_id}/manual-publish",
            json={
                "state_hash": readiness["state_hash"],
                "expected_assignment_updated_at": readiness["expected_assignment_updated_at"],
                "explicit_confirmation": True,
            },
        )
        assert published.status_code == 200, published.text
        assert published.json()["participant_snapshot"]["total"] == 2
        snapshots = list(
            db.scalars(
                select(AssignmentParticipantSnapshot).where(
                    AssignmentParticipantSnapshot.assignment_id == uuid.UUID(assignment_id)
                )
            )
        )
        assert {row.student_id for row in snapshots} == {
            first_student.id,
            second_student.id,
        }

        old_membership = db.scalar(
            select(ClassStudent).where(
                ClassStudent.class_id == first.id,
                ClassStudent.student_id == first_student.id,
            )
        )
        assert old_membership is not None
        old_membership.status = MembershipStatus.removed
        first_student.student_number = "J999"
        first_student.name = "已改名学生"
        newcomer = add_student(db, actor.id, first, "J102", "新同学")
        db.commit()

        pool = client.post(f"/api/assignments/{assignment_id}/joint-grading-pool", json={})
        assert pool.status_code == 201, pool.text
        assert pool.json()["class_count"] == 2
        assert pool.json()["batch_count"] == 2
        collaborator = User(
            email="joint-grader@example.com",
            password_hash="x",
            display_name="联考协作老师",
        )
        db.add(collaborator)
        db.flush()
        db.add(
            GradingCollaborator(
                assignment_id=uuid.UUID(assignment_id),
                user_id=collaborator.id,
                added_by=actor.id,
                role="grader",
                status="active",
            )
        )
        db.commit()
        assigned = client.put(
            f"/api/assignments/{assignment_id}/joint-question-assignments/{question.json()['id']}",
            json={"assignee_id": str(collaborator.id)},
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["questions"][0]["assignee_id"] == str(collaborator.id)
        assignment_rows = list(
            db.scalars(
                select(GradingQuestionAssignment).where(
                    GradingQuestionAssignment.question_id == uuid.UUID(question.json()["id"])
                )
            )
        )
        assert len(assignment_rows) == 2
        assert {row.assignee_id for row in assignment_rows} == {collaborator.id}
        first_batch = next(
            item for item in pool.json()["items"] if item["class_id"] == str(first.id)
        )
        option_ids = {item["id"] for item in first_batch["matching"]["student_options"]}
        assert str(first_student.id) in option_ids
        assert str(newcomer.id) not in option_ids
        frozen_option = next(
            item
            for item in first_batch["matching"]["student_options"]
            if item["id"] == str(first_student.id)
        )
        assert frozen_option == {
            "id": str(first_student.id),
            "student_number": "J101",
            "name": "甲同学",
        }

        repeated = client.post(f"/api/assignments/{assignment_id}/joint-grading-pool", json={})
        assert repeated.status_code == 201
        assert {item["id"] for item in repeated.json()["items"]} == {
            item["id"] for item in pool.json()["items"]
        }

        matched = client.post(
            f"/api/grading-batches/{first_batch['id']}/files",
            files=[("files", ("J101-answer.png", png(), "image/png"))],
        )
        assert matched.status_code == 201, matched.text
        assert matched.json()["items"][0]["suggested_student_id"] == str(first_student.id)
    finally:
        app.dependency_overrides.pop(get_storage, None)
