from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import timedelta
from decimal import Decimal

import pytest
from app.api.codex_local import _internal_auth
from app.api.domain import ApiProblem
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    Assignment,
    AssignmentRubricPublicationBinding,
    CodexWorkItem,
    GradeRelease,
    GradingBatch,
    ProcessingRun,
    ProcessingStep,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    SchoolClass,
    StructuredRubricVersion,
    Student,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
    now_utc,
)
from app.processing import codex_local
from app.processing.codex_local import (
    CodexLocalProblem,
    claim_work_items,
    materialize_work_items,
    submit_work_item,
)
from app.processing.contracts import ProcessingInputSnapshot
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

TOKEN = "phase3d-internal-token-that-is-longer-than-32"


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_step(db: Session) -> tuple[ProcessingRun, ProcessingStep]:
    owner = User(
        email=f"codex-local-{uuid.uuid4()}@example.invalid",
        password_hash="!",
        display_name="Codex local test",
    )
    db.add(owner)
    db.flush()
    school_class = SchoolClass(owner_id=owner.id, name=f"class-{uuid.uuid4()}")
    assignment = Assignment(owner_id=owner.id, title="Synthetic")
    student = Student(
        owner_id=owner.id,
        student_number=str(uuid.uuid4()),
        name="Synthetic student",
    )
    db.add_all([school_class, assignment, student])
    db.flush()
    batch = GradingBatch(
        owner_id=owner.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
    )
    db.add(batch)
    db.flush()
    submission = Submission(
        owner_id=owner.id,
        grading_batch_id=batch.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        student_id=student.id,
    )
    db.add(submission)
    db.flush()
    # Question scope is not consulted because the work-request builder is isolated below.
    answer = StudentAnswer(
        submission_id=submission.id,
        question_id=uuid.uuid4(),
        question_version_reference="synthetic",
    )
    db.add(answer)
    db.flush()
    run = ProcessingRun(
        owner_id=owner.id,
        grading_batch_id=batch.id,
        status="queued",
        mode="codex_local",
        generation=1,
        input_version="a" * 64,
        request_hash="b" * 64,
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
    db.commit()
    return run, step


def _valid_response() -> dict[str, object]:
    return {
        "schema_version": "criterion-suggestion-v1",
        "criteria": [
            {
                "criterion_stable_key": "criterion-1",
                "status": "suggested_pass",
                "suggested_points": "2",
                "max_points": "2",
                "confidence": "0.8",
                "decision": "pass",
                "evidence_refs": ["evidence-1"],
                "validation_refs": [],
                "error_codes": [],
                "requires_review": True,
                "reasoning_summary": "Synthetic suggestion",
            }
        ],
        "total_suggested_points": "2",
    }


def test_settings_reject_weak_token_and_production_enablement() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        Settings(codex_local_enabled=True, codex_local_internal_token="weak")
    with pytest.raises(ValueError, match="CODEX_LOCAL_ENABLED must be false"):
        Settings(
            app_env="production",
            codex_local_enabled=True,
            codex_local_internal_token=TOKEN,
            demo_actor_enabled=False,
            session_hmac_secret="s" * 40,
            minio_access_key="production-access",
            minio_secret_key="production-secret-value",
            database_url="postgresql://user:strong@database/ahamark",
            csrf_trusted_origins=["https://example.invalid"],
            cors_origins=["https://example.invalid"],
            trusted_hosts=["example.invalid"],
            auth_cookie_secure=True,
        )


def test_internal_auth_is_bearer_only_and_disabled_is_404() -> None:
    with pytest.raises(ApiProblem) as disabled:
        _internal_auth(Settings(), authorization=f"Bearer {TOKEN}")
    assert (disabled.value.status, disabled.value.code) == (404, "CODEX_LOCAL_DISABLED")
    settings = Settings(codex_local_enabled=True, codex_local_internal_token=TOKEN)
    for supplied in (None, f"Cookie {TOKEN}", "Bearer wrong"):
        with pytest.raises(ApiProblem) as denied:
            _internal_auth(settings, authorization=supplied)
        assert (denied.value.status, denied.value.code) == (
            401,
            "CODEX_LOCAL_AUTH_REQUIRED",
        )
    assert _internal_auth(settings, authorization=f"Bearer {TOKEN}") is settings


def test_internal_route_does_not_accept_actor_cookie(db: Session) -> None:
    settings = Settings(codex_local_enabled=True, codex_local_internal_token=TOKEN)

    def override_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            client.cookies.set("ahamark_session", TOKEN)
            denied = client.post(
                "/api/internal/codex-local/work-items/claim",
                json={"worker_id": "worker", "limit": 1},
            )
            assert (denied.status_code, denied.json()["code"]) == (
                401,
                "CODEX_LOCAL_AUTH_REQUIRED",
            )
            accepted = client.post(
                "/api/internal/codex-local/work-items/claim",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"worker_id": "worker", "limit": 1},
            )
            assert accepted.status_code == 200
            assert accepted.json() == {"items": [], "count": 0}
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_db, None)


