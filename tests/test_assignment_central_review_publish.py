from datetime import timedelta
from pathlib import Path

from app.api.assignment_central_review import PublishInput, digest
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentClass,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    SchoolClass,
    StructuredRubricVersion,
    User,
    now_utc,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

client = TestClient(app)


def test_publish_contract_requires_server_readiness_and_explicit_true() -> None:
    response = client.post(
        "/api/assignments/00000000-0000-0000-0000-000000000000/publish",
        json={},
    )
    assert response.status_code == 422
    try:
        PublishInput.model_validate(
            {
                "readiness_snapshot_id": "00000000-0000-0000-0000-000000000000",
                "readiness_hash": "0" * 64,
                "expected_assignment_updated_at": "2026-07-26T00:00:00Z",
                "explicit_confirmation": False,
            }
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("explicit_confirmation=false must be rejected")


def test_stable_hash_ignores_mapping_insertion_order() -> None:
    assert digest({"b": [2, 1], "a": {"y": 2, "x": 1}}) == digest(
        {"a": {"x": 1, "y": 2}, "b": [2, 1]}
    )


def test_assignment_worker_has_no_publish_capability_import() -> None:
    source = (Path(__file__).parents[1] / "workers/tasks/assignment_generation.py").read_text(
        encoding="utf-8"
    )
    assert "teacher_publish" not in source
    assert "AssignmentPublishReadinessSnapshot" not in source
    assert "AssignmentExplicitConfirmation" not in source
    assert "AssignmentStatus.published" not in source


def test_green_teacher_review_binding_readiness_and_publish() -> None:
    client.get("/api/classes")
    db = SessionLocal()
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor is not None
    school_class = SchoolClass(owner_id=actor.id, name="集中审查班", status=ArchiveStatus.active)
    assignment = Assignment(
        owner_id=actor.id,
        title="集中审查作业",
        total_score=10,
        due_at=now_utc() + timedelta(days=7),
    )
    db.add_all([school_class, assignment])
    db.flush()
    db.add(AssignmentClass(assignment_id=assignment.id, class_id=school_class.id))
    paper = PaperVersion(assignment_id=assignment.id, version=1, created_by=actor.id)
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    question = Question(
        paper_version_id=paper.id,
        question_number="1",
        display_order=1,
        question_type="calculation",
        content_text="1+1",
        max_score=10,
    )
    db.add(question)
    db.flush()
    job = AssignmentGenerationJob(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation=1,
        status="review_required",
        idempotency_key="central-review-green",
        request_fingerprint="1" * 64,
        source_snapshot_hash="2" * 64,
        provider_config_version="test",
        prompt_version="test",
        schema_version="test",
    )
    db.add(job)
    db.flush()
    revision = AssignmentDraftRevision(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        revision=1,
        source_snapshot_hash=job.source_snapshot_hash,
        created_by_type="teacher",
        created_by=actor.id,
    )
    answer = ReferenceAnswerVersion(
        question_id=question.id,
        source_type="teacher_official",
        raw_content="2",
        normalized_content="2",
        structured_content={"alternative_answers": ["2.0"]},
        content_hash="3" * 64,
        version=1,
        provenance={"teacher": str(actor.id)},
        created_by=actor.id,
        status="confirmed",
        teacher_confirmed_at=now_utc(),
    )
    db.add_all([revision, answer])
    db.flush()
    structured = StructuredRubricVersion(
        question_id=question.id,
        question_version="1",
        reference_answer_version_id=answer.id,
        rubric_version=1,
        title="计算正确",
        total_points=10,
        status="confirmed",
        content_hash="4" * 64,
        created_by=actor.id,
        confirmed_by=actor.id,
        confirmed_at=now_utc(),
    )
    db.add(structured)
    db.flush()
    db.add(
        RubricCriterion(
            rubric_version_id=structured.id,
            stable_key="answer",
            title="答案正确",
            max_points=10,
            display_order=1,
            criterion_type="answer",
            required=True,
            validation_mode="manual",
        )
    )
    db.commit()

    created = client.post(f"/api/assignments/{assignment.id}/review-sessions")
    assert created.status_code == 201, created.text
    session = created.json()
    for kind in (
        "classes",
        "due_at",
        "total_score",
        "file_roles",
        "answer_sources",
        "paper_version",
        "reference_answers",
        "structured_rubrics",
    ):
        response = client.post(
            f"/api/assignment-review-sessions/{session['id']}/confirm/{kind}",
            json={
                "expected_review_version": session["review_version"],
                "explicit_confirmation": True,
            },
        )
        assert response.status_code == 200, response.text
        session["review_version"] = response.json()["review_version"]
    binding = client.post(
        f"/api/assignment-review-sessions/{session['id']}/rubric-binding",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert binding.status_code == 200, binding.text
    session = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    confirmed = client.post(
        f"/api/assignment-rubric-publication-bindings/{binding.json()['id']}/confirm",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    session = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    prepared = client.post(
        f"/api/assignment-review-sessions/{session['id']}/prepare-publication",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert prepared.status_code == 200, prepared.text
    db.refresh(assignment)
    assert assignment.status == "draft"
    published = client.post(
        f"/api/assignments/{assignment.id}/publish",
        json={
            "readiness_snapshot_id": prepared.json()["id"],
            "readiness_hash": prepared.json()["readiness_hash"],
            "expected_assignment_updated_at": assignment.updated_at.isoformat(),
            "explicit_confirmation": True,
        },
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    repeated = client.post(
        f"/api/assignments/{assignment.id}/publish",
        json={
            "readiness_snapshot_id": prepared.json()["id"],
            "readiness_hash": prepared.json()["readiness_hash"],
            "expected_assignment_updated_at": assignment.updated_at.isoformat(),
            "explicit_confirmation": True,
        },
    )
    assert repeated.status_code == 200
