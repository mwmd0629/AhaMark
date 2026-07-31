from __future__ import annotations

import copy
import os
import re
import threading
import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.ai_grading.schema import AIGradingOutput, ValidationContext
from app.core.config import get_settings
from app.models import (
    AICriterionSuggestion,
    AIScoringJob,
    Assignment,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AssignmentReviewSession,
    AssignmentRubricPublicationBinding,
    AuditLog,
    CodexWorkItem,
    GradeRelease,
    GradingBatch,
    GradingJob,
    GradingResult,
    PaperVersion,
    ProcessingRun,
    ProcessingRunCommand,
    ProcessingStep,
    Question,
    QuestionRecognitionEvidence,
    QuestionRubric,
    ReferenceAnswerVersion,
    RubricCriterion,
    RubricItem,
    RubricVersion,
    SchoolClass,
    StoredFile,
    StructuredRubricVersion,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionRecognitionJob,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
    VersionStatus,
    now_utc,
)
from app.processing import codex_local, orchestrator
from app.processing.contracts import (
    PROCESSING_MANIFEST_SCHEMA,
    canonical_hash,
    canonicalize,
)
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session


def _seed_codex_work_item(
    engine: Engine,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    owner_id, batch_id, submission_id = _seed_batch(engine)
    with Session(engine) as db, db.begin():
        submission = db.get(Submission, submission_id)
        assert submission is not None
        assignment = db.get(Assignment, submission.assignment_id)
        assert assignment is not None
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status=VersionStatus.confirmed,
            created_by=owner_id,
        )
        db.add(paper)
        db.flush()
        question = Question(
            paper_version_id=paper.id,
            question_number="1",
            display_order=1,
            question_type="short_answer",
            content_text="Synthetic",
            max_score=1,
        )
        db.add(question)
        db.flush()
        answer = StudentAnswer(
            submission_id=submission.id,
            question_id=question.id,
            question_version_reference=str(paper.id),
            recognized_text="Synthetic",
        )
        db.add(answer)
        db.flush()
        run = ProcessingRun(
            owner_id=owner_id,
            grading_batch_id=batch_id,
            status="waiting_codex",
            mode="codex_local",
            generation=1,
            input_version="a" * 64,
            request_hash="b" * 64,
            pending_codex_count=1,
        )
        db.add(run)
        db.flush()
        step = ProcessingStep(
            processing_run_id=run.id,
            submission_id=submission.id,
            student_answer_id=answer.id,
            scope_key=f"answer:{answer.id}",
            kind="codex_suggestion",
            status="pending",
            generation=1,
            input_version="c" * 64,
            request_hash="d" * 64,
        )
        db.add(step)
        db.flush()
        item = CodexWorkItem(
            processing_step_id=step.id,
            owner_id=owner_id,
            grading_batch_id=batch_id,
            submission_id=submission.id,
            student_answer_id=answer.id,
            status="queued",
            generation=1,
            input_version="c" * 64,
            request_hash="e" * 64,
            request_payload={"schema": "codex-work-request-v1"},
            provider="codex_local",
            prompt_version="codex-local-v1",
            schema_version="criterion-suggestion-v1",
            config_version="suggestion-only-v1",
            available_at=now_utc(),
        )
        db.add(item)
        db.flush()
        # Keep this target first even when a developer reuses the same isolated marker DB.
        item.created_at = now_utc() - timedelta(days=365)
        return item.id, run.id, step.id


