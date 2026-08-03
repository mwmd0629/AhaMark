import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from app.api.assignment_generation import dispatch_job
from app.assignment_generation.providers import select_provider
from app.assignment_generation.service import (
    create_job,
    ensure_current,
    transition,
)
from app.assignment_generation.snapshot import canonical_hash, canonical_json, source_snapshot_hash
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AuditLog,
    PaperPage,
    PaperVersion,
    StoredFile,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

from workers.tasks.assignment_generation import _run

client = TestClient(app)


def actor_and_assignment() -> tuple[User, Assignment]:
    client.get("/api/classes")
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
        assert actor is not None
        assignment = Assignment(owner_id=actor.id, title="编排测试")
        db.add(assignment)
        db.commit()
        db.refresh(actor)
        db.refresh(assignment)
        db.expunge(actor)
        db.expunge(assignment)
        return actor, assignment


def start(aid: uuid.UUID, key: str = "generation-key-0001"):
    return client.post(
        f"/api/assignments/{aid}/generation-jobs",
        json={"idempotency_key": key, "provider_mode": "unavailable"},
    )


def test_create_idempotency_concurrency_and_new_generation(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    first = start(assignment.id)
    assert first.status_code == 201
    assert first.json()["generation"] == 1
    assert first.json()["revision"]["revision"] == 1
    assert first.json()["status"] == "queued"

    repeated = start(assignment.id)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["reused"] is True

    concurrent = start(assignment.id, "generation-key-0002")
    assert concurrent.status_code == 409
    assert concurrent.json()["code"] == "GENERATION_ALREADY_ACTIVE"

    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(first.json()["id"]))
        assert job is not None
        job.status = "partial"
        job.progress = 100
        db.commit()
    second = start(assignment.id, "generation-key-0003")
    assert second.status_code == 201
    assert second.json()["generation"] == 2
    assert second.json()["revision"]["revision"] == 2
    assert second.json()["revision"]["parent_revision_id"] == first.json()["revision"]["id"]
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(AuditLog).where(
                    AuditLog.actor_id == actor.id,
                    AuditLog.action == "assignment_generation.create",
                )
            )
            is not None
        )


def test_true_concurrent_generation_start_has_one_active_winner(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    barrier = Barrier(2)

    def submit(key: str) -> tuple[int, str | None]:
        barrier.wait()
        with TestClient(app) as concurrent_client:
            response = concurrent_client.post(
                f"/api/assignments/{assignment.id}/generation-jobs",
                json={"idempotency_key": key, "provider_mode": "unavailable"},
            )
            return response.status_code, response.json().get("code")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("concurrent-key-0001", "concurrent-key-0002")))
    assert sorted(status for status, _code in outcomes) == [201, 409]
    assert next(code for status, code in outcomes if status == 409) in {
        "GENERATION_ALREADY_ACTIVE",
        "GENERATION_CONCURRENT_CONFLICT",
    }
    with SessionLocal() as db:
        active = db.scalars(
            select(AssignmentGenerationJob).where(
                AssignmentGenerationJob.assignment_id == assignment.id,
                AssignmentGenerationJob.status == "queued",
            )
        ).all()
        assert len(active) == 1