def test_materialize_is_idempotent_and_contract_fenced(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, step = _seed_step(db)
    payload = {"schema": "codex-work-request-v1", "suggestion_only": True}
    monkeypatch.setattr(
        codex_local,
        "build_work_request",
        lambda *_args, **_kwargs: (payload, "c" * 64, "e" * 64),
    )
    first = materialize_work_items(db, run=run, steps=[step])
    second = materialize_work_items(db, run=run, steps=[step])
    assert first[0].id == second[0].id
    assert db.scalar(select(func.count()).select_from(CodexWorkItem)) == 1
    first[0].request_hash = "f" * 64
    db.flush()
    with pytest.raises(CodexLocalProblem) as conflict:
        materialize_work_items(db, run=run, steps=[step])
    assert conflict.value.code == "CODEX_WORK_ITEM_CONTRACT_CONFLICT"


def test_partial_materialize_replay_recounts_the_whole_run(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, first_step = _seed_step(db)
    assert first_step.student_answer_id is not None
    second_step = ProcessingStep(
        processing_run_id=run.id,
        submission_id=first_step.submission_id,
        student_answer_id=first_step.student_answer_id,
        scope_key=f"{first_step.scope_key}:second",
        kind="codex_suggestion",
        status="pending",
        generation=1,
        input_version=first_step.input_version,
        request_hash="9" * 64,
    )
    db.add(second_step)
    db.flush()
    payload = {"schema": "codex-work-request-v1", "suggestion_only": True}
    monkeypatch.setattr(
        codex_local,
        "build_work_request",
        lambda *_args, **_kwargs: (payload, "c" * 64, "e" * 64),
    )
    materialize_work_items(db, run=run, steps=[first_step, second_step])
    assert run.pending_codex_count == 2
    assert db.scalar(select(func.count()).select_from(CodexWorkItem)) == 2

    materialize_work_items(db, run=run, steps=[first_step])
    assert run.pending_codex_count == 2
    assert db.scalar(select(func.count()).select_from(CodexWorkItem)) == 2

    second_item = db.scalar(
        select(CodexWorkItem).where(CodexWorkItem.processing_step_id == second_step.id)
    )
    assert second_item is not None
    second_item.status = "stale"
    second_item.stale_at = now_utc()
    db.flush()
    materialize_work_items(db, run=run, steps=[first_step])
    assert run.pending_codex_count == 1


def test_claim_and_submit_are_lease_fenced_and_suggestion_only(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, step = _seed_step(db)
    payload = {"schema": "codex-work-request-v1", "suggestion_only": True}
    monkeypatch.setattr(
        codex_local,
        "build_work_request",
        lambda *_args, **_kwargs: (payload, "c" * 64, "e" * 64),
    )
    item = materialize_work_items(db, run=run, steps=[step])[0]
    db.commit()
    from app.ai_grading.schema import ValidationContext

    context = ValidationContext(
        criterion_maxima={"criterion-1": Decimal("2")},
        evidence_ids={"evidence-1"},
        criterion_keys={"criterion-1"},
        question_max_points=Decimal("2"),
    )
    monkeypatch.setattr(
        codex_local,
        "_current_item_state",
        lambda *_args, **_kwargs: (run, step, context),
    )
    claimed = claim_work_items(db, worker_id="worker-1", limit=1, lease_seconds=60)
    assert len(claimed) == 1
    lease_token = claimed[0]["lease_token"]
    assert lease_token not in str(db.get(CodexWorkItem, item.id).__dict__)
    with pytest.raises(CodexLocalProblem) as fenced:
        submit_work_item(
            db,
            item_id=item.id,
            worker_id="worker-2",
            lease_token=lease_token,
            request_hash=item.request_hash,
            response=_valid_response(),
        )
    assert fenced.value.code == "CODEX_LEASE_FENCED"
    submitted = submit_work_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        lease_token=lease_token,
        request_hash=item.request_hash,
        response=_valid_response(),
    )
    assert submitted.status == "submitted"
    replay = submit_work_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        lease_token=lease_token,
        request_hash=item.request_hash,
        response=_valid_response(),
    )
    assert replay.id == submitted.id
    changed = _valid_response()
    changed["teacher_summary"] = "different"
    with pytest.raises(CodexLocalProblem) as conflict:
        submit_work_item(
            db,
            item_id=item.id,
            worker_id="worker-1",
            lease_token=lease_token,
            request_hash=item.request_hash,
            response=changed,
        )
    assert conflict.value.code == "CODEX_RESPONSE_CONFLICT"
    assert db.scalar(select(func.count()).select_from(TeacherReview)) == 0
    assert db.scalar(select(func.count()).select_from(SubmissionScoreSnapshot)) == 0
    assert db.scalar(select(func.count()).select_from(GradeRelease)) == 0


def test_invalid_response_keeps_lease_and_expiry_requeues_then_exhausts(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, step = _seed_step(db)
    payload = {"schema": "codex-work-request-v1", "suggestion_only": True}
    monkeypatch.setattr(
        codex_local,
        "build_work_request",
        lambda *_args, **_kwargs: (payload, "c" * 64, "e" * 64),
    )
    item = materialize_work_items(db, run=run, steps=[step])[0]
    item.max_attempts = 2
    db.commit()
    from app.ai_grading.schema import ValidationContext

    context = ValidationContext(
        criterion_maxima={"criterion-1": Decimal("2")},
        evidence_ids={"evidence-1"},
        criterion_keys={"criterion-1"},
        question_max_points=Decimal("2"),
    )
    monkeypatch.setattr(
        codex_local,
        "_current_item_state",
        lambda *_args, **_kwargs: (run, step, context),
    )
    first = claim_work_items(db, worker_id="worker-1", limit=1, lease_seconds=60)[0]
    invalid = _valid_response()
    invalid["criteria"][0]["criterion_stable_key"] = "unknown"  # type: ignore[index]
    with pytest.raises(CodexLocalProblem) as rejected:
        submit_work_item(
            db,
            item_id=item.id,
            worker_id="worker-1",
            lease_token=first["lease_token"],
            request_hash=item.request_hash,
            response=invalid,
        )
    assert rejected.value.code == "CODEX_RESPONSE_INVALID"
    db.refresh(item)
    assert item.status == "leased"
    first_hash = item.lease_token_hash
    item.lease_expires_at = now_utc() - timedelta(seconds=1)
    db.commit()
    second = claim_work_items(db, worker_id="worker-2", limit=1, lease_seconds=60)[0]
    assert second["lease_token"] != first["lease_token"]
    assert item.attempt == 2
    assert item.lease_token_hash != first_hash
    item.lease_expires_at = now_utc() - timedelta(seconds=1)
    db.commit()
    assert claim_work_items(db, worker_id="worker-3", limit=1, lease_seconds=60) == []
    db.refresh(item)
    assert item.status == "terminal_failed"
    assert item.error_code == "CODEX_LEASE_ATTEMPTS_EXHAUSTED"


def test_work_request_contains_real_bundle_content_and_hashes_it(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, step = _seed_step(db)
    answer = db.get(StudentAnswer, step.student_answer_id)
    assert answer is not None
    question = Question(
        id=answer.question_id,
        paper_version_id=uuid.uuid4(),
        question_number="7",
        display_order=7,
        question_type="short_answer",
        content_text="What is 1+1?",
        content_latex="1+1",
        max_score=Decimal("2"),
    )
    reference = ReferenceAnswerVersion(
        question_id=question.id,
        source_type="teacher_official",
        source_region={},
        raw_content="Two",
        normalized_content="2",
        structured_content={"answer": 2},
        content_hash="1" * 64,
        version=1,
        provenance={},
        created_by=run.owner_id,
        status="confirmed",
    )
    db.add_all([question, reference])
    db.flush()
    rubric = StructuredRubricVersion(
        question_id=question.id,
        question_version="synthetic",
        reference_answer_version_id=reference.id,
        rubric_version=1,
        title="Exact answer",
        total_points=Decimal("2"),
        status="confirmed",
        content_hash="2" * 64,
        created_by=run.owner_id,
    )
    db.add(rubric)
    db.flush()
    criterion = RubricCriterion(
        rubric_version_id=rubric.id,
        stable_key="criterion-1",
        title="Correct",
        max_points=Decimal("2"),
        display_order=1,
        criterion_type="correctness",
        required=True,
        dependencies=[],
        expected_evidence={"kind": "answer"},
        validation_mode="manual",
        validation_rule={"equals": "2"},
        manual_review_policy={"manual_only": True},
        partial_credit_policy={},
        metadata_={},
    )
    binding = AssignmentRubricPublicationBinding(
        owner_id=run.owner_id,
        assignment_id=uuid.uuid4(),
        review_session_id=uuid.uuid4(),
        paper_version_id=question.paper_version_id,
        legacy_rubric_version_id=uuid.uuid4(),
        binding_version=1,
        status="confirmed",
        source_binding_hash="3" * 64,
        target_legacy_hash="4" * 64,
        mapping=[{"criterion_stable_key": "criterion-1", "legacy": "q7"}],
        created_by=run.owner_id,
    )
    db.add_all([criterion, binding])
    answer.recognized_text = "2"
    db.commit()
    snapshot_payload = {
        "schema": "processing-input-v1",
        "formal": {
            "reference_answer": {"id": str(reference.id)},
            "structured_rubric": {"id": str(rubric.id)},
        },
        "legacy_projection": {"binding_id": str(binding.id)},
        "recognition_evidence": {"regions": [{"id": "evidence-1"}]},
    }
    monkeypatch.setattr(
        codex_local,
        "build_processing_input_snapshot",
        lambda *_args, **_kwargs: ProcessingInputSnapshot(
            payload=snapshot_payload,
            input_version="9" * 64,
        ),
    )
    request, version, first_hash = codex_local.build_work_request(
        db,
        owner_id=run.owner_id,
        batch_id=run.grading_batch_id,
        submission_id=step.submission_id,
        answer_id=answer.id,
    )
    bundle = request["grading_bundle"]
    assert request["schema_version"] == "criterion-suggestion-v1"
    assert request["processing_input"]["payload"] == snapshot_payload
    assert bundle["question"]["text"] == "What is 1+1?"
    assert bundle["student_answer"]["text"] == "2"
    assert bundle["reference_answer"]["raw_content"] == "Two"
    assert bundle["structured_rubric"]["criteria"][0]["validation_rule"] == {
        "equals": "2"
    }
    assert bundle["legacy_binding"]["criterion_to_legacy_mapping"] == binding.mapping
    assert version == "9" * 64
    answer.recognized_text = "two"
    db.flush()
    _, _, changed_hash = codex_local.build_work_request(
        db,
        owner_id=run.owner_id,
        batch_id=run.grading_batch_id,
        submission_id=step.submission_id,
        answer_id=answer.id,
    )
    assert changed_hash != first_hash
