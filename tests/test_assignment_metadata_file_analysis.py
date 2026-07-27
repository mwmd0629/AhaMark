import uuid

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    AssignmentClass,
    AssignmentFieldSuggestion,
    AssignmentSourceFileAnalysis,
    AuditLog,
    FileStatus,
    PaperPage,
    PaperVersion,
    StoredFile,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from workers.tasks.assignment_generation import _run

client = TestClient(app)


def source_assignment() -> Assignment:
    client.get("/api/classes")
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
        assert actor is not None
        assignment = Assignment(owner_id=actor.id, title="教师原始标题")
        db.add(assignment)
        db.flush()
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            created_by=actor.id,
            source_type="upload",
        )
        db.add(paper)
        db.flush()
        assignment.active_paper_version_id = paper.id
        for index, name in enumerate(
            ("2026数学期中满分100分试卷.pdf", "第三方参考答案.pdf"), start=1
        ):
            stored = StoredFile(
                owner_id=actor.id,
                storage_key=f"assignments/test/{uuid.uuid4()}.pdf",
                original_name=name,
                content_type="application/pdf",
                size=1024,
                checksum="a" * 64,
                status=FileStatus.ready,
            )
            db.add(stored)
            db.flush()
            db.add(
                PaperPage(
                    paper_version_id=paper.id,
                    stored_file_id=stored.id,
                    page_number=index,
                    source_page_number=1,
                    status="ready",
                )
            )
        db.commit()
        db.refresh(assignment)
        db.expunge(assignment)
        return assignment


def run_fake(assignment_id: uuid.UUID, monkeypatch) -> dict:
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    response = client.post(
        f"/api/assignments/{assignment_id}/generation-jobs",
        json={"idempotency_key": "metadata-file-analysis-0001", "provider_mode": "fake"},
    )
    assert response.status_code == 201
    result = _run(response.json()["id"], None)
    assert result["status"] == "partial"
    return client.get(f"/api/assignment-generation-jobs/{response.json()['id']}").json()


def test_fake_provider_creates_draft_only_field_suggestions(monkeypatch):
    assignment = source_assignment()
    job = run_fake(assignment.id, monkeypatch)
    revision_id = job["revision"]["id"]
    response = client.get(f"/api/assignment-draft-revisions/{revision_id}/field-suggestions")
    assert response.status_code == 200
    suggestions = response.json()
    names = {row["field_name"] for row in suggestions}
    assert {"title", "subject", "academic_year", "assessment_type", "total_score"} <= names
    assert "class_ids" not in names
    assert "due_at" not in names
    with SessionLocal() as db:
        persisted = db.get(Assignment, assignment.id)
        assert persisted is not None
        assert persisted.title == "教师原始标题"
        assert persisted.total_score is None
        assert persisted.status.value == "draft"
        assert db.scalar(select(func.count()).select_from(AssignmentClass)) == 0


def test_teacher_disposition_and_explicit_total_score_confirmation(monkeypatch):
    assignment = source_assignment()
    job = run_fake(assignment.id, monkeypatch)
    revision_id = job["revision"]["id"]
    suggestions = client.get(
        f"/api/assignment-draft-revisions/{revision_id}/field-suggestions"
    ).json()
    title = next(row for row in suggestions if row["field_name"] == "title")
    current = client.get(f"/api/assignments/{assignment.id}").json()
    accepted = client.patch(
        f"/api/assignment-field-suggestions/{title['id']}/disposition",
        json={
            "action": "accept",
            "expected_teacher_edit_version": 0,
            "expected_assignment_updated_at": current["updated_at"],
        },
    )
    assert accepted.status_code == 200
    assert client.get(f"/api/assignments/{assignment.id}").json()["title"] != "教师原始标题"
    repeated = client.patch(
        f"/api/assignment-field-suggestions/{title['id']}/disposition",
        json={"action": "reject", "expected_teacher_edit_version": 0},
    )
    assert repeated.status_code == 409

    suggestions = client.get(
        f"/api/assignment-draft-revisions/{revision_id}/field-suggestions"
    ).json()
    total = next(row for row in suggestions if row["field_name"] == "total_score")
    missing_explicit = client.post(
        f"/api/assignment-field-suggestions/{total['id']}/confirm-total-score",
        json={
            "expected_teacher_edit_version": 0,
            "expected_assignment_updated_at": client.get(
                f"/api/assignments/{assignment.id}"
            ).json()["updated_at"],
            "confirmed_value": 100,
            "explicit_confirmation": False,
        },
    )
    assert missing_explicit.status_code == 422
    current = client.get(f"/api/assignments/{assignment.id}").json()
    confirmed = client.post(
        f"/api/assignment-field-suggestions/{total['id']}/confirm-total-score",
        json={
            "expected_teacher_edit_version": 0,
            "expected_assignment_updated_at": current["updated_at"],
            "confirmed_value": 100,
            "explicit_confirmation": True,
        },
    )
    assert confirmed.status_code == 200
    assert client.get(f"/api/assignments/{assignment.id}").json()["total_score"] == "100.00"
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(AuditLog).where(
                    AuditLog.action == "assignment_field_suggestion.confirm_total_score"
                )
            )
            is not None
        )


def test_file_role_duplicate_and_untrusted_answer_confirmation(monkeypatch):
    assignment = source_assignment()
    job = run_fake(assignment.id, monkeypatch)
    revision_id = job["revision"]["id"]
    response = client.get(f"/api/assignment-draft-revisions/{revision_id}/file-analyses")
    assert response.status_code == 200
    analyses = response.json()
    assert len(analyses) == 2
    assert any("DUPLICATE_FILE" in row["warning_codes"] for row in analyses)
    answer = next(row for row in analyses if row["suggested_role"] == "reference_answer")
    assert answer["suggested_answer_source"] == "third_party"
    forbidden = client.patch(
        f"/api/assignment-source-file-analyses/{answer['id']}/confirmation",
        json={
            "expected_teacher_edit_version": 0,
            "confirmed_role": "reference_answer",
            "confirmed_answer_source": "publisher_official",
        },
    )
    assert forbidden.status_code == 422
    confirmed = client.patch(
        f"/api/assignment-source-file-analyses/{answer['id']}/confirmation",
        json={
            "expected_teacher_edit_version": 0,
            "confirmed_role": "reference_answer",
            "confirmed_answer_source": "third_party",
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["analysis_status"] == "confirmed"
    with SessionLocal() as db:
        persisted = db.get(Assignment, assignment.id)
        assert persisted is not None and persisted.status.value == "draft"
        assert db.scalar(select(func.count()).select_from(AssignmentSourceFileAnalysis)) == 2
        assert db.scalar(select(func.count()).select_from(AssignmentFieldSuggestion)) >= 6