def test_idempotency_key_reused_for_changed_request_conflicts(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    assert start(assignment.id).status_code == 201
    with SessionLocal() as db:
        item = db.get(Assignment, assignment.id)
        assert item is not None
        item.title = "教师已修改输入"
        db.commit()
    conflict = start(assignment.id)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_idempotency_key_is_normalized_and_scoped_by_owner(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    created = start(assignment.id, "  normalized-key-0001  ")
    assert created.status_code == 201
    repeated = start(assignment.id, "normalized-key-0001")
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created.json()["id"]
    with SessionLocal() as db:
        other = User(
            email="idempotency-owner@example.com",
            password_hash="not-used",
            display_name="Other",
        )
        db.add(other)
        db.flush()
        other_assignment = Assignment(owner_id=other.id, title="Other assignment")
        db.add(other_assignment)
        db.flush()
        other_job, _revision, reused = create_job(
            db,
            other.id,
            other_assignment.id,
            "normalized-key-0001",
            "unavailable",
            None,
        )
        db.commit()
        assert reused is False
        assert other_job.owner_id != actor.id


def test_state_machine_rejects_illegal_transition():
    job = AssignmentGenerationJob(
        owner_id=uuid.uuid4(),
        assignment_id=uuid.uuid4(),
        generation=1,
        status="queued",
        progress=0,
        idempotency_key="state-machine",
        source_snapshot_hash="0" * 64,
        provider_config_version="v1",
        prompt_version="v1",
        schema_version="v1",
    )
    transition(job, "analyzing")
    assert (job.status, job.progress) == ("analyzing", 10)
    try:
        transition(job, "ready")
    except Exception as exc:
        assert getattr(exc, "code", None) == "GENERATION_INVALID_TRANSITION"
    else:
        raise AssertionError("illegal transition was accepted")


def test_cancel_queued_and_teacher_metadata_concurrency(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    revision = created["revision"]
    changed = client.patch(
        f"/api/assignment-draft-revisions/{revision['id']}/metadata",
        json={
            "expected_teacher_edit_version": 0,
            "label": "教师检查版",
            "notes": "<script>不会作为 HTML 渲染</script>",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["teacher_edit_version"] == 1
    stale_edit = client.patch(
        f"/api/assignment-draft-revisions/{revision['id']}/metadata",
        json={"expected_teacher_edit_version": 0, "label": "覆盖"},
    )
    assert stale_edit.status_code == 409
    assert stale_edit.json()["code"] == "DRAFT_MODIFIED_BY_TEACHER"
    cancelled = client.post(f"/api/assignment-generation-jobs/{created['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    with SessionLocal() as db:
        actions = set(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.resource_id.in_([created["id"], revision["id"]])
                )
            ).all()
        )
        assert "assignment_generation.cancel" in actions
        assert "assignment_draft.metadata_update" in actions


def test_true_concurrent_metadata_patch_allows_only_one_writer(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    revision_id = start(assignment.id).json()["revision"]["id"]
    barrier = Barrier(2)

    def patch(label: str) -> tuple[int, str | None]:
        barrier.wait()
        with TestClient(app) as concurrent_client:
            response = concurrent_client.patch(
                f"/api/assignment-draft-revisions/{revision_id}/metadata",
                json={"expected_teacher_edit_version": 0, "label": label},
            )
            return response.status_code, response.json().get("code")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(patch, ("writer-a", "writer-b")))
    assert sorted(status for status, _code in outcomes) == [200, 409]
    assert next(code for status, code in outcomes if status == 409) == "DRAFT_MODIFIED_BY_TEACHER"
    with SessionLocal() as db:
        revision = db.get(AssignmentDraftRevision, uuid.UUID(revision_id))
        assert revision is not None
        assert revision.teacher_edit_version == 1


def test_worker_unavailable_finishes_partial_with_audit_records(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    result = _run(created["id"], None)
    assert result["status"] == "partial"
    detail = client.get(f"/api/assignment-generation-jobs/{created['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "partial"
    assert body["progress"] == 100
    assert {row["stage"] for row in body["stages"]} == {
        "analyzing",
        "processing_pages",
        "extracting_questions",
        "generating_rubrics",
        "validating",
    }
    assert any(row["code"] == "PROVIDER_UNAVAILABLE" for row in body["issues"])
    assert any(row["code"] == "GENERATION_PARTIAL" for row in body["issues"])
    assert body["revision"]["risk_summary"]["blocking"] >= 1
    with SessionLocal() as db:
        assignment_row = db.get(Assignment, assignment.id)
        assert assignment_row is not None
        assert assignment_row.status == "draft"

    duplicate = _run(created["id"], None)
    assert duplicate["status"] == "duplicate_delivery"
    detail_after_duplicate = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    assert len(detail_after_duplicate["stages"]) == 5


def test_source_change_marks_stale_and_late_worker_cannot_restore(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    with SessionLocal() as db:
        item = db.get(Assignment, assignment.id)
        assert item is not None
        item.instructions = "教师新增要求"
        db.commit()
    assert _run(created["id"], None)["status"] == "source_changed"
    assert _run(created["id"], None)["status"] == "discarded_late"
    body = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    assert body["status"] == "stale"
    assert sum(row["code"] == "SOURCE_CHANGED" for row in body["issues"]) == 1


def test_retry_stage_appends_generation_and_keeps_history(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    _run(created["id"], None)
    first = [
        row
        for row in client.get(f"/api/assignment-generation-jobs/{created['id']}").json()["stages"]
        if row["stage"] == "extracting_questions"
    ][0]
    retried = client.post(
        f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
        json={"stage": "extracting_questions"},
    )
    assert retried.status_code == 200
    _run(created["id"], "extracting_questions")
    rows = [
        row
        for row in client.get(f"/api/assignment-generation-jobs/{created['id']}").json()["stages"]
        if row["stage"] == "extracting_questions"
    ]
    assert [row["stage_generation"] for row in rows] == [1, 2]
    assert rows[0]["id"] == first["id"]
    detail = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    assert detail["retryable"] is True
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        assert job is not None
        assert job.attempt == 1


def test_retry_budget_is_independent_for_sequential_stages(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    _run(created["id"], None)

    for stage in ("extracting_questions", "generating_rubrics"):
        retried = client.post(
            f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
            json={"stage": stage},
        )
        assert retried.status_code == 200
        assert _run(created["id"], stage)["status"] == "partial"

    detail = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    by_stage = {
        stage: [row["stage_generation"] for row in detail["stages"] if row["stage"] == stage]
        for stage in ("extracting_questions", "generating_rubrics")
    }
    assert by_stage == {
        "extracting_questions": [1, 2],
        "generating_rubrics": [1, 2],
    }
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        assert job is not None
        assert job.attempt == 1


def test_retry_budget_is_enforced_per_stage(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    _run(created["id"], None)
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        assert job is not None
        job.max_attempts = 2
        db.commit()

    first_retry = client.post(
        f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
        json={"stage": "extracting_questions"},
    )
    assert first_retry.status_code == 200
    _run(created["id"], "extracting_questions")

    exhausted = client.post(
        f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
        json={"stage": "extracting_questions"},
    )
    assert exhausted.status_code == 409
    assert exhausted.json()["code"] == "GENERATION_MAX_ATTEMPTS_REACHED"


def test_worker_edit_version_guard_and_snapshot_stability(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        job, revision, _ = create_job(
            db, actor.id, assignment.id, "direct-create-key", "unavailable", None
        )
        db.commit()
        first = source_snapshot_hash(db, db.get(Assignment, assignment.id))
        second = source_snapshot_hash(db, db.get(Assignment, assignment.id))
        assert first == second
        expected = revision.teacher_edit_version
        revision.teacher_edit_version += 1
        db.commit()
        assert ensure_current(db, job, revision, expected) == "DRAFT_MODIFIED_BY_TEACHER"
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    assert canonical_hash({"a": 1}) == canonical_hash({"a": 1})


def test_snapshot_changes_for_file_page_and_config_inputs(monkeypatch):
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=f"snapshot/{uuid.uuid4()}",
            original_name="paper.pdf",
            content_type="application/pdf",
            size=128,
            checksum="a" * 64,
            status="ready",
        )
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status="draft",
            source_type="manual",
            created_by=actor.id,
        )
        db.add_all([stored, paper])
        db.flush()
        page = PaperPage(
            paper_version_id=paper.id,
            stored_file_id=stored.id,
            page_number=1,
            source_page_number=1,
            rotation=0,
            status="ready",
        )
        row = db.get(Assignment, assignment.id)
        assert row is not None
        row.active_paper_version_id = paper.id
        db.add(page)
        db.commit()
        baseline = source_snapshot_hash(db, row)

        stored.checksum = "b" * 64
        db.flush()
        assert source_snapshot_hash(db, row) != baseline
        stored.checksum = "a" * 64
        page.rotation = 90
        db.flush()
        assert source_snapshot_hash(db, row) != baseline
        page.rotation = 0
        db.flush()
        assert source_snapshot_hash(db, row) == baseline

        monkeypatch.setattr(
            "app.assignment_generation.snapshot.get_settings",
            lambda: SimpleNamespace(
                assignment_generation_provider_config_version="changed-provider-config",
                assignment_generation_prompt_version="assignment-generation-prompt-v1",
                assignment_generation_schema_version="assignment-generation-schema-v1",
            ),
        )
        assert source_snapshot_hash(db, row) != baseline


def test_production_fake_degrades_to_unavailable():
    settings = SimpleNamespace(
        assignment_generation_provider="fake",
        app_env="production",
    )
    provider = select_provider(settings, "fake")
    assert provider.name == "unavailable"
    assert provider.available is False
    assert provider.error_code == "FAKE_PROVIDER_DISABLED_IN_PRODUCTION"


def test_capabilities_are_server_owned_and_default_safe():
    response = client.get("/api/assignment-generation-capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "provider": "unavailable",
        "provider_status": "unavailable",
        "provider_error_code": "PROVIDER_UNAVAILABLE",
        "external_provider_requests": False,
        "teacher_start_allowed": True,
        "suggestion_only": True,
        "real_provider_quality_passed": False,
    }


def test_published_assignment_rejects_generation_before_job_creation(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        row = db.get(Assignment, assignment.id)
        assert row is not None
        row.status = "published"
        db.commit()
    response = start(assignment.id, "published-generation-key")
    assert response.status_code == 409
    assert response.json()["code"] == "ASSIGNMENT_ALREADY_PUBLISHED"
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(AssignmentGenerationJob.id).where(
                    AssignmentGenerationJob.assignment_id == assignment.id
                )
            )
            is None
        )


def test_dispatch_failure_maps_to_stable_failure(monkeypatch):
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        job, _revision, _ = create_job(
            db, actor.id, assignment.id, "dispatch-failure-key", "unavailable", None
        )
        db.commit()
        monkeypatch.setattr(
            "workers.celery_app.celery_app.send_task",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker secret")),
        )
        dispatch_job(db, job)
        assert job.status == "failed"
        assert job.error_code == "WORKER_UNAVAILABLE"
        assert "secret" not in (job.error_message or "")


def test_owner_isolation_for_job_and_revision():
    from app.api.domain import ApiProblem
    from app.assignment_generation.service import owned_job, owned_revision

    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        job, revision, _ = create_job(
            db, actor.id, assignment.id, "owner-isolation-key", "unavailable", None
        )
        other = User(
            email="other-generation-owner@example.com",
            password_hash="not-used",
            display_name="Other",
        )
        db.add(other)
        db.commit()
        for lookup, identifier in ((owned_job, job.id), (owned_revision, revision.id)):
            try:
                lookup(db, other.id, identifier)
            except ApiProblem as exc:
                assert exc.status == 404
            else:
                raise AssertionError("cross-owner generation resource leaked")


def test_activate_is_draft_only_and_audited(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    revision_id = created["revision"]["id"]
    premature = client.post(f"/api/assignment-draft-revisions/{revision_id}/activate")
    assert premature.status_code == 409
    assert premature.json()["code"] == "DRAFT_NOT_ACTIVATABLE"
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        revision = db.get(AssignmentDraftRevision, uuid.UUID(revision_id))
        assert job is not None and revision is not None
        job.status = "review_required"
        job.progress = 100
        revision.status = "review_required"
        db.commit()
    activated = client.post(f"/api/assignment-draft-revisions/{revision_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    with SessionLocal() as db:
        assignment_row = db.get(Assignment, assignment.id)
        assert assignment_row is not None
        assert assignment_row.status == "draft"
        assert (
            db.scalar(
                select(AuditLog).where(
                    AuditLog.actor_id == actor.id,
                    AuditLog.action == "assignment_draft.activate",
                    AuditLog.resource_id == revision_id,
                )
            )
            is not None
        )


def test_running_cancel_is_observed_before_worker_write(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        assert job is not None
        job.status = "analyzing"
        job.current_stage = "analyzing"
        job.progress = 10
        db.commit()
    response = client.post(f"/api/assignment-generation-jobs/{created['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["cancel_requested_at"] is not None
    assert _run(created["id"], None)["status"] == "cancel_requested"
    final = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    assert final["status"] == "cancelled"
    assert final["stages"] == []
