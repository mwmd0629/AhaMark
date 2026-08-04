import io
import uuid

from app.api.actor import CurrentActor, get_current_actor
from app.api.assignments import freeze_participant_roster
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    AssignmentParticipantSnapshot,
    ClassStudent,
    GradingCollaborator,
    GradingQuestionAssignment,
    MembershipStatus,
    Role,
    Student,
    User,
    UserRole,
    now_utc,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, select
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
    assert one_class.status_code == 201
    assert "JOINT_EXAM_CLASSES_REQUIRED" in {
        issue["code"] for issue in one_class.json()["completeness"]["issues"]
    }

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


def test_cross_teacher_joint_exam_requires_invitation_and_class_owner_authorization():
    owner, db = actor_and_db()
    owner_class = active_class(db, owner.id, "主责老师班级")
    owner_student = add_student(db, owner.id, owner_class, "O001", "主责班学生")
    collaborator = User(
        email="joint-class-owner@example.com",
        password_hash="!test!",
        display_name="参考班负责人",
    )
    outsider = User(
        email="joint-outsider@example.com",
        password_hash="!test!",
        display_name="未受邀老师",
    )
    no_role = User(
        email="joint-no-role@example.com",
        password_hash="!test!",
        display_name="无角色账号",
    )
    student_only = User(
        email="joint-student-only@example.com",
        password_hash="!test!",
        display_name="学生账号",
    )
    auditor = User(
        email="joint-auditor@example.com",
        password_hash="!test!",
        display_name="审计账号",
    )
    db.add_all([collaborator, outsider, no_role, student_only, auditor])
    db.flush()
    teacher_role = db.scalar(select(Role).where(Role.name == "teacher"))
    if teacher_role is None:
        teacher_role = Role(name="teacher", description="教师端")
        db.add(teacher_role)
        db.flush()
    student_role = Role(name="student", description="学生端")
    auditor_role = Role(name="auditor", description="只读审计")
    db.add_all([student_role, auditor_role])
    db.flush()
    collaborator_teacher_link = UserRole(user_id=collaborator.id, role_id=teacher_role.id)
    db.add_all(
        [
            collaborator_teacher_link,
            UserRole(user_id=student_only.id, role_id=student_role.id),
            UserRole(user_id=auditor.id, role_id=auditor_role.id),
        ]
    )
    collaborator_class = active_class(db, collaborator.id, "协作老师班级")
    collaborator_student = add_student(
        db, collaborator.id, collaborator_class, "C001", "协作班学生"
    )
    outsider_class = active_class(db, outsider.id, "未授权班级")
    add_student(db, outsider.id, outsider_class, "X001", "未授权学生")

    created = client.post(
        "/api/assignments",
        json={
            "title": "跨教师联考",
            "delivery_mode": "joint_exam",
            "class_ids": [str(owner_class.id)],
        },
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["id"]

    try:
        app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            outsider.id, outsider.email
        )
        uninvited = client.post(
            f"/api/assignments/{assignment_id}/joint-classes",
            json={"class_ids": [str(outsider_class.id)]},
        )
        assert uninvited.status_code == 404

        app.dependency_overrides[get_current_actor] = lambda: CurrentActor(owner.id, owner.email)
        for rejected_account in (no_role, student_only, auditor):
            rejected = client.post(
                f"/api/assignments/{assignment_id}/joint-team/collaborators",
                json={"email": rejected_account.email},
            )
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["code"] == "COLLABORATOR_TEACHER_REQUIRED"
        invited = client.post(
            f"/api/assignments/{assignment_id}/joint-team/collaborators",
            json={"email": collaborator.email},
        )
        assert invited.status_code == 201, invited.text

        app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            collaborator.id, collaborator.email
        )
        invitations = client.get("/api/assignments/joint-exams/invitations")
        assert invitations.status_code == 200, invitations.text
        assert {item["assignment_id"] for item in invitations.json()} == {assignment_id}
        cannot_authorize_other_owner = client.post(
            f"/api/assignments/{assignment_id}/joint-classes",
            json={"class_ids": [str(outsider_class.id)]},
        )
        assert cannot_authorize_other_owner.status_code == 403
        authorized = client.post(
            f"/api/assignments/{assignment_id}/joint-classes",
            json={"class_ids": [str(collaborator_class.id)]},
        )
        assert authorized.status_code == 201, authorized.text
        team = authorized.json()
        cross_class = next(
            row for row in team["classes"] if row["id"] == str(collaborator_class.id)
        )
        assert cross_class["authorized"] is True
        assert cross_class["authorized_by"] == str(collaborator.id)

        db.delete(collaborator_teacher_link)
        db.commit()
        invitations_after_role_revocation = client.get("/api/assignments/joint-exams/invitations")
        assert invitations_after_role_revocation.status_code == 200
        assert invitations_after_role_revocation.json() == []
        denied_after_role_revocation = client.get(f"/api/assignments/{assignment_id}/joint-team")
        assert denied_after_role_revocation.status_code == 404

        app.dependency_overrides[get_current_actor] = lambda: CurrentActor(owner.id, owner.email)
        current = client.get(f"/api/assignments/{assignment_id}").json()
        preserved = client.put(
            f"/api/assignments/{assignment_id}/classes",
            json={
                "class_ids": [str(owner_class.id)],
                "updated_at": current["updated_at"],
            },
        )
        assert preserved.status_code == 200, preserved.text
        assert {row["id"] for row in preserved.json()["classes"]} == {
            str(owner_class.id),
            str(collaborator_class.id),
        }
        assignment = db.get(Assignment, uuid.UUID(assignment_id))
        assert assignment is not None
        assert freeze_participant_roster(db, assignment) == 2
        db.commit()
        snapshots = list(
            db.scalars(
                select(AssignmentParticipantSnapshot).where(
                    AssignmentParticipantSnapshot.assignment_id == assignment.id
                )
            )
        )
        assert {row.student_id for row in snapshots} == {
            owner_student.id,
            collaborator_student.id,
        }
    finally:
        app.dependency_overrides.pop(get_current_actor, None)


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
        teacher_role = db.scalar(select(Role).where(Role.name == "teacher"))
        if teacher_role is None:
            teacher_role = Role(name="teacher", description="教师端")
            db.add(teacher_role)
            db.flush()
        db.add(UserRole(user_id=collaborator.id, role_id=teacher_role.id))
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
        app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            collaborator.id, collaborator.email
        )
        work = client.get("/api/joint-grading-work")
        assert work.status_code == 200, work.text
        assert work.json()[0]["assignment_id"] == assignment_id
        assert work.json()[0]["question_id"] == question.json()["id"]
        assert work.json()[0]["class_count"] == 2
        db.execute(
            delete(UserRole).where(
                UserRole.user_id == collaborator.id,
                UserRole.role_id == teacher_role.id,
            )
        )
        db.commit()
        revoked_work = client.get("/api/joint-grading-work")
        assert revoked_work.status_code == 403
        assert revoked_work.json()["code"] == "TEACHER_ROLE_REQUIRED"
        assert assignment_id not in revoked_work.text
        app.dependency_overrides[get_current_actor] = lambda: CurrentActor(actor.id, actor.email)
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
        app.dependency_overrides.pop(get_current_actor, None)
        app.dependency_overrides.pop(get_storage, None)
