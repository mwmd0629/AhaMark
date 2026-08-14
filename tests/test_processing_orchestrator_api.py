from __future__ import annotations

import uuid
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from app.api.actor import CurrentActor, get_current_actor
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    Assignment,
    AssignmentStatus,
    CodexWorkItem,
    GradeRelease,
    GradingBatch,
    PaperVersion,
    ProcessingRun,
    ProcessingRunCommand,
    ProcessingStep,
    Question,
    SchoolClass,
    Status,
    Student,
    StudentAnswer,
    Submission,
    SubmissionProcessingJob,
    SubmissionRecognitionJob,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
    now_utc,
)
from app.processing import codex_local, orchestrator
from app.processing.automatic_confirmation import AutomaticConfirmationDecision
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _seed_batch(db: Session, *, submission_count: int = 2) -> tuple[User, GradingBatch]:
    actor = User(
        email=f"processing-{uuid.uuid4()}@example.invalid",
        password_hash="!",
        display_name="Processing test",
        status=Status.active,
    )
    db.add(actor)
    db.flush()
    school_class = SchoolClass(owner_id=actor.id, name=f"Class {uuid.uuid4()}")
    assignment = Assignment(
        owner_id=actor.id,
        title="Synthetic processing assignment",
        status=AssignmentStatus.published,
    )
    db.add_all([school_class, assignment])
    db.flush()
    batch = GradingBatch(
        owner_id=actor.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        name="Synthetic processing batch",
    )
    db.add(batch)
    db.flush()
    for index in range(submission_count):
        student = Student(
            owner_id=actor.id,
            student_number=f"S-{uuid.uuid4()}",
            name=f"Synthetic student {index}",
        )
        db.add(student)
        db.flush()
        db.add(
            Submission(
                owner_id=actor.id,
                grading_batch_id=batch.id,
                assignment_id=assignment.id,
                class_id=school_class.id,
                student_id=student.id,
                status="uploaded",
            )
        )
    db.commit()
    db.refresh(actor)
    db.refresh(batch)
    return actor, batch


def _manifest_for(db: Session, batch: GradingBatch) -> dict[str, Any]:
    submission_ids = list(
        db.scalars(
            select(Submission.id)
            .where(Submission.grading_batch_id == batch.id)
            .order_by(Submission.id)
        )
    )
    included = [
        {
            "submission_id": str(submission_id),
            "status": "uploaded",
            "answers": [
                {
                    "answer_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"answer:{submission_id}")),
                    "question_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"question:{submission_id}")),
                    "input_version": "b" * 64,
                }
            ],
            "blockers": [],
        }
        for submission_id in submission_ids
    ]
    return {
        "schema": "processing-manifest-v1",
        "batch_id": str(batch.id),
        "included": included,
        "excluded": [],
        "input_version": "a" * 64,
    }


@pytest.fixture
def processing_case(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[Session, User, GradingBatch, TestClient], None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine, expire_on_commit=False)
    db = local_session()
    actor, batch = _seed_batch(db)

    def override_db() -> Generator[Session, None, None]:
        yield db

    monkeypatch.setattr(
        orchestrator,
        "_manifest",
        lambda session, owner_id, current_batch: _manifest_for(session, current_batch),
    )
    # These orchestrator contract tests use synthetic manifest-only answer ids.
    # Codex work-item materialization has its own tests with complete scoring inputs.
    monkeypatch.setattr(orchestrator, "materialize_work_items", lambda *_args, **_kwargs: [])
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_actor] = lambda: CurrentActor(actor.id, actor.email)
    with TestClient(app) as test_client:
        yield db, actor, batch, test_client
    app.dependency_overrides.pop(get_current_actor, None)
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def _post_continue(client: TestClient, batch: GradingBatch, key: str) -> Any:
    return client.post(
        f"/api/grading-batches/{batch.id}/processing-runs",
        json={"idempotency_key": key},
    )


def _formal_counts(db: Session) -> tuple[int, int, int]:
    return (
        db.scalar(select(func.count()).select_from(TeacherReview)) or 0,
        db.scalar(select(func.count()).select_from(SubmissionScoreSnapshot)) or 0,
        db.scalar(select(func.count()).select_from(GradeRelease)) or 0,
    )


