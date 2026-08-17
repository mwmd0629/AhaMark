import uuid
from decimal import Decimal

import pytest
from app.api.domain import ApiProblem
from app.api.rubric_templates import _owned_template
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    AssignmentStatus,
    AuditLog,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    RubricTemplateApplication,
    StructuredRubricVersion,
    User,
    VersionStatus,
    now_utc,
)
from app.question_versions import question_version_token
from fastapi.testclient import TestClient
from sqlalchemy import select

client = TestClient(app)


def actor_and_question(score: str = "7") -> tuple[User, Question, ReferenceAnswerVersion]:
    client.get("/api/rubric-templates")
    db = SessionLocal()
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor is not None
    assignment = Assignment(
        owner_id=actor.id,
        title="模板测试",
        subject="数学",
        grade="八年级",
        status=AssignmentStatus.draft,
    )
    db.add(assignment)
    db.flush()
    paper = PaperVersion(
        assignment_id=assignment.id,
        version=1,
        status=VersionStatus.draft,
        created_by=actor.id,
    )
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    question = Question(
        paper_version_id=paper.id,
        question_number="1",
        display_order=1,
        question_type="calculation",
        content_text="1+1=?",
        max_score=Decimal(score),
    )
    db.add(question)
    db.flush()
    reference = ReferenceAnswerVersion(
        question_id=question.id,
        source_type="teacher_authored",
        raw_content="2",
        normalized_content="2",
        structured_content={},
        content_hash="a" * 64,
        version=1,
        provenance={},
        created_by=actor.id,
        status="confirmed",
        teacher_confirmed_at=now_utc(),
    )
    db.add(reference)
    db.commit()
    db.refresh(question)
    db.refresh(reference)
    db.expunge(actor)
    db.expunge(question)
    db.expunge(reference)
    db.close()
    return actor, question, reference


def criterion(key: str, points: str) -> dict[str, object]:
    return {
        "stable_key": key,
        "title": key,
        "description": "可复用要求",
        "max_points": points,
        "criterion_type": "computation",
        "required": True,
        "dependencies": [],
        "validation_mode": "ai_suggestion",
        "manual_review_policy": {},
        "partial_credit_policy": {},
        "validation_rule": {},
        "metadata": {},
    }


