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
    GenerationIssue,
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
            ("2026数学分析期中满分100分试卷.pdf", "第三方参考答案.pdf"), start=1
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


def run_fake(
    assignment_id: uuid.UUID,
    monkeypatch,
    idempotency_key: str = "metadata-file-analysis-0001",
) -> dict:
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    response = client.post(
        f"/api/assignments/{assignment_id}/generation-jobs",
        json={"idempotency_key": idempotency_key, "provider_mode": "fake"},
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
    subject = next(row for row in suggestions if row["field_name"] == "subject")
    assert subject["normalized_value"] == "数学分析"
    assert "class_ids" not in names
    assert "due_at" not in names
    with SessionLocal() as db:
        persisted = db.get(Assignment, assignment.id)
        assert persisted is not None
        assert persisted.title == "教师原始标题"
        assert persisted.total_score is None
        assert persisted.status.value == "draft"
        assert db.scalar(select(func.count()).select_from(AssignmentClass)) == 0


def test_existing_total_score_does_not_create_unconfirmed_score_issue(monkeypatch):
    assignment = source_assignment()
    with SessionLocal() as db:
        persisted = db.get(Assignment, assignment.id)
        assert persisted is not None
        persisted.total_score = 100
        db.commit()

    job = run_fake(assignment.id, monkeypatch)
    revision_id = job["revision"]["id"]
    suggestions = client.get(
        f"/api/assignment-draft-revisions/{revision_id}/field-suggestions"
    ).json()

    assert all(row["field_name"] != "total_score" for row in suggestions)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(GenerationIssue)
                .where(
                    GenerationIssue.assignment_id == assignment.id,
                    GenerationIssue.code.in_({"TOTAL_SCORE_UNCONFIRMED", "TOTAL_SCORE_CONFLICT"}),
                )
            )
            == 0
        )


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
    question = next(row for row in analyses if row["suggested_role"] == "question_paper")
    assert "FILE_ROLE_REVIEW_REQUIRED" not in question["warning_codes"]
    answer = next(row for row in analyses if row["suggested_role"] == "reference_answer")
    assert answer["suggested_answer_source"] == "third_party"
    assert "FILE_ROLE_CONFLICT_REVIEW_REQUIRED" in answer["warning_codes"]
    assert "ANSWER_SOURCE_CONFIRMATION_REQUIRED" not in answer["warning_codes"]
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


def test_mixed_question_and_answer_role_is_supported(monkeypatch):
    assignment = source_assignment()
    with SessionLocal.begin() as db:
        mixed_file = db.scalar(
            select(StoredFile).where(
                StoredFile.owner_id == assignment.owner_id,
                StoredFile.original_name == "2026数学分析期中满分100分试卷.pdf",
            )
        )
        assert mixed_file is not None
        mixed_file.original_name = "习题与解答.pdf"

    job = run_fake(assignment.id, monkeypatch, "mixed-file-role-0001")
    analysis = next(
        row
        for row in client.get(
            f"/api/assignment-draft-revisions/{job['revision']['id']}/file-analyses"
        ).json()
        if row["file_name"] == "习题与解答.pdf"
    )
    assert analysis["suggested_role"] == "question_and_answer"
    response = client.patch(
        f"/api/assignment-source-file-analyses/{analysis['id']}/confirmation",
        json={
            "expected_teacher_edit_version": 0,
            "confirmed_role": "question_and_answer",
            "confirmed_answer_source": "unknown",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["teacher_confirmed_role"] == "question_and_answer"


def test_teacher_can_correct_confirmed_file_role_and_old_generation_becomes_stale(monkeypatch):
    assignment = source_assignment()
    job = run_fake(assignment.id, monkeypatch)
    revision_id = job["revision"]["id"]
    analysis = next(
        row
        for row in client.get(f"/api/assignment-draft-revisions/{revision_id}/file-analyses").json()
        if row["suggested_role"] == "question_paper"
    )

    confirmed = client.patch(
        f"/api/assignment-source-file-analyses/{analysis['id']}/confirmation",
        json={
            "expected_teacher_edit_version": 0,
            "confirmed_role": "question_paper",
            "confirmed_answer_source": "not_applicable",
        },
    )
    assert confirmed.status_code == 200

    corrected = client.patch(
        f"/api/assignment-source-file-analyses/{analysis['id']}/confirmation",
        json={
            "expected_teacher_edit_version": 1,
            "confirmed_role": "reference_answer",
            "confirmed_answer_source": "unknown",
            "review_note": "首次用途选错",
        },
    )

    assert corrected.status_code == 200
    assert corrected.json()["analysis_status"] == "confirmed"
    assert corrected.json()["teacher_confirmed_role"] == "reference_answer"
    assert corrected.json()["teacher_edit_version"] == 2
    refreshed_job = client.get(f"/api/assignment-generation-jobs/{job['id']}").json()
    assert refreshed_job["status"] == "stale"
    assert refreshed_job["revision"]["status"] == "stale"
    assert any(issue["code"] == "SOURCE_CHANGED" for issue in refreshed_job["issues"])
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(AuditLog.action).where(
                    AuditLog.resource_id == analysis["id"],
                    AuditLog.action == "assignment_source_file_analysis.update_confirmation",
                )
            )
            == "assignment_source_file_analysis.update_confirmation"
        )