def test_continue_get_serializer_and_active_plan_alias(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, actor, batch, client = processing_case

    empty_latest = client.get(f"/api/grading-batches/{batch.id}/processing-runs/latest")
    assert empty_latest.status_code == 200
    assert empty_latest.json() is None

    created = _post_continue(client, batch, "continue-1")
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["provider"] == "codex_local"
    assert payload["provider_label"] == "Codex-assisted"
    assert payload["suggestion_only"] is True
    assert payload["target_state"] == "awaiting_teacher_review"
    assert payload["generation"] == 1
    assert len(payload["steps"]) == 2

    fetched = client.get(f"/api/grading-batches/{batch.id}/processing-runs/{payload['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == payload
    latest = client.get(f"/api/grading-batches/{batch.id}/processing-runs/latest")
    assert latest.status_code == 200, latest.text
    assert latest.json() == payload

    replay = _post_continue(client, batch, "continue-1")
    alias = _post_continue(client, batch, "continue-2")
    assert replay.status_code == alias.status_code == 201
    assert replay.json()["id"] == alias.json()["id"] == payload["id"]
    assert db.scalar(select(func.count()).select_from(ProcessingRun)) == 1
    assert db.scalar(select(func.count()).select_from(ProcessingRunCommand)) == 2
    commands = list(
        db.scalars(select(ProcessingRunCommand).order_by(ProcessingRunCommand.idempotency_key))
    )
    assert {command.result_run_id for command in commands} == {uuid.UUID(payload["id"])}
    assert all(command.owner_id == actor.id for command in commands)
    assert _formal_counts(db) == (0, 0, 0)


def test_continue_plans_one_codex_step_per_answer_without_losing_scope(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _actor, batch, client = processing_case
    submission_id = db.scalar(
        select(Submission.id)
        .where(Submission.grading_batch_id == batch.id)
        .order_by(Submission.id)
        .limit(1)
    )
    assert submission_id is not None
    answer_ids = [uuid.uuid4(), uuid.uuid4()]
    question_ids = [uuid.uuid4(), uuid.uuid4()]
    manifest = {
        "schema": "processing-manifest-v1",
        "batch_id": str(batch.id),
        "included": [
            {
                "submission_id": str(submission_id),
                "status": "recognized",
                "answers": [
                    {
                        "answer_id": str(answer_id),
                        "question_id": str(question_id),
                        "input_version": f"{index + 1}" * 64,
                    }
                    for index, (answer_id, question_id) in enumerate(
                        zip(answer_ids, question_ids, strict=True)
                    )
                ],
                "blockers": [],
            }
        ],
        "excluded": [],
        "input_version": "c" * 64,
    }
    monkeypatch.setattr(orchestrator, "_manifest", lambda *_args, **_kwargs: manifest)

    response = _post_continue(client, batch, "per-answer")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["submission_count"] == 1
    assert payload["step_count"] == 2
    assert len(payload["steps"]) == 2
    assert {step["kind"] for step in payload["steps"]} == {"codex_suggestion"}
    assert {step["submission_id"] for step in payload["steps"]} == {str(submission_id)}
    assert {step["student_answer_id"] for step in payload["steps"]} == {
        str(answer_id) for answer_id in answer_ids
    }
    assert {step["scope_key"] for step in payload["steps"]} == {
        f"answer:{answer_id}" for answer_id in answer_ids
    }


def test_continue_input_drift_stales_old_children_and_clears_leases(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, actor, batch, client = processing_case
    first = _post_continue(client, batch, "drift-first").json()
    old_run = db.get(ProcessingRun, uuid.UUID(first["id"]))
    assert old_run is not None
    old_step = old_run.steps[0]
    assert old_step.student_answer_id is not None
    old_step.status = "dispatched"
    old_step.dispatch_token = "drift-dispatch-token"
    old_step.dispatch_owner = "drift-dispatch-owner"
    old_step.dispatch_lease_expires_at = now_utc()
    work_item = CodexWorkItem(
        processing_step_id=old_step.id,
        owner_id=actor.id,
        grading_batch_id=batch.id,
        submission_id=old_step.submission_id,
        student_answer_id=old_step.student_answer_id,
        status="leased",
        generation=1,
        input_version=old_step.input_version,
        request_hash=old_step.request_hash,
        request_payload={"synthetic": True},
        provider="codex_local",
        prompt_version="codex-local-v1",
        schema_version="grading-suggestion-v1",
        config_version="suggestion-only-v1",
        lease_token_hash="d" * 64,
        lease_owner="drift-work-owner",
        lease_expires_at=now_utc(),
    )
    db.add(work_item)
    db.commit()

    changed_manifest = _manifest_for(db, batch)
    changed_manifest["input_version"] = "d" * 64
    monkeypatch.setattr(orchestrator, "_manifest", lambda *_args, **_kwargs: changed_manifest)
    second = _post_continue(client, batch, "drift-second")
    assert second.status_code == 201, second.text
    assert second.json()["generation"] == 2

    stale_run = db.get(ProcessingRun, old_run.id)
    stale_step = db.get(ProcessingStep, old_step.id)
    stale_work_item = db.get(CodexWorkItem, work_item.id)
    assert stale_run is not None
    assert stale_step is not None
    assert stale_work_item is not None
    assert stale_run.status == "stale"
    assert stale_run.stale_at is not None
    assert stale_step.status == "stale"
    assert stale_step.stale_at is not None
    assert (
        stale_step.dispatch_token,
        stale_step.dispatch_owner,
        stale_step.dispatch_lease_expires_at,
    ) == (None, None, None)
    assert stale_work_item.status == "stale"
    assert stale_work_item.stale_at is not None
    assert (
        stale_work_item.lease_token_hash,
        stale_work_item.lease_owner,
        stale_work_item.lease_expires_at,
    ) == (None, None, None)


def test_idempotency_key_rejects_a_different_command_body(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, _actor, batch, client = processing_case
    created = _post_continue(client, batch, "shared-key")
    run_id = created.json()["id"]

    conflict = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{run_id}/reconcile",
        json={"idempotency_key": "shared-key", "expected_generation": 1},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert db.scalar(select(func.count()).select_from(ProcessingRunCommand)) == 1
    assert _formal_counts(db) == (0, 0, 0)


def test_idempotency_key_is_trimmed_and_unicode_whitespace_is_rejected(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, _actor, batch, client = processing_case

    created = _post_continue(client, batch, "  normalized-key\t")
    assert created.status_code == 201, created.text
    command = db.scalar(
        select(ProcessingRunCommand).where(ProcessingRunCommand.idempotency_key == "normalized-key")
    )
    assert command is not None

    rejected = _post_continue(client, batch, "\u3000\t\n")
    assert rejected.status_code == 422, rejected.text
    assert db.scalar(select(func.count()).select_from(ProcessingRunCommand)) == 1


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("PROCESSING_INPUT_STALE", "Synthetic input changed"),
        ("SUBMISSION_SCOPE_MISMATCH", "Synthetic scope mismatch"),
    ],
)
def test_continue_fails_closed_without_partial_orchestration_rows(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    message: str,
) -> None:
    db, _actor, batch, client = processing_case

    def fail_manifest(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise orchestrator.OrchestratorProblem(409, code, message)

    monkeypatch.setattr(orchestrator, "_manifest", fail_manifest)
    before = (
        db.scalar(select(func.count()).select_from(ProcessingRun)),
        db.scalar(select(func.count()).select_from(ProcessingRunCommand)),
        db.scalar(select(func.count()).select_from(ProcessingStep)),
    )

    response = _post_continue(client, batch, f"fail-closed-{code}")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code
    after = (
        db.scalar(select(func.count()).select_from(ProcessingRun)),
        db.scalar(select(func.count()).select_from(ProcessingRunCommand)),
        db.scalar(select(func.count()).select_from(ProcessingStep)),
    )
    assert after == before == (0, 0, 0)


def test_retry_scope_generation_sorted_ids_and_old_key_replay(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, _actor, batch, client = processing_case
    first = _post_continue(client, batch, "original-continue").json()
    old_run_id = uuid.UUID(first["id"])
    old_steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == old_run_id)
            .order_by(ProcessingStep.id)
        )
    )
    assert len(old_steps) == 2
    for step in old_steps:
        step.status = "retryable_failed"
        step.retryable = True
        step.error_code = "SYNTHETIC_RETRY"
    old_run = db.get(ProcessingRun, old_run_id)
    assert old_run is not None
    old_run.status = "failed"
    db.commit()

    selected_id = old_steps[1].id
    retried = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{old_run_id}/retry",
        json={
            "idempotency_key": "retry-1",
            "expected_generation": 1,
            "step_ids": [str(selected_id)],
        },
    )
    assert retried.status_code == 201, retried.text
    payload = retried.json()
    assert payload["generation"] == 2
    assert payload["id"] != str(old_run_id)
    new_steps = {item["submission_id"]: item for item in payload["steps"]}
    retried_submission_id = str(old_steps[1].submission_id)
    untouched_submission_id = str(old_steps[0].submission_id)
    assert new_steps[retried_submission_id]["status"] == "pending"
    assert new_steps[retried_submission_id]["attempt"] == 1
    assert new_steps[retried_submission_id]["error_code"] is None
    assert new_steps[untouched_submission_id]["status"] == "retryable_failed"
    assert new_steps[untouched_submission_id]["attempt"] == 0

    historical = _post_continue(client, batch, "original-continue")
    assert historical.status_code == 201
    assert historical.json()["id"] == str(old_run_id)

    retry_command = db.scalar(
        select(ProcessingRunCommand).where(ProcessingRunCommand.idempotency_key == "retry-1")
    )
    assert retry_command is not None
    assert retry_command.request_payload["step_ids"] == [str(selected_id)]
    assert _formal_counts(db) == (0, 0, 0)


def test_retry_rejects_a_run_with_any_active_step_without_writes(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, _actor, batch, client = processing_case
    created = _post_continue(client, batch, "active-retry-seed").json()
    run_id = uuid.UUID(created["id"])
    steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == run_id)
            .order_by(ProcessingStep.id)
        )
    )
    steps[0].status = "retryable_failed"
    steps[0].retryable = True
    steps[1].status = "running"
    run = db.get(ProcessingRun, run_id)
    assert run is not None
    run.status = "running"
    db.commit()
    before_runs = db.scalar(select(func.count()).select_from(ProcessingRun))
    before_commands = db.scalar(select(func.count()).select_from(ProcessingRunCommand))

    response = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{run_id}/retry",
        json={
            "idempotency_key": "active-retry",
            "expected_generation": 1,
            "step_ids": [str(steps[0].id)],
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "PROCESSING_RUN_ACTIVE"
    assert db.scalar(select(func.count()).select_from(ProcessingRun)) == before_runs
    assert db.scalar(select(func.count()).select_from(ProcessingRunCommand)) == before_commands
    active_step = db.get(ProcessingStep, steps[1].id)
    assert active_step is not None
    assert active_step.status == "running"


def test_retry_stales_old_children_clears_leases_and_sets_new_counters(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, actor, batch, client = processing_case
    created = _post_continue(client, batch, "lease-retry-seed").json()
    source_run_id = uuid.UUID(created["id"])
    source_run = db.get(ProcessingRun, source_run_id)
    assert source_run is not None
    steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == source_run_id)
            .order_by(ProcessingStep.id)
        )
    )
    selected, completed = steps
    selected.status = "retryable_failed"
    selected.retryable = True
    selected.error_code = "SYNTHETIC_RETRY"
    selected.dispatch_token = "old-dispatch-token"
    selected.dispatch_owner = "old-dispatch-owner"
    selected.dispatch_lease_expires_at = now_utc()
    completed.status = "succeeded"
    completed.completed_at = now_utc()
    source_run.status = "partially_failed"
    work_item = CodexWorkItem(
        processing_step_id=selected.id,
        owner_id=actor.id,
        grading_batch_id=batch.id,
        submission_id=selected.submission_id,
        student_answer_id=selected.student_answer_id,
        status="leased",
        generation=1,
        input_version=selected.input_version,
        request_hash=selected.request_hash,
        request_payload={"synthetic": True},
        provider="codex_local",
        prompt_version="codex-local-v1",
        schema_version="grading-suggestion-v1",
        config_version="suggestion-only-v1",
        lease_token_hash="e" * 64,
        lease_owner="old-work-owner",
        lease_expires_at=now_utc(),
    )
    assert selected.student_answer_id is not None
    db.add(work_item)
    db.commit()
    monkeypatch.setattr(orchestrator, "materialize_work_items", codex_local.materialize_work_items)
    monkeypatch.setattr(
        codex_local,
        "build_work_request",
        lambda *_args, **_kwargs: (
            {"schema": "codex-work-request-v1", "suggestion_only": True},
            selected.input_version,
            "f" * 64,
        ),
    )

    response = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{source_run_id}/retry",
        json={
            "idempotency_key": "lease-retry",
            "expected_generation": 1,
            "step_ids": [str(selected.id)],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["generation"] == 2
    assert payload["status"] == "waiting_codex"
    assert payload["completed_step_count"] == 1
    assert payload["failed_step_count"] == 0
    assert payload["pending_codex_count"] == 1
    copied = {step["scope_key"]: step for step in payload["steps"]}
    assert copied[selected.scope_key]["status"] == "pending"
    assert copied[selected.scope_key]["attempt"] == 1
    assert copied[selected.scope_key]["error_code"] is None
    assert copied[completed.scope_key]["status"] == "succeeded"
    queued_items = list(
        db.scalars(
            select(CodexWorkItem).where(
                CodexWorkItem.generation == 2,
                CodexWorkItem.status == "queued",
            )
        )
    )
    assert len(queued_items) == 1
    assert queued_items[0].processing_step_id == uuid.UUID(copied[selected.scope_key]["id"])
    replay = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{source_run_id}/retry",
        json={
            "idempotency_key": "lease-retry",
            "expected_generation": 1,
            "step_ids": [str(selected.id)],
        },
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == payload["id"]
    assert (
        db.scalar(
            select(func.count()).select_from(CodexWorkItem).where(CodexWorkItem.generation == 2)
        )
        == 1
    )

    stale_run = db.get(ProcessingRun, source_run.id)
    stale_step = db.get(ProcessingStep, selected.id)
    stale_work_item = db.get(CodexWorkItem, work_item.id)
    assert stale_run is not None
    assert stale_step is not None
    assert stale_work_item is not None
    assert stale_run.status == "stale"
    assert stale_run.stale_at is not None
    assert stale_step.status == "stale"
    assert stale_step.stale_at is not None
    assert (
        stale_step.dispatch_token,
        stale_step.dispatch_owner,
        stale_step.dispatch_lease_expires_at,
    ) == (None, None, None)
    assert stale_work_item.status == "stale"
    assert stale_work_item.stale_at is not None
    assert (
        stale_work_item.lease_token_hash,
        stale_work_item.lease_owner,
        stale_work_item.lease_expires_at,
    ) == (None, None, None)


def test_retry_rejects_a_step_from_another_batch_without_new_generation(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, actor, batch, client = processing_case
    source = _post_continue(client, batch, "scope-source").json()
    source_run = db.get(ProcessingRun, uuid.UUID(source["id"]))
    assert source_run is not None
    for step in source_run.steps:
        step.status = "retryable_failed"
        step.retryable = True
    source_run.status = "failed"

    foreign_batch = GradingBatch(
        owner_id=actor.id,
        assignment_id=batch.assignment_id,
        class_id=batch.class_id,
        name="Foreign synthetic processing batch",
    )
    db.add(foreign_batch)
    db.flush()
    foreign_student = Student(
        owner_id=actor.id,
        student_number=f"S-{uuid.uuid4()}",
        name="Foreign synthetic student",
    )
    db.add(foreign_student)
    db.flush()
    db.add(
        Submission(
            owner_id=actor.id,
            grading_batch_id=foreign_batch.id,
            assignment_id=batch.assignment_id,
            class_id=batch.class_id,
            student_id=foreign_student.id,
            status="uploaded",
        )
    )
    db.commit()
    foreign = _post_continue(client, foreign_batch, "scope-foreign").json()
    foreign_step_id = foreign["steps"][0]["id"]
    before_runs = db.scalar(select(func.count()).select_from(ProcessingRun))

    rejected = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{source['id']}/retry",
        json={
            "idempotency_key": "scope-invalid",
            "expected_generation": 1,
            "step_ids": [foreign_step_id],
        },
    )
    assert rejected.status_code == 404, rejected.text
    assert rejected.json()["code"] == "PROCESSING_STEP_NOT_FOUND"
    assert db.scalar(select(func.count()).select_from(ProcessingRun)) == before_runs
    assert (
        db.scalar(
            select(func.count())
            .select_from(ProcessingRunCommand)
            .where(ProcessingRunCommand.idempotency_key == "scope-invalid")
        )
        == 0
    )


def test_retry_rejects_duplicate_step_ids_at_api_boundary(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, _actor, batch, client = processing_case
    first = _post_continue(client, batch, "duplicate-seed").json()
    step_id = first["steps"][0]["id"]

    rejected = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{first['id']}/retry",
        json={
            "idempotency_key": "duplicate-retry",
            "expected_generation": 1,
            "step_ids": [step_id, step_id],
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert (
        db.scalar(
            select(func.count())
            .select_from(ProcessingRunCommand)
            .where(ProcessingRunCommand.idempotency_key == "duplicate-retry")
        )
        == 0
    )


def test_retry_normalizes_step_id_order_in_the_command_hash(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, _actor, batch, client = processing_case
    first = _post_continue(client, batch, "sorted-seed").json()
    run_id = uuid.UUID(first["id"])
    steps = list(
        db.scalars(select(ProcessingStep).where(ProcessingStep.processing_run_id == run_id))
    )
    for step in steps:
        step.status = "retryable_failed"
        step.retryable = True
    run = db.get(ProcessingRun, run_id)
    assert run is not None
    run.status = "failed"
    db.commit()
    descending = sorted((step.id for step in steps), key=str, reverse=True)

    retried = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{run_id}/retry",
        json={
            "idempotency_key": "sorted-retry",
            "expected_generation": 1,
            "step_ids": [str(step_id) for step_id in descending],
        },
    )
    assert retried.status_code == 201, retried.text
    command = db.scalar(
        select(ProcessingRunCommand).where(ProcessingRunCommand.idempotency_key == "sorted-retry")
    )
    assert command is not None
    assert command.request_payload["step_ids"] == sorted(str(step_id) for step_id in descending)


def test_reconcile_uses_complete_state_priority_and_never_writes_grades(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, actor, batch, client = processing_case
    created = _post_continue(client, batch, "reconcile-seed").json()
    run_id = uuid.UUID(created["id"])
    steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == run_id)
            .order_by(ProcessingStep.id)
        )
    )
    assert all(step.student_answer_id is not None for step in steps)
    work_item = CodexWorkItem(
        processing_step_id=steps[0].id,
        owner_id=actor.id,
        grading_batch_id=batch.id,
        submission_id=steps[0].submission_id,
        student_answer_id=steps[0].student_answer_id,
        status="stale",
        generation=1,
        input_version=steps[0].input_version,
        request_hash=steps[0].request_hash,
        request_payload={"synthetic": True},
        provider="codex_local",
        prompt_version="codex-local-v1",
        schema_version="grading-suggestion-v1",
        config_version="suggestion-only-v1",
        stale_at=now_utc(),
    )
    db.add(work_item)
    db.commit()

    def reconcile(
        key: str,
        statuses: tuple[str, str],
        *,
        work_status: str = "stale",
    ) -> dict[str, Any]:
        steps[0].status, steps[1].status = statuses
        work_item.status = work_status
        work_item.stale_at = now_utc() if work_status == "stale" else None
        db.commit()
        response = client.post(
            f"/api/grading-batches/{batch.id}/processing-runs/{run_id}/reconcile",
            json={"idempotency_key": key, "expected_generation": 1},
        )
        assert response.status_code == 200, response.text
        return cast(dict[str, Any], response.json())

    active = reconcile("reconcile-stale", ("running", "retryable_failed"))
    assert active["status"] == "failed"
    assert active["completed_step_count"] == 0
    assert active["failed_step_count"] == 1

    waiting_codex = reconcile(
        "reconcile-codex",
        ("blocked_review", "retryable_failed"),
        work_status="queued",
    )
    assert waiting_codex["status"] == "waiting_codex"
    assert waiting_codex["pending_codex_count"] == 1

    blocked = reconcile("reconcile-blocked", ("blocked_review", "retryable_failed"))
    assert blocked["status"] == "failed"
    assert blocked["failed_step_count"] == 1

    partial = reconcile("reconcile-partial", ("succeeded", "retryable_failed"))
    assert partial["status"] == "failed"
    assert partial["completed_step_count"] == 0
    assert partial["failed_step_count"] == 1

    failed = reconcile("reconcile-failed", ("terminal_failed", "retryable_failed"))
    assert failed["status"] == "failed"
    assert failed["completed_step_count"] == 0
    assert failed["failed_step_count"] == 1

    assert db.scalar(select(func.count()).select_from(CodexWorkItem)) == 1
    assert _formal_counts(db) == (0, 0, 0)


def _recognition_run_and_steps(
    db: Session, actor: User, batch: GradingBatch
) -> tuple[ProcessingRun, list[ProcessingStep]]:
    submissions = list(
        db.scalars(
            select(Submission)
            .where(Submission.grading_batch_id == batch.id)
            .order_by(Submission.id)
        )
    )
    run = ProcessingRun(
        owner_id=actor.id,
        grading_batch_id=batch.id,
        status="waiting_input",
        mode="codex_local",
        generation=1,
        input_version="1" * 64,
        request_hash="2" * 64,
        input_manifest={"synthetic": True},
        submission_count=len(submissions),
        step_count=len(submissions),
    )
    db.add(run)
    db.flush()
    steps = [
        ProcessingStep(
            processing_run_id=run.id,
            submission_id=submission.id,
            scope_key=f"submission:{submission.id}",
            kind="recognition",
            status="blocked_review",
            generation=1,
            input_version="3" * 64,
            request_hash="4" * 64,
            error_code="RECOGNITION_EVIDENCE_NOT_CONFIRMED",
            retryable=False,
        )
        for submission in submissions
    ]
    db.add_all(steps)
    db.flush()
    return run, steps


def test_recognition_job_materialization_is_durable_and_idempotent(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, actor, batch, _ = processing_case
    run, steps = _recognition_run_and_steps(db, actor, batch)
    eligible_submission = steps[0].submission_id
    region = SimpleNamespace(
        id=uuid.uuid4(),
        region_version=1,
        segmentation_version="submission-seg-v1",
    )
    monkeypatch.setattr(
        orchestrator,
        "_recognition_input",
        lambda _db, submission_id: ([], [region]) if submission_id == eligible_submission else None,
    )

    created = orchestrator._materialize_recognition_jobs(db, run=run, steps=steps)
    assert len(created) == 1
    assert steps[0].recognition_job_id == created[0]
    assert steps[0].status == "dispatched"
    assert steps[0].error_code is None
    assert steps[1].recognition_job_id is None
    assert steps[1].status == "blocked_review"
    assert steps[1].error_code == "RECOGNITION_EVIDENCE_NOT_CONFIRMED"
    db.commit()

    replay = orchestrator._materialize_recognition_jobs(db, run=run, steps=steps)
    assert replay == []
    assert db.scalar(select(func.count()).select_from(SubmissionRecognitionJob)) == 1

    job = db.get(SubmissionRecognitionJob, created[0])
    assert job is not None
    job.max_attempts += 1
    steps[0].recognition_job_id = None
    steps[0].status = "pending"
    steps[0].error_code = None
    stale_replay = orchestrator._materialize_recognition_jobs(db, run=run, steps=steps)
    assert stale_replay == []
    assert steps[0].status == "stale"
    assert steps[0].error_code == "RECOGNITION_INPUT_STALE"


def test_continue_reaggregates_after_durable_recognition_and_replay_does_not_redispatch(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _actor, batch, client = processing_case
    submission = db.scalar(
        select(Submission).where(Submission.grading_batch_id == batch.id).order_by(Submission.id)
    )
    assert submission is not None
    answer_id = uuid.uuid4()
    manifest = {
        "schema": "processing-run-input-v1",
        "batch_id": str(batch.id),
        "included": [
            {
                "submission_id": str(submission.id),
                "status": submission.status,
                "answers": [
                    {
                        "answer_id": str(answer_id),
                        "question_id": str(uuid.uuid4()),
                        "blocker": "RECOGNITION_EVIDENCE_NOT_CONFIRMED",
                    }
                ],
                "blockers": ["RECOGNITION_EVIDENCE_NOT_CONFIRMED"],
            }
        ],
        "excluded": [],
        "input_version": "a" * 64,
    }
    region = SimpleNamespace(
        id=uuid.uuid4(),
        region_version=1,
        segmentation_version="submission-seg-v1",
    )
    monkeypatch.setattr(orchestrator, "_manifest", lambda *_args: manifest)
    monkeypatch.setattr(orchestrator, "_recognition_input", lambda *_args: ([], [region]))
    dispatched: list[list[uuid.UUID]] = []

    def dispatch(_db: Session, job_ids: list[uuid.UUID]) -> None:
        assert db.scalar(select(func.count()).select_from(ProcessingRunCommand)) == 1
        assert all(db.get(SubmissionRecognitionJob, job_id) is not None for job_id in job_ids)
        dispatched.append(job_ids)

    monkeypatch.setattr(orchestrator, "_dispatch_recognition_jobs", dispatch)
    first = _post_continue(client, batch, "recognition-continue")
    assert first.status_code == 201, first.text
    payload = first.json()
    assert payload["status"] == "running"
    assert payload["steps"][0]["status"] == "dispatched"
    assert payload["completed_step_count"] == 0
    assert payload["failed_step_count"] == 0
    assert payload["pending_codex_count"] == 0
    assert len(dispatched) == 1

    replay = _post_continue(client, batch, "recognition-continue")
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == payload["id"]
    assert len(dispatched) == 1


def test_broker_failure_is_durable_and_reconcile_projects_retryable_failure(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, actor, batch, client = processing_case
    run, steps = _recognition_run_and_steps(db, actor, batch)
    region = SimpleNamespace(
        id=uuid.uuid4(),
        region_version=1,
        segmentation_version="submission-seg-v1",
    )
    monkeypatch.setattr(orchestrator, "_recognition_input", lambda *_args: ([], [region]))
    job_ids = orchestrator._materialize_recognition_jobs(db, run=run, steps=[steps[0]])
    db.add(
        ProcessingRunCommand(
            owner_id=actor.id,
            grading_batch_id=batch.id,
            operation="continue",
            idempotency_key="recognition-broker-seed",
            request_hash="6" * 64,
            request_payload={},
            result_run_id=run.id,
            result_generation=run.generation,
        )
    )
    db.commit()

    from workers.celery_app import celery_app

    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )
    orchestrator._dispatch_recognition_jobs(db, job_ids)
    job = db.get(SubmissionRecognitionJob, job_ids[0])
    assert job is not None
    assert job.status == "failed"
    assert job.error_code == "WORKER_UNAVAILABLE"

    response = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{run.id}/reconcile",
        json={"idempotency_key": "recognition-reconcile", "expected_generation": 1},
    )
    assert response.status_code == 200, response.text
    step_payload = response.json()["steps"][0]
    assert step_payload["status"] == "retryable_failed"
    assert step_payload["error_code"] == "WORKER_UNAVAILABLE"
    assert _formal_counts(db) == (0, 0, 0)


def test_retry_replaces_selected_recognition_job_and_preserves_unselected_link(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, actor, batch, client = processing_case
    run, steps = _recognition_run_and_steps(db, actor, batch)
    run.input_version = "a" * 64
    jobs: list[SubmissionRecognitionJob] = []
    for index, step in enumerate(steps):
        job = SubmissionRecognitionJob(
            owner_id=actor.id,
            submission_id=step.submission_id,
            status="failed" if index == 0 else "completed",
            provider="fake",
            provider_version="fake-v1",
            idempotency_key=f"old-recognition-{uuid.uuid4()}",
            provider_kind="mixed",
            config_version="answer-evidence-v1",
            input_hash=str(index + 7) * 64,
            max_attempts=3,
            attempt=1,
            generation=1,
        )
        db.add(job)
        db.flush()
        step.recognition_job_id = job.id
        step.status = "retryable_failed" if index == 0 else "blocked_review"
        step.retryable = index == 0
        step.error_code = (
            "OCR_PROVIDER_ERROR" if index == 0 else "RECOGNITION_CONFIRMATION_REQUIRED"
        )
        jobs.append(job)
    db.commit()
    region = SimpleNamespace(
        id=uuid.uuid4(),
        region_version=1,
        segmentation_version="submission-seg-v1",
    )
    monkeypatch.setattr(orchestrator, "_recognition_input", lambda *_args: ([], [region]))
    monkeypatch.setattr(orchestrator, "_dispatch_recognition_jobs", lambda *_args: None)

    response = client.post(
        f"/api/grading-batches/{batch.id}/processing-runs/{run.id}/retry",
        json={
            "idempotency_key": "recognition-mixed-retry",
            "expected_generation": 1,
            "step_ids": [str(steps[0].id)],
        },
    )
    assert response.status_code == 201, response.text
    new_steps = {item["submission_id"]: item for item in response.json()["steps"]}
    selected = new_steps[str(steps[0].submission_id)]
    unselected = new_steps[str(steps[1].submission_id)]
    selected_row = db.get(ProcessingStep, uuid.UUID(selected["id"]))
    unselected_row = db.get(ProcessingStep, uuid.UUID(unselected["id"]))
    assert selected_row is not None and unselected_row is not None
    assert selected_row.recognition_job_id not in {None, jobs[0].id}
    assert unselected_row.recognition_job_id == jobs[1].id


@pytest.mark.parametrize(
    ("job_status", "attempt", "expected_step_status", "expected_code"),
    [
        ("queued", 0, "dispatched", None),
        ("running", 1, "running", None),
        (
            "completed",
            1,
            "blocked_review",
            "RECOGNITION_CONFIRMATION_REQUIRED",
        ),
        ("failed", 1, "retryable_failed", "OCR_PROVIDER_ERROR"),
        ("failed", 3, "terminal_failed", "OCR_PROVIDER_ERROR"),
    ],
)
def test_reconcile_projects_recognition_job_without_creating_formal_results(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    job_status: str,
    attempt: int,
    expected_step_status: str,
    expected_code: str | None,
) -> None:
    db, actor, batch, _ = processing_case
    run, steps = _recognition_run_and_steps(db, actor, batch)
    job = SubmissionRecognitionJob(
        owner_id=actor.id,
        submission_id=steps[0].submission_id,
        status=job_status,
        provider="fake",
        provider_version="test-v1",
        idempotency_key=f"recognition-{uuid.uuid4()}",
        provider_kind="mixed",
        config_version="answer-evidence-v1",
        input_hash="5" * 64,
        max_attempts=3,
        attempt=attempt,
        generation=1,
        error_code="OCR_PROVIDER_ERROR" if job_status == "failed" else None,
    )
    db.add(job)
    db.flush()
    steps[0].recognition_job_id = job.id
    db.commit()

    orchestrator._reconcile_recognition_children(db, steps=[steps[0]])

    assert steps[0].status == expected_step_status
    assert steps[0].error_code == expected_code
    assert _formal_counts(db) == (0, 0, 0)


def test_submission_processing_materialization_is_idempotent(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
) -> None:
    db, actor, batch, _ = processing_case
    run, steps = _recognition_run_and_steps(db, actor, batch)

    first = orchestrator._materialize_submission_processing_jobs(db, run=run, steps=steps)
    second = orchestrator._materialize_submission_processing_jobs(db, run=run, steps=steps)
    db.flush()

    assert len(first) == len(steps)
    assert second == []
    assert db.scalar(select(func.count()).select_from(SubmissionProcessingJob)) == len(steps)
    assert all(step.stage == "submission_processing" for step in steps)
    assert _formal_counts(db) == (0, 0, 0)


def test_ambiguous_segmentation_stays_waiting_input_without_formal_writes(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, actor, batch, _ = processing_case
    run, steps = _recognition_run_and_steps(db, actor, batch)
    job_ids = orchestrator._materialize_submission_processing_jobs(db, run=run, steps=[steps[0]])
    job = db.get(SubmissionProcessingJob, job_ids[0])
    assert job is not None
    job.status = "completed"
    monkeypatch.setattr(
        orchestrator,
        "auto_confirm_deterministic_regions",
        lambda *_args, **_kwargs: AutomaticConfirmationDecision(
            False,
            "SEGMENTATION_AMBIGUOUS",
            "Every answer must have exactly one current region",
        ),
    )

    orchestrator._reconcile_submission_processing_children(db, run=run, steps=[steps[0]])

    assert steps[0].status == "blocked_review"
    assert steps[0].stage == "segmentation_confirmation"
    assert steps[0].error_code == "SEGMENTATION_AMBIGUOUS"
    assert _formal_counts(db) == (0, 0, 0)


def test_no_answer_submission_scope_expands_to_one_codex_step_per_answer(
    processing_case: tuple[Session, User, GradingBatch, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, actor, batch, _ = processing_case
    run, steps = _recognition_run_and_steps(db, actor, batch)
    source_step = steps[0]
    source_step.status = "succeeded"
    assignment = db.get(Assignment, batch.assignment_id)
    assert assignment is not None
    paper = PaperVersion(
        assignment_id=assignment.id,
        version=1,
        created_by=actor.id,
    )
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    questions = [
        Question(
            paper_version_id=paper.id,
            question_number=str(index + 1),
            display_order=index + 1,
            question_type="short_answer",
            content_text=f"Question {index + 1}",
        )
        for index in range(2)
    ]
    db.add_all(questions)
    db.flush()
    answers = [
        StudentAnswer(
            submission_id=source_step.submission_id,
            question_id=question.id,
            question_version_reference=str(paper.id),
            status="recognition_confirmed",
            requires_review=False,
        )
        for question in questions
    ]
    db.add_all(answers)
    db.flush()
    monkeypatch.setattr(
        orchestrator,
        "build_processing_input_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(input_version="9" * 64),
    )

    created = orchestrator._materialize_codex_steps_after_recognition(db, run=run, steps=steps)
    replay = orchestrator._materialize_codex_steps_after_recognition(db, run=run, steps=steps)

    assert {step.student_answer_id for step in created} == {answer.id for answer in answers}
    assert all(step.kind == step.stage == "codex_suggestion" for step in created)
    assert replay == []
    assert run.step_count == len(steps)
    assert _formal_counts(db) == (0, 0, 0)