def create_template() -> dict[str, object]:
    response = client.post(
        "/api/rubric-templates",
        json={
            "name": "计算题通用模板",
            "subject": "数学",
            "grade": "八年级",
            "question_type": "calculation",
            "scoring_basis": "proportional",
            "total_points": "100",
            "criteria": [
                criterion("method", "33.33"),
                criterion("process", "33.33"),
                criterion("result", "33.34"),
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_crud_owner_isolation_immutable_confirmation_and_filters() -> None:
    actor, _question, _reference = actor_and_question()
    created = create_template()
    template_id = created["id"]
    version = created["current_version"]
    listed = client.get("/api/rubric-templates?search=计算题&subject=数学&grade=八年级")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [template_id]
    updated = client.patch(
        f"/api/rubric-templates/{template_id}",
        json={
            "name": "计算题模板更新",
            "expected_content_hash": version["content_hash"],
        },
    )
    assert updated.status_code == 200
    stale = client.patch(
        f"/api/rubric-templates/{template_id}",
        json={
            "name": "过期覆盖",
            "expected_content_hash": version["content_hash"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "RUBRIC_TEMPLATE_STALE"
    version = updated.json()["current_version"]
    confirmed = client.post(
        f"/api/rubric-templates/{template_id}/confirm",
        json={"expected_content_hash": version["content_hash"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    immutable = client.patch(
        f"/api/rubric-templates/{template_id}",
        json={
            "name": "覆盖确认版本",
            "expected_content_hash": version["content_hash"],
        },
    )
    assert immutable.status_code == 409
    assert immutable.json()["code"] == "RUBRIC_TEMPLATE_IMMUTABLE"
    duplicate = client.post(f"/api/rubric-templates/{template_id}/duplicate")
    assert duplicate.status_code == 201 and duplicate.json()["status"] == "draft"
    archived = client.post(f"/api/rubric-templates/{template_id}/archive")
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    cannot_version = client.post(f"/api/rubric-templates/{template_id}/versions", json={})
    assert cannot_version.status_code == 409
    assert cannot_version.json()["code"] == "RUBRIC_TEMPLATE_ARCHIVED"

    db = SessionLocal()
    with pytest.raises(ApiProblem) as error:
        _owned_template(db, uuid.uuid4(), uuid.UUID(template_id))
    assert error.value.status == 404
    assert _owned_template(db, actor.id, uuid.UUID(template_id)).id == uuid.UUID(template_id)
    db.close()


def test_preview_rounding_apply_idempotency_and_stale_guard() -> None:
    _actor, question, reference = actor_and_question("7")
    created = create_template()
    version = created["current_version"]
    confirmed = client.post(
        f"/api/rubric-templates/{created['id']}/confirm",
        json={"expected_content_hash": version["content_hash"]},
    ).json()
    current = confirmed["current_version"]
    preview_response = client.post(
        f"/api/questions/{question.id}/rubric-template-preview",
        json={"template_version_id": current["id"]},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert [item["max_points"] for item in preview["criteria"]] == ["2.33", "2.33", "2.34"]
    assert sum(Decimal(item["max_points"]) for item in preview["criteria"]) == Decimal("7")

    payload = {
        "template_version_id": current["id"],
        "idempotency_key": "template-apply-0001",
        "expected_template_content_hash": preview["template_content_hash"],
        "expected_question_version": preview["question_version"],
        "reference_answer_version_id": str(reference.id),
        "expected_reference_answer_content_hash": reference.content_hash,
    }
    applied = client.post(f"/api/questions/{question.id}/apply-rubric-template", json=payload)
    assert applied.status_code == 201, applied.text
    replay = client.post(f"/api/questions/{question.id}/apply-rubric-template", json=payload)
    assert replay.status_code == 201 and replay.json()["replayed"] is True

    db = SessionLocal()
    rubric = db.get(
        StructuredRubricVersion,
        uuid.UUID(applied.json()["structured_rubric_version_id"]),
    )
    assert rubric is not None and rubric.status == "draft"
    assert rubric.reference_answer_version_id == reference.id
    criteria = list(
        db.scalars(select(RubricCriterion).where(RubricCriterion.rubric_version_id == rubric.id))
    )
    assert sum(Decimal(item.max_points) for item in criteria) == Decimal("7")
    assert all(item.expected_evidence == {} for item in criteria)
    assert db.scalar(select(RubricTemplateApplication)) is not None
    db.close()

    stale = dict(payload)
    stale["idempotency_key"] = "template-apply-stale"
    stale["expected_question_version"] = "stale-question"
    response = client.post(f"/api/questions/{question.id}/apply-rubric-template", json=stale)
    assert response.status_code == 409
    assert response.json()["code"] == "RUBRIC_TEMPLATE_APPLY_STALE"


def test_fixed_score_blocker_and_save_as_template_strips_answer_evidence() -> None:
    _actor, question, reference = actor_and_question("7")
    invalid_precision = client.post(
        "/api/rubric-templates",
        json={
            "name": "非法精度",
            "scoring_basis": "fixed",
            "total_points": "5",
            "criteria": [criterion("a", "1.005"), criterion("b", "3.995")],
        },
    )
    assert invalid_precision.status_code == 422
    assert any(
        error["code"] == "POINTS_PRECISION_INVALID"
        for error in invalid_precision.json()["details"]["errors"]
    )
    fixed = client.post(
        "/api/rubric-templates",
        json={
            "name": "固定五分",
            "scoring_basis": "fixed",
            "total_points": "5",
            "criteria": [criterion("answer", "5")],
        },
    ).json()
    fixed = client.post(
        f"/api/rubric-templates/{fixed['id']}/confirm",
        json={"expected_content_hash": fixed["current_version"]["content_hash"]},
    ).json()
    blocked = client.post(
        f"/api/questions/{question.id}/rubric-template-preview",
        json={"template_version_id": fixed["current_version"]["id"]},
    )
    assert blocked.status_code == 200
    assert blocked.json()["blockers"] == [
        {"code": "FIXED_SCORE_MISMATCH", "message": "固定分值模板总分必须等于题目满分"}
    ]

    db = SessionLocal()
    rubric = StructuredRubricVersion(
        question_id=question.id,
        question_version=question_version_token(db.get(Question, question.id)),
        reference_answer_version_id=reference.id,
        rubric_version=1,
        title="本题专属评分",
        total_points=Decimal("7"),
        status="confirmed",
        content_hash="b" * 64,
        created_by=reference.created_by,
    )
    db.add(rubric)
    db.flush()
    db.add(
        RubricCriterion(
            rubric_version_id=rubric.id,
            stable_key="answer",
            title="答案正确",
            description="检查结论",
            max_points=Decimal("7"),
            display_order=0,
            criterion_type="final_answer",
            required=True,
            dependencies=[],
            expected_evidence={"answer": "2", "source_region": {"page": 1}},
            validation_mode="ai_suggestion",
            manual_review_policy={"requires_teacher": True, "evidence": {"answer": "2"}},
            partial_credit_policy={"strategy": "proportional", "expected_value": "2"},
            error_category="algebra",
            validation_rule={"answer_type": "exact_scalar", "expected_value": "2"},
            metadata_={"answer": "2", "reusable": "yes"},
        )
    )
    db.commit()
    rubric_id = rubric.id
    db.close()
    saved = client.post(
        f"/api/structured-rubrics/{rubric_id}/save-as-template",
        json={"name": "从题目保存", "scoring_basis": "proportional"},
    )
    assert saved.status_code == 201, saved.text
    item = saved.json()
    assert item["status"] == "draft"
    saved_criterion = item["current_version"]["criteria"][0]
    assert saved_criterion["metadata"] == {"reusable": "yes"}
    assert "expected_evidence" not in saved_criterion
    assert saved_criterion["description"] is None
    assert saved_criterion["validation_rule"] == {"answer_type": "exact_scalar"}
    assert saved_criterion["stable_key"] == "answer"
    assert saved_criterion["criterion_type"] == "final_answer"
    assert saved_criterion["required"] is True
    assert saved_criterion["dependencies"] == []
    assert saved_criterion["validation_mode"] == "ai_suggestion"
    assert saved_criterion["manual_review_policy"] == {"requires_teacher": True}
    assert saved_criterion["partial_credit_policy"] == {"strategy": "proportional"}
    assert saved_criterion["error_category"] == "algebra"
    db = SessionLocal()
    assert db.scalar(
        select(AuditLog).where(
            AuditLog.action == "save_as_template",
            AuditLog.resource_id == str(rubric_id),
        )
    )
    db.close()