def test_unchanged_files_keep_teacher_confirmed_roles_on_regeneration(monkeypatch):
    assignment = source_assignment()
    first_job = run_fake(assignment.id, monkeypatch)
    first_revision_id = first_job["revision"]["id"]
    first_analyses = client.get(
        f"/api/assignment-draft-revisions/{first_revision_id}/file-analyses"
    ).json()

    for analysis in first_analyses:
        confirmed_role = analysis["suggested_role"]
        confirmed_answer_source = (
            "third_party" if confirmed_role == "reference_answer" else "not_applicable"
        )
        response = client.patch(
            f"/api/assignment-source-file-analyses/{analysis['id']}/confirmation",
            json={
                "expected_teacher_edit_version": 0,
                "confirmed_role": confirmed_role,
                "confirmed_answer_source": confirmed_answer_source,
            },
        )
        assert response.status_code == 200

    with SessionLocal() as db:
        prior = db.get(
            AssignmentSourceFileAnalysis,
            uuid.UUID(first_analyses[0]["id"]),
        )
        assert prior is not None
        prior.suggested_role = "unknown"
        prior.suggested_answer_source = "unknown"
        db.commit()

    second_job = run_fake(
        assignment.id,
        monkeypatch,
        idempotency_key="metadata-file-analysis-0002",
    )
    second_revision_id = second_job["revision"]["id"]
    second_analyses = client.get(
        f"/api/assignment-draft-revisions/{second_revision_id}/file-analyses"
    ).json()

    assert len(second_analyses) == 2
    assert {row["analysis_status"] for row in second_analyses} == {"confirmed"}
    assert {
        (row["teacher_confirmed_role"], row["teacher_confirmed_answer_source"])
        for row in second_analyses
    } == {
        ("question_paper", "not_applicable"),
        ("reference_answer", "third_party"),
    }
    assert {row["teacher_edit_version"] for row in second_analyses} == {1}


def test_changed_file_requires_file_role_confirmation_again(monkeypatch):
    assignment = source_assignment()
    first_job = run_fake(assignment.id, monkeypatch)
    first_revision_id = first_job["revision"]["id"]
    first_analysis = client.get(
        f"/api/assignment-draft-revisions/{first_revision_id}/file-analyses"
    ).json()[0]
    response = client.patch(
        f"/api/assignment-source-file-analyses/{first_analysis['id']}/confirmation",
        json={
            "expected_teacher_edit_version": 0,
            "confirmed_role": first_analysis["suggested_role"],
            "confirmed_answer_source": "not_applicable",
        },
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        persisted = db.get(StoredFile, uuid.UUID(first_analysis["stored_file_id"]))
        assert persisted is not None
        persisted.checksum = "b" * 64
        db.commit()

    second_job = run_fake(
        assignment.id,
        monkeypatch,
        idempotency_key="metadata-file-analysis-0003",
    )
    second_revision_id = second_job["revision"]["id"]
    second_analyses = client.get(
        f"/api/assignment-draft-revisions/{second_revision_id}/file-analyses"
    ).json()

    matching = next(
        row for row in second_analyses if row["stored_file_id"] == first_analysis["stored_file_id"]
    )
    assert matching["analysis_status"] == "suggested"
    assert matching["teacher_confirmed_role"] is None


def test_deleted_file_is_not_returned_by_file_analysis_list(monkeypatch):
    assignment = source_assignment()
    job = run_fake(assignment.id, monkeypatch)
    revision_id = job["revision"]["id"]
    analyses = client.get(f"/api/assignment-draft-revisions/{revision_id}/file-analyses").json()
    deleted = analyses[0]

    with SessionLocal() as db:
        stored = db.get(StoredFile, uuid.UUID(deleted["stored_file_id"]))
        assert stored is not None
        stored.status = FileStatus.deleted
        db.commit()

    remaining = client.get(f"/api/assignment-draft-revisions/{revision_id}/file-analyses")
    assert remaining.status_code == 200
    assert deleted["stored_file_id"] not in {row["stored_file_id"] for row in remaining.json()}