@pytest.fixture(scope="module")
def processing_postgresql() -> Iterator[Engine]:
    database_url = os.getenv("PROCESSING_ORCHESTRATOR_PG_URL")
    if not database_url:
        pytest.skip("requires an explicitly isolated PostgreSQL database")
    marker = os.getenv("PROCESSING_ORCHESTRATOR_PG_MARKER", "")
    if not re.fullmatch(r"[a-z0-9]{12,64}", marker):
        pytest.fail(
            "PROCESSING_ORCHESTRATOR_PG_MARKER must be an explicit 12-64 character "
            "lowercase alphanumeric marker"
        )
    database_name = make_url(database_url).database or ""
    if database_name != f"ahamark_processing_{marker}":
        pytest.fail("refusing a PostgreSQL database that does not exactly match its marker")
    if database_name in {"ahamark", "ahamark_business_e2e"}:
        pytest.fail("refusing a protected application or business database")

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    original_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    engine: Engine | None = None
    try:
        command.upgrade(config, "0029_processing_auto_confirmation")
        engine = create_engine(database_url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


def _seed_batch(engine: Engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    with Session(engine) as db, db.begin():
        owner = User(
            email=f"phase3c-{uuid.uuid4()}@example.test",
            password_hash="test",
            display_name="Phase 3C",
        )
        db.add(owner)
        db.flush()
        school_class = SchoolClass(owner_id=owner.id, name=f"Phase 3C {uuid.uuid4()}")
        assignment = Assignment(owner_id=owner.id, title="Phase 3C")
        db.add_all([school_class, assignment])
        db.flush()
        batch = GradingBatch(
            owner_id=owner.id,
            assignment_id=assignment.id,
            class_id=school_class.id,
            name="Phase 3C",
        )
        db.add(batch)
        db.flush()
        submission = Submission(
            owner_id=owner.id,
            grading_batch_id=batch.id,
            assignment_id=assignment.id,
            class_id=school_class.id,
        )
        db.add(submission)
        db.flush()
        return owner.id, batch.id, submission.id


def _seed_batch_for_owner(engine: Engine, owner_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine) as db, db.begin():
        school_class = SchoolClass(owner_id=owner_id, name=f"Phase 3C {uuid.uuid4()}")
        assignment = Assignment(owner_id=owner_id, title="Phase 3C")
        db.add_all([school_class, assignment])
        db.flush()
        batch = GradingBatch(
            owner_id=owner_id,
            assignment_id=assignment.id,
            class_id=school_class.id,
            name="Phase 3C",
        )
        db.add(batch)
        db.flush()
        submission = Submission(
            owner_id=owner_id,
            grading_batch_id=batch.id,
            assignment_id=assignment.id,
            class_id=school_class.id,
        )
        db.add(submission)
        db.flush()
        return batch.id, submission.id


def _stable_manifest(batch_id: uuid.UUID, submission_id: uuid.UUID) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": PROCESSING_MANIFEST_SCHEMA,
        "batch_id": str(batch_id),
        "included": [
            {
                "submission_id": str(submission_id),
                "status": "uploaded",
                "answers": [],
                "blockers": [],
            }
        ],
        "excluded": [],
    }
    manifest["input_version"] = canonical_hash(manifest)
    return manifest


def _formal_counts(db: Session) -> tuple[int, int, int]:
    return (
        db.scalar(select(func.count()).select_from(TeacherReview)) or 0,
        db.scalar(select(func.count()).select_from(SubmissionScoreSnapshot)) or 0,
        db.scalar(select(func.count()).select_from(GradeRelease)) or 0,
    )


def _valid_codex_response() -> dict[str, Any]:
    return {
        "schema_version": "criterion-suggestion-v1",
        "criteria": [
            {
                "criterion_stable_key": "criterion-1",
                "status": "suggested_pass",
                "suggested_points": "1",
                "max_points": "1",
                "confidence": "0.8",
                "decision": "pass",
                "evidence_refs": ["evidence-1"],
                "validation_refs": [],
                "error_codes": [],
                "requires_review": True,
                "reasoning_summary": "Synthetic",
            }
        ],
        "total_suggested_points": "1",
    }


def test_postgresql_command_concurrency_and_generation_fencing(
    processing_postgresql: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = processing_postgresql
    owner_id, batch_id, submission_id = _seed_batch(engine)
    manifest = _stable_manifest(batch_id, submission_id)
    monkeypatch.setattr(
        orchestrator,
        "_manifest",
        lambda _db, _owner_id, _batch: copy.deepcopy(manifest),
    )
    with Session(engine) as db:
        formal_before = _formal_counts(db)

    barrier = threading.Barrier(2)
    results: list[tuple[str, uuid.UUID | str]] = []
    result_lock = threading.Lock()

    def continue_once(key: str) -> None:
        with Session(engine) as db:
            barrier.wait()
            try:
                run = orchestrator.continue_processing(
                    db, owner_id=owner_id, batch_id=batch_id, idempotency_key=key
                )
            except orchestrator.OrchestratorProblem as exc:
                value: tuple[str, uuid.UUID | str] = ("error", exc.code)
            else:
                value = ("ok", run.id)
            with result_lock:
                results.append(value)

    same_key = f"same-{uuid.uuid4()}"
    threads = [threading.Thread(target=continue_once, args=(same_key,)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert results[0][0] == results[1][0] == "ok"
    assert results[0][1] == results[1][1]

    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcessingRunCommand)
                .where(
                    ProcessingRunCommand.owner_id == owner_id,
                    ProcessingRunCommand.idempotency_key == same_key,
                )
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcessingRun)
                .where(ProcessingRun.grading_batch_id == batch_id)
            )
            == 1
        )

    barrier = threading.Barrier(2)
    results.clear()
    alias_keys = [f"alias-{uuid.uuid4()}", f"alias-{uuid.uuid4()}"]
    threads = [threading.Thread(target=continue_once, args=(key,)) for key in alias_keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert results[0][0] == results[1][0] == "ok"
    assert results[0][1] == results[1][1]

    with Session(engine) as db, db.begin():
        run = db.scalar(select(ProcessingRun).where(ProcessingRun.grading_batch_id == batch_id))
        assert run is not None
        step = db.scalar(select(ProcessingStep).where(ProcessingStep.processing_run_id == run.id))
        assert step is not None
        run.status = "partially_failed"
        step.status = "retryable_failed"
        step.retryable = True
        step.error_code = "SYNTHETIC_RETRY"
        source_run_id = run.id
        source_step_id = step.id
        expected_generation = run.generation

    barrier = threading.Barrier(2)
    results.clear()

    def retry_once(key: str) -> None:
        with Session(engine) as db:
            barrier.wait()
            try:
                run = orchestrator.retry_processing(
                    db,
                    owner_id=owner_id,
                    batch_id=batch_id,
                    source_run_id=source_run_id,
                    idempotency_key=key,
                    expected_generation=expected_generation,
                    step_ids=[source_step_id],
                )
            except orchestrator.OrchestratorProblem as exc:
                value: tuple[str, uuid.UUID | str] = ("error", exc.code)
            else:
                value = ("ok", run.id)
            with result_lock:
                results.append(value)

    retry_keys = [f"retry-{uuid.uuid4()}", f"retry-{uuid.uuid4()}"]
    threads = [threading.Thread(target=retry_once, args=(key,)) for key in retry_keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert sorted(value[0] for value in results) == ["error", "ok"]
    assert {value[1] for value in results if value[0] == "error"} == {
        "PROCESSING_GENERATION_CONFLICT"
    }

    with Session(engine) as db:
        runs = list(
            db.scalars(
                select(ProcessingRun)
                .where(ProcessingRun.grading_batch_id == batch_id)
                .order_by(ProcessingRun.generation)
            )
        )
        assert [run.generation for run in runs] == [1, 2]
        assert len({run.generation for run in runs}) == 2
        historical = orchestrator.continue_processing(
            db,
            owner_id=owner_id,
            batch_id=batch_id,
            idempotency_key=same_key,
        )
        assert historical.id == source_run_id
        assert historical.generation == 1
        assert _formal_counts(db) == formal_before == (0, 0, 0)


def test_postgresql_command_creation_rolls_back_as_one_transaction(
    processing_postgresql: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = processing_postgresql
    owner_id, batch_id, submission_id = _seed_batch(engine)
    manifest = _stable_manifest(batch_id, submission_id)
    monkeypatch.setattr(
        orchestrator,
        "_manifest",
        lambda _db, _owner_id, _batch: copy.deepcopy(manifest),
    )

    def interrupt(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("synthetic transaction interruption")

    monkeypatch.setattr(orchestrator, "_add_command", interrupt)
    with pytest.raises(RuntimeError, match="synthetic transaction interruption"):
        with Session(engine) as db:
            orchestrator.continue_processing(
                db,
                owner_id=owner_id,
                batch_id=batch_id,
                idempotency_key=f"rollback-{uuid.uuid4()}",
            )

    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcessingRun)
                .where(ProcessingRun.grading_batch_id == batch_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcessingRunCommand)
                .where(ProcessingRunCommand.grading_batch_id == batch_id)
            )
            == 0
        )
        assert _formal_counts(db) == (0, 0, 0)


def test_postgresql_global_owner_key_conflict_across_batches_is_stable(
    processing_postgresql: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = processing_postgresql
    owner_id, first_batch_id, first_submission_id = _seed_batch(engine)
    second_batch_id, second_submission_id = _seed_batch_for_owner(engine, owner_id)
    manifests = {
        first_batch_id: _stable_manifest(first_batch_id, first_submission_id),
        second_batch_id: _stable_manifest(second_batch_id, second_submission_id),
    }
    monkeypatch.setattr(
        orchestrator,
        "_manifest",
        lambda _db, _owner_id, batch: copy.deepcopy(manifests[batch.id]),
    )

    original_replay = orchestrator._command_replay
    replay_barrier = threading.Barrier(2)
    local = threading.local()

    def synchronized_replay(*args: Any, **kwargs: Any) -> ProcessingRun | None:
        result = original_replay(*args, **kwargs)
        count = getattr(local, "replay_count", 0) + 1
        local.replay_count = count
        if result is None and count <= 2:
            replay_barrier.wait(timeout=30)
        return result

    monkeypatch.setattr(orchestrator, "_command_replay", synchronized_replay)
    key = f"cross-batch-{uuid.uuid4()}"
    results: list[tuple[str, str]] = []
    result_lock = threading.Lock()

    def invoke(batch_id: uuid.UUID) -> None:
        with Session(engine) as db:
            try:
                run = orchestrator.continue_processing(
                    db,
                    owner_id=owner_id,
                    batch_id=batch_id,
                    idempotency_key=key,
                )
            except orchestrator.OrchestratorProblem as exc:
                value = ("error", exc.code)
            except Exception as exc:  # pragma: no cover - diagnostic if fencing regresses
                value = ("unexpected", type(exc).__name__)
            else:
                value = ("ok", str(run.id))
            with result_lock:
                results.append(value)

    threads = [
        threading.Thread(target=invoke, args=(first_batch_id,)),
        threading.Thread(target=invoke, args=(second_batch_id,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert sorted(value[0] for value in results) == ["error", "ok"]
    assert {value[1] for value in results if value[0] == "error"} == {"IDEMPOTENCY_KEY_CONFLICT"}
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcessingRunCommand)
                .where(
                    ProcessingRunCommand.owner_id == owner_id,
                    ProcessingRunCommand.idempotency_key == key,
                )
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcessingRun)
                .where(ProcessingRun.grading_batch_id.in_((first_batch_id, second_batch_id)))
            )
            == 1
        )


def test_postgresql_codex_claim_scanner_never_double_leases(
    processing_postgresql: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = processing_postgresql
    item_id, run_id, step_id = _seed_codex_work_item(engine)
    monkeypatch.setattr(
        codex_local,
        "_current_item_state",
        lambda db, _item: (
            db.get(ProcessingRun, run_id),
            db.get(ProcessingStep, step_id),
            None,
        ),
    )
    barrier = threading.Barrier(2)
    claims: list[list[dict[str, Any]]] = []
    claim_lock = threading.Lock()

    def claim_once(worker: str) -> None:
        with Session(engine) as db:
            barrier.wait()
            result = codex_local.claim_work_items(
                db,
                worker_id=worker,
                limit=100,
                lease_seconds=60,
            )
            with claim_lock:
                claims.append(result)

    threads = [
        threading.Thread(target=claim_once, args=("worker-a",)),
        threading.Thread(target=claim_once, args=("worker-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()

    target_claims = [
        claimed
        for result in claims
        for claimed in result
        if claimed["work_item_id"] == str(item_id)
    ]
    assert len(target_claims) == 1
    claimed = target_claims[0]
    assert claimed["work_item_id"] == str(item_id)
    with Session(engine) as db:
        item = db.get(CodexWorkItem, item_id)
        assert item is not None
        assert item.status == "leased"
        assert item.attempt == 1
        assert item.lease_token_hash is not None
        assert claimed["lease_token"] not in str(item.__dict__)
        assert _formal_counts(db) == (0, 0, 0)


def test_postgresql_codex_submit_same_token_is_one_write(
    processing_postgresql: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = processing_postgresql
    item_id, run_id, step_id = _seed_codex_work_item(engine)
    context = ValidationContext(
        criterion_maxima={"criterion-1": Decimal("1")},
        evidence_ids={"evidence-1"},
        criterion_keys={"criterion-1"},
        question_max_points=Decimal("1"),
    )
    monkeypatch.setattr(
        codex_local,
        "_current_item_state",
        lambda db, _item: (
            db.get(ProcessingRun, run_id),
            db.get(ProcessingStep, step_id),
            context,
        ),
    )
    with Session(engine) as db:
        claimed = codex_local.claim_work_items(
            db,
            worker_id="worker-submit",
            limit=1,
            lease_seconds=60,
        )[0]
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[str] = []
    result_lock = threading.Lock()

    def submit_once() -> None:
        with Session(engine) as db:
            barrier.wait()
            try:
                item = codex_local.submit_work_item(
                    db,
                    item_id=item_id,
                    worker_id="worker-submit",
                    lease_token=claimed["lease_token"],
                    request_hash=claimed["request_hash"],
                    response=_valid_codex_response(),
                )
            except codex_local.CodexLocalProblem as exc:
                with result_lock:
                    errors.append(exc.code)
            else:
                with result_lock:
                    results.append(item.status)

    threads = [threading.Thread(target=submit_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert errors == []
    assert results == ["submitted", "submitted"]
    with Session(engine) as db:
        item = db.get(CodexWorkItem, item_id)
        assert item is not None
        assert item.status == "submitted"
        assert item.response_hash is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.resource_id == str(item_id),
                    AuditLog.action == "codex_local.submitted",
                )
            )
            == 1
        )
        assert _formal_counts(db) == (0, 0, 0)


@pytest.mark.parametrize("drift", ["generation", "finalized"])
def test_postgresql_codex_claim_marks_generation_or_finalized_drift_stale(
    processing_postgresql: Engine,
    drift: str,
) -> None:
    engine = processing_postgresql
    item_id, run_id, _step_id = _seed_codex_work_item(engine)
    with Session(engine) as db, db.begin():
        item = db.get(CodexWorkItem, item_id)
        run = db.get(ProcessingRun, run_id)
        assert item is not None and run is not None
        if drift == "generation":
            db.add(
                ProcessingRun(
                    owner_id=run.owner_id,
                    grading_batch_id=run.grading_batch_id,
                    status="queued",
                    mode="codex_local",
                    generation=2,
                    input_version="f" * 64,
                    request_hash="0" * 64,
                )
            )
        else:
            submission = db.get(Submission, item.submission_id)
            assert submission is not None
            submission.finalized_at = now_utc()
            submission.status = "finalized"
    with Session(engine) as db:
        assert (
            codex_local.claim_work_items(
                db,
                worker_id="worker-drift",
                limit=1,
                lease_seconds=60,
            )
            == []
        )
    with Session(engine) as db:
        item = db.get(CodexWorkItem, item_id)
        assert item is not None
        assert item.status == "stale"
        assert item.error_code == "CODEX_WORK_INPUT_STALE"
        assert item.lease_token_hash is None
        assert _formal_counts(db) == (0, 0, 0)


def _seed_postgresql_submitted_apply(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    item_id, run_id, step_id = _seed_codex_work_item(engine)
    with Session(engine) as db, db.begin():
        item = db.get(CodexWorkItem, item_id)
        run = db.get(ProcessingRun, run_id)
        step = db.get(ProcessingStep, step_id)
        assert item is not None and run is not None and step is not None
        answer = db.get(StudentAnswer, item.student_answer_id)
        submission = db.get(Submission, item.submission_id)
        assert answer is not None and submission is not None
        assignment = db.get(Assignment, submission.assignment_id)
        question = db.get(Question, answer.question_id)
        assert assignment is not None and question is not None
        paper = db.get(PaperVersion, question.paper_version_id)
        assert paper is not None
        reference = ReferenceAnswerVersion(
            question_id=question.id,
            source_type="teacher_official",
            source_region={},
            raw_content="1",
            normalized_content="1",
            structured_content={},
            content_hash="1" * 64,
            version=1,
            provenance={},
            created_by=item.owner_id,
            status="confirmed",
        )
        legacy = RubricVersion(
            assignment_id=assignment.id,
            version=1,
            status=VersionStatus.confirmed,
            created_by=item.owner_id,
        )
        db.add_all([reference, legacy])
        db.flush()
        rubric = StructuredRubricVersion(
            question_id=question.id,
            question_version=answer.question_version_reference,
            reference_answer_version_id=reference.id,
            rubric_version=1,
            title="Apply rubric",
            total_points=Decimal("1"),
            status="confirmed",
            content_hash="2" * 64,
            created_by=item.owner_id,
        )
        question_rubric = QuestionRubric(
            rubric_version_id=legacy.id,
            question_id=question.id,
            standard_answer="1",
        )
        generation_job = AssignmentGenerationJob(
            owner_id=item.owner_id,
            assignment_id=assignment.id,
            generation=1,
            status="completed",
            progress=100,
            idempotency_key=f"pg-apply-generation-{item.id}",
            request_fingerprint="3" * 64,
            source_snapshot_hash="4" * 64,
            provider_mode="unavailable",
            provider_config_version="test",
            prompt_version="test",
            schema_version="test",
        )
        db.add_all([rubric, question_rubric, generation_job])
        db.flush()
        criterion = RubricCriterion(
            rubric_version_id=rubric.id,
            stable_key="criterion-1",
            title="Correct",
            max_points=Decimal("1"),
            display_order=1,
            criterion_type="correctness",
            required=True,
            dependencies=[],
            expected_evidence={},
            validation_mode="ai",
            validation_rule={},
            manual_review_policy={},
            partial_credit_policy={},
            metadata_={},
        )
        rubric_item = RubricItem(
            question_rubric_id=question_rubric.id,
            display_order=1,
            title="Correct",
            points=Decimal("1"),
        )
        revision = AssignmentDraftRevision(
            owner_id=item.owner_id,
            assignment_id=assignment.id,
            generation_job_id=generation_job.id,
            revision=1,
            source_snapshot_hash="4" * 64,
            status="completed",
            draft_payload={},
            risk_summary={},
            created_by_type="worker",
            created_by=item.owner_id,
        )
        db.add_all([criterion, rubric_item, revision])
        db.flush()
        review = AssignmentReviewSession(
            owner_id=item.owner_id,
            assignment_id=assignment.id,
            generation_job_id=generation_job.id,
            draft_revision_id=revision.id,
            generation=1,
            source_snapshot_hash="4" * 64,
            review_version=1,
            status="completed",
            risk_ledger_hash="5" * 64,
            expected_assignment_updated_at=assignment.updated_at,
            paper_version_id=paper.id,
            structured_binding_hash="6" * 64,
            legacy_rubric_version_id=legacy.id,
            created_by=item.owner_id,
        )
        db.add(review)
        db.flush()
        binding = AssignmentRubricPublicationBinding(
            owner_id=item.owner_id,
            assignment_id=assignment.id,
            review_session_id=review.id,
            paper_version_id=paper.id,
            legacy_rubric_version_id=legacy.id,
            binding_version=1,
            status="confirmed",
            source_binding_hash="7" * 64,
            target_legacy_hash="8" * 64,
            mapping=[
                {
                    "question_id": str(question.id),
                    "structured_rubric_version_id": str(rubric.id),
                    "legacy_question_rubric_id": str(question_rubric.id),
                    "criteria": [
                        {
                            "criterion_id": str(criterion.id),
                            "rubric_item_id": str(rubric_item.id),
                        }
                    ],
                }
            ],
            created_by=item.owner_id,
            confirmed_by=item.owner_id,
            confirmed_at=now_utc(),
        )
        stored = StoredFile(
            owner_id=item.owner_id,
            storage_key=f"pg-apply/{item.id}",
            original_name="synthetic.png",
            content_type="image/png",
            size=1,
            checksum="9" * 64,
        )
        recognition_job = SubmissionRecognitionJob(
            owner_id=item.owner_id,
            submission_id=submission.id,
            status="completed",
            provider="synthetic",
            provider_version="1",
            idempotency_key=f"pg-apply-recognition-{item.id}",
            provider_kind="printed_text",
            config_version="test",
            input_hash="a" * 64,
            output_hash="b" * 64,
            generation=1,
        )
        db.add_all([binding, stored, recognition_job])
        db.flush()
        page = SubmissionPage(
            submission_id=submission.id,
            stored_file_id=stored.id,
            page_number=1,
            page_version=1,
        )
        db.add(page)
        db.flush()
        region = StudentAnswerRegion(
            student_answer_id=answer.id,
            submission_page_id=page.id,
            x=Decimal("0.1"),
            y=Decimal("0.1"),
            width=Decimal("0.5"),
            height=Decimal("0.5"),
            status="confirmed",
            confirmed_by=item.owner_id,
            confirmed_at=now_utc(),
        )
        db.add(region)
        db.flush()
        evidence = QuestionRecognitionEvidence(
            owner_id=item.owner_id,
            submission_id=submission.id,
            student_answer_id=answer.id,
            recognition_job_id=recognition_job.id,
            status="confirmed",
            block_sources=[{"region_id": str(region.id)}],
            provider_versions={"synthetic": "1"},
            input_hash="c" * 64,
            output_hash="d" * 64,
            recognition_version=1,
            confirmed_revision=1,
            requires_review=False,
        )
        db.add(evidence)
        db.flush()
        evidence_id = evidence.id
        assignment.active_rubric_version_id = legacy.id
        run.submission_count = 1
        run.step_count = 1
        request = canonicalize(
            {
                "schema": "codex-work-request-v1",
                "processing_input": {
                    "payload": {
                        "formal": {
                            "structured_rubric": {"id": str(rubric.id)},
                            "reference_answer": {"id": str(reference.id)},
                        },
                        "legacy_projection": {"binding_id": str(binding.id)},
                    }
                },
            }
        )
        response = _valid_codex_response()
        response["criteria"][0]["evidence_refs"] = [str(region.id)]
        canonical_response = canonicalize(
            AIGradingOutput.model_validate(response).model_dump(mode="json")
        )
        item.status = "submitted"
        item.input_version = step.input_version
        item.request_payload = request
        item.request_hash = canonical_hash(request)
        item.response_payload = canonical_response
        item.response_hash = canonical_hash(canonical_response)
        item.submitted_lease_token_hash = "e" * 64
        item.submitted_at = now_utc()
        item.available_at = None
        input_version = item.input_version
        context = ValidationContext(
            criterion_maxima={"criterion-1": Decimal("1")},
            evidence_ids={str(region.id)},
            criterion_keys={"criterion-1"},
            question_max_points=Decimal("1"),
        )
    monkeypatch.setattr(
        codex_local,
        "_request_components",
        lambda *_args, **_kwargs: (request, input_version, context),
    )
    monkeypatch.setattr(
        codex_local,
        "require_current_recognition_evidence",
        lambda db, **_kwargs: db.get(QuestionRecognitionEvidence, evidence_id),
    )
    return item_id, run_id, step_id


def test_postgresql_codex_apply_same_item_is_one_strict_and_legacy_child(
    processing_postgresql: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = processing_postgresql
    item_id, _run_id, _step_id = _seed_postgresql_submitted_apply(engine, monkeypatch)
    with Session(engine) as db:
        item = db.get(CodexWorkItem, item_id)
        assert item is not None and item.response_hash is not None
        request_hash, response_hash = item.request_hash, item.response_hash
    barrier = threading.Barrier(2)
    statuses: list[str] = []
    errors: list[str] = []
    result_lock = threading.Lock()

    def apply_once(worker: str) -> None:
        with Session(engine) as db:
            barrier.wait()
            try:
                applied = codex_local.apply_work_item(
                    db,
                    item_id=item_id,
                    worker_id=worker,
                    request_hash=request_hash,
                    response_hash=response_hash,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                with result_lock:
                    errors.append(f"{type(exc).__name__}:{exc}")
            else:
                with result_lock:
                    statuses.append(applied.status)

    threads = [
        threading.Thread(target=apply_once, args=("apply-a",)),
        threading.Thread(target=apply_once, args=("apply-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert errors == []
    assert statuses == ["applied", "applied"]
    with Session(engine) as db:
        item = db.get(CodexWorkItem, item_id)
        assert item is not None and item.status == "applied"
        assert (
            db.scalar(
                select(func.count()).select_from(GradingJob).where(
                    GradingJob.id == item.grading_job_id
                )
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count()).select_from(GradingResult).where(
                    GradingResult.id == item.grading_result_id
                )
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count()).select_from(AIScoringJob).where(
                    AIScoringJob.student_answer_id == item.student_answer_id,
                    AIScoringJob.provider == "codex_local",
                )
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AICriterionSuggestion)
                .join(AIScoringJob, AIScoringJob.id == AICriterionSuggestion.ai_scoring_job_id)
                .where(AIScoringJob.student_answer_id == item.student_answer_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.resource_id == str(item_id),
                    AuditLog.action == "codex_local.applied",
                )
            )
            == 1
        )
        assert _formal_counts(db) == (0, 0, 0)


def test_postgresql_codex_apply_interruption_rolls_back_every_child(
    processing_postgresql: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = processing_postgresql
    item_id, _run_id, _step_id = _seed_postgresql_submitted_apply(engine, monkeypatch)
    with Session(engine) as db:
        item = db.get(CodexWorkItem, item_id)
        assert item is not None and item.response_hash is not None
        request_hash, response_hash = item.request_hash, item.response_hash

    def interrupt_legacy_result(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO grading_results"):
            raise RuntimeError("synthetic apply interruption")

    event.listen(engine, "before_cursor_execute", interrupt_legacy_result)
    try:
        with pytest.raises(RuntimeError, match="synthetic apply interruption"):
            with Session(engine) as db:
                codex_local.apply_work_item(
                    db,
                    item_id=item_id,
                    worker_id="apply-interrupt",
                    request_hash=request_hash,
                    response_hash=response_hash,
                )
    finally:
        event.remove(engine, "before_cursor_execute", interrupt_legacy_result)
    with Session(engine) as db:
        item = db.get(CodexWorkItem, item_id)
        assert item is not None and item.status == "submitted"
        assert item.grading_job_id is None and item.grading_result_id is None
        assert (
            db.scalar(
                select(func.count()).select_from(GradingJob).where(
                    GradingJob.idempotency_key.like("pcx:%"),
                    GradingJob.submission_id == item.submission_id,
                )
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count()).select_from(AIScoringJob).where(
                    AIScoringJob.student_answer_id == item.student_answer_id,
                    AIScoringJob.provider == "codex_local",
                )
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.resource_id == str(item_id),
                    AuditLog.action == "codex_local.applied",
                )
            )
            == 0
        )
        assert _formal_counts(db) == (0, 0, 0)


def test_postgresql_codex_apply_and_reconcile_have_no_lock_cycle(
    processing_postgresql: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = processing_postgresql
    item_id, run_id, _step_id = _seed_postgresql_submitted_apply(engine, monkeypatch)
    with Session(engine) as db:
        item = db.get(CodexWorkItem, item_id)
        run = db.get(ProcessingRun, run_id)
        assert item is not None and item.response_hash is not None and run is not None
        request_hash, response_hash = item.request_hash, item.response_hash
        owner_id, batch_id, generation = run.owner_id, run.grading_batch_id, run.generation
    barrier = threading.Barrier(2)
    errors: list[str] = []
    result_lock = threading.Lock()

    def apply_once() -> None:
        try:
            with Session(engine) as db:
                barrier.wait()
                db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                codex_local.apply_work_item(
                    db,
                    item_id=item_id,
                    worker_id="apply-vs-reconcile",
                    request_hash=request_hash,
                    response_hash=response_hash,
                )
        except Exception as exc:  # pragma: no cover - asserted below
            with result_lock:
                errors.append(f"apply:{type(exc).__name__}:{exc}")

    def reconcile_once() -> None:
        try:
            with Session(engine) as db:
                barrier.wait()
                db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                orchestrator.reconcile_processing(
                    db,
                    owner_id=owner_id,
                    batch_id=batch_id,
                    run_id=run_id,
                    idempotency_key=f"concurrent-reconcile-{item_id}",
                    expected_generation=generation,
                )
        except Exception as exc:  # pragma: no cover - asserted below
            with result_lock:
                errors.append(f"reconcile:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=apply_once), threading.Thread(target=reconcile_once)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert errors == []
    with Session(engine) as db:
        final = orchestrator.reconcile_processing(
            db,
            owner_id=owner_id,
            batch_id=batch_id,
            run_id=run_id,
            idempotency_key=f"final-reconcile-{item_id}",
            expected_generation=generation,
        )
        assert final.status == "awaiting_teacher_review"
        assert _formal_counts(db) == (0, 0, 0)
