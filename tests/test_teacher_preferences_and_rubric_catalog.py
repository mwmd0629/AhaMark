from decimal import Decimal

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    AssignmentClass,
    AuditLog,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    SchoolClass,
    Status,
    StructuredRubricVersion,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "secure-pass-123"


def create_teacher(email: str, display_name: str = "测试教师") -> User:
    with SessionLocal() as db:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            display_name=display_name,
            status=Status.active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def login(email: str) -> tuple[TestClient, str]:
    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    csrf = client.cookies.get("ahamark_csrf")
    assert csrf
    return client, csrf


def test_teacher_preferences_persist_and_reject_sensitive_or_stale_updates() -> None:
    teacher = create_teacher("teacher@example.com")
    other = create_teacher("other@example.com")
    with SessionLocal() as db:
        own_class = SchoolClass(owner_id=teacher.id, name="高一一班")
        other_class = SchoolClass(owner_id=other.id, name="其他教师班级")
        db.add_all([own_class, other_class])
        db.commit()
        own_class_id = str(own_class.id)
        other_class_id = str(other_class.id)

    client, csrf = login(teacher.email)
    initial = client.get("/auth/preferences")
    assert initial.status_code == 200
    assert initial.json()["revision"] == 0
    assert initial.json()["preferences"] == {
        "default_class_id": None,
        "rubric_status_filter": "all",
        "rubric_page_size": 20,
        "compact_rubric_cards": False,
    }
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
    saved = client.put(
        "/auth/preferences", headers={"x-csrf-token": csrf}, json=payload
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["profile"]["display_name"] == "王老师"
    assert saved.json()["preferences"] == payload["preferences"]
    assert client.get("/auth/preferences").json()["preferences"] == payload["preferences"]

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
        assert latest is not None
        assert latest.metadata_["revision"] == 1
        assert latest.metadata_["preferences"] == payload["preferences"]

    stale = client.put(
        "/auth/preferences", headers={"x-csrf-token": csrf}, json=payload
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "PREFERENCES_VERSION_CONFLICT"

    sensitive = client.put(
        "/auth/preferences",
        headers={"x-csrf-token": csrf},
        json={**payload, "expected_revision": 1, "openai_api_key": "must-not-be-accepted"},
    )
    assert sensitive.status_code == 422
    assert sensitive.json()["code"] == "REQUEST_VALIDATION_FAILED"

    foreign_class = client.put(
        "/auth/preferences",
        headers={"x-csrf-token": csrf},
        json={
            **payload,
            "expected_revision": 1,
            "preferences": {**payload["preferences"], "default_class_id": other_class_id},
        },
    )
    assert foreign_class.status_code == 422
    assert foreign_class.json()["code"] == "DEFAULT_CLASS_NOT_AVAILABLE"


def add_rubric(
    *,
    owner: User,
    title: str,
    question_number: str,
    status: str,
    class_name: str,
) -> tuple[str, str]:
    with SessionLocal() as db:
        assignment = Assignment(
            owner_id=owner.id,
            title=title,
            subject="数学",
            grade="高一",
            total_score=Decimal("5"),
        )
        school_class = SchoolClass(owner_id=owner.id, name=class_name)
        db.add_all([assignment, school_class])
        db.flush()
        db.add(AssignmentClass(assignment_id=assignment.id, class_id=school_class.id))
        paper = PaperVersion(assignment_id=assignment.id, version=1, created_by=owner.id)
        db.add(paper)
        db.flush()
        question = Question(
            paper_version_id=paper.id,
            question_number=question_number,
            display_order=1,
            question_type="short_answer",
            content_text="判断函数的单调性并说明理由。",
            max_score=Decimal("5"),
        )
        db.add(question)
        db.flush()
        reference = ReferenceAnswerVersion(
            question_id=question.id,
            source_type="teacher_authored",
            raw_content="函数单调递增。",
            normalized_content="函数单调递增。",
            structured_content={},
            content_hash=f"reference-{owner.id}",
            version=1,
            provenance={"entered_by_teacher": True},
            created_by=owner.id,
            status="confirmed",
        )
        db.add(reference)
        db.flush()
        rubric = StructuredRubricVersion(
            question_id=question.id,
            question_version="paper-v1",
            reference_answer_version_id=reference.id,
            rubric_version=1,
            title=f"{title}评分模板",
            total_points=Decimal("5"),
            status=status,
            content_hash=f"rubric-{owner.id}",
            created_by=owner.id,
        )
        db.add(rubric)
        db.flush()
        db.add(
            RubricCriterion(
                rubric_version_id=rubric.id,
                stable_key="final_answer",
                title="最终答案",
                max_points=Decimal("5"),
                display_order=1,
                criterion_type="final_answer",
                required=True,
                dependencies=[],
                expected_evidence={},
                validation_mode="manual_only",
                manual_review_policy={},
                partial_credit_policy={},
                validation_rule={},
                metadata_={},
            )
        )
        assignment.active_paper_version_id = paper.id
        db.commit()
        return str(rubric.id), str(school_class.id)


def test_rubric_catalog_supports_real_filters_and_tenant_isolation() -> None:
    teacher = create_teacher("teacher@example.com")
    other = create_teacher("other@example.com")
    rubric_id, class_id = add_rubric(
        owner=teacher,
        title="函数单元测试",
        question_number="3",
        status="confirmed",
        class_name="高一一班",
    )
    add_rubric(
        owner=other,
        title="其他教师的作业",
        question_number="9",
        status="confirmed",
        class_name="高一二班",
    )

    client, _ = login(teacher.email)
    response = client.get(
        "/api/structured-rubrics",
        params={
            "class_id": class_id,
            "status": "confirmed",
            "search": "函数",
            "page_size": 10,
        },
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["rubric"]["id"] == rubric_id
    assert item["rubric"]["criteria"][0]["title"] == "最终答案"
    assert item["assignment"]["title"] == "函数单元测试"
    assert item["question"]["question_number"] == "3"
    assert item["question"]["max_score"] == "5.00"

    assert client.get("/api/structured-rubrics", params={"search": "其他教师"}).json()[
        "total"
    ] == 0
    invalid = client.get("/api/structured-rubrics", params={"status": "not-a-status"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "REQUEST_VALIDATION_FAILED"
