import uuid
from decimal import Decimal
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.ai_grading.providers import (
    FakeAIScoringProvider,
    ProviderResponse,
    UnavailableAIScoringProvider,
    provider_from_settings,
)
from app.api import ai_grading as ai_grading_api
from app.api.actor import CurrentActor
from app.api.ai_grading import CreateJob, ReviewInput, create_job, job_json, retry_job
from app.api.ai_grading import cancel as cancel_job
from app.api.ai_grading import review as review_suggestion
from app.api.domain import ApiProblem
from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models import (
    AICriterionSuggestion,
    AIFeedbackDraft,
    AIProviderInvocation,
    AIScoringJob,
    CriterionValidationResult,
    Question,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    StudentAnswer,
    Submission,
    now_utc,
)
from sqlalchemy import func, select
from test_math_validation_stale import validation_fixture

from workers.tasks import ai_grading as worker


def scoring_job() -> tuple[Any, AIScoringJob]:
    db, validation, _result = validation_fixture()
    answer = db.get(StudentAnswer, validation.student_answer_id)
    evidence = db.get(QuestionRecognitionEvidence, validation.recognition_evidence_id)
    rubric = db.get(StructuredRubricVersion, validation.rubric_version_id)
    reference = db.get(ReferenceAnswerVersion, validation.reference_answer_version_id)
    question = db.get(Question, validation.question_id)
    submission = db.get(Submission, validation.submission_id)
    assert all((answer, evidence, rubric, reference, question, submission))
    answer.status = "confirmed"
    answer.corrected_text = "1"
    answer.requires_review = False
    evidence.status = "confirmed"
    evidence.stale_at = None
    evidence.requires_review = False
    job = AIScoringJob(
        owner_id=submission.owner_id,
        assignment_id=submission.assignment_id,
        question_id=question.id,
        submission_id=submission.id,
        student_answer_id=answer.id,
        recognition_evidence_id=evidence.id,
        reference_answer_version_id=reference.id,
        rubric_version_id=rubric.id,
        math_validation_job_id=validation.id,
        question_version=answer.question_version_reference,
        scoring_input_version=(
            f"{evidence.input_hash}:{evidence.recognition_version}:"
            f"{evidence.confirmed_revision or 0}"
        ),
        status="queued",
        idempotency_key=uuid.uuid4().hex,
        generation=1,
        attempt=0,
        provider="fake",
        model=None,
        endpoint_mode="deterministic",
        prompt_version="ai-grading-v1",
        schema_version="criterion-suggestion-v1",
        provider_config_version="stage4-v1",
        grading_config_version="stage4-v1",
        request_hash="a" * 64,
        image_count=0,
        image_bytes=0,
        retryable=False,
    )
    db.add(job)
    db.commit()
    return db, job


def test_migration_head_matches_strict_audit_models() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    portal_revision = script.get_revision("0026_student_portal")
    audit_revision = script.get_revision("0025_ai_grading_audit_contract")
    assert script.get_current_head() == portal_revision.revision
    assert portal_revision.down_revision == audit_revision.revision
    assert audit_revision.down_revision == "0024_nullable_publish_readiness_due_at"
    assert {"validation_refs", "error_codes", "requires_review"} <= set(
        AICriterionSuggestion.__table__.columns.keys()
    )
    assert {"error_code", "started_at", "completed_at"} <= set(
        AIProviderInvocation.__table__.columns.keys()
    )


@pytest.fixture(autouse=True)
def no_region_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_region_images", lambda *_args: ([], set(), 0, 0))


def run_with_provider(
    monkeypatch: pytest.MonkeyPatch, provider: object, job: AIScoringJob
) -> dict[str, Any]:
    monkeypatch.setattr(worker, "provider_from_settings", lambda _settings: provider)
    return worker.run_ai_grading.run(str(job.id), job.generation)


def test_fake_provider_completes_audited_idempotent_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job = scoring_job()
    result = run_with_provider(monkeypatch, FakeAIScoringProvider(), job)
    db.expire_all()
    current = db.get(AIScoringJob, job.id)
    suggestion = db.scalar(
        select(AICriterionSuggestion).where(AICriterionSuggestion.ai_scoring_job_id == job.id)
    )
    invocation = db.scalar(
        select(AIProviderInvocation).where(AIProviderInvocation.ai_scoring_job_id == job.id)
    )
    assert result == {"status": "completed", "suggestions": 1}
    assert current.status == "completed"
    assert suggestion.suggested_points == suggestion.max_points
    assert suggestion.requires_review is True
    assert suggestion.validation_refs
    assert suggestion.evidence_refs == [f"recognition:{job.recognition_evidence_id}"]
    assert (
        db.scalar(
            select(func.count())
            .select_from(AIFeedbackDraft)
            .where(AIFeedbackDraft.ai_scoring_job_id == job.id)
        )
        == 1
    )
    assert invocation.response_status == "ok"
    assert invocation.started_at is not None and invocation.completed_at is not None
    serialized = job_json(db, current)
    assert serialized["suggestions"][0]["requires_review"] is True
    assert serialized["suggestions"][0]["status"] == "scored"
    assert serialized["suggestions"][0]["validation_refs"] == suggestion.validation_refs
    assert serialized["evidence"][0]["kind"] == "recognition"
    assert serialized["validation"]["generation"] == 1
    assert serialized["validation"]["results"][0]["id"] in suggestion.validation_refs
    assert serialized["invocations"][0]["started_at"] is not None
    assert run_with_provider(monkeypatch, FakeAIScoringProvider(), job)["status"] == (
        "already_processed"
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(AICriterionSuggestion)
            .where(AICriterionSuggestion.ai_scoring_job_id == job.id)
        )
        == 1
    )
    db.close()


def test_teacher_disposition_is_immutable_against_duplicate_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job = scoring_job()
    run_with_provider(monkeypatch, FakeAIScoringProvider(), job)
    suggestion = db.scalar(
        select(AICriterionSuggestion).where(AICriterionSuggestion.ai_scoring_job_id == job.id)
    )
    assert suggestion is not None
    actor = CurrentActor(job.owner_id, "teacher@example.test")
    data = ReviewInput(action="accepted", reason="教师核验后采纳")
    review_suggestion(suggestion.id, data, db, actor)
    with pytest.raises(ApiProblem) as duplicate:
        review_suggestion(suggestion.id, data, db, actor)
    assert duplicate.value.code == "AI_SUGGESTION_ALREADY_REVIEWED"
    db.close()


def test_default_and_non_test_fake_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(
        provider_from_settings(Settings(_env_file=None)), UnavailableAIScoringProvider
    )
    development = Settings(_env_file=None, app_env="development", ai_grading_provider="fake")
    assert isinstance(provider_from_settings(development), UnavailableAIScoringProvider)
    test_only = Settings(_env_file=None, app_env="test", ai_grading_provider="fake")
    assert isinstance(provider_from_settings(test_only), FakeAIScoringProvider)
    db, job = scoring_job()
    result = run_with_provider(monkeypatch, UnavailableAIScoringProvider(), job)
    db.expire_all()
    current = db.get(AIScoringJob, job.id)
    invocation = db.scalar(
        select(AIProviderInvocation).where(AIProviderInvocation.ai_scoring_job_id == job.id)
    )
    assert result["status"] == "review_pending"
    assert current.error_code == "PROVIDER_UNAVAILABLE"
    assert invocation.error_code == "PROVIDER_UNAVAILABLE"
    assert (
        db.scalar(
            select(func.count())
            .select_from(AICriterionSuggestion)
            .where(AICriterionSuggestion.ai_scoring_job_id == job.id)
        )
        == 0
    )
    db.close()


@pytest.mark.parametrize("kind", ["timeout", "invalid_json", "evidence_ref", "validation_ref"])
def test_invalid_provider_outputs_fail_closed(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    db, job = scoring_job()

    class InvalidProvider:
        name, endpoint_mode = "fake-invalid", "test"

        def score(self, payload: dict[str, Any], context: Any) -> ProviderResponse:
            if kind == "timeout":
                raise TimeoutError
            if kind == "invalid_json":
                return ProviderResponse(None, error="invalid_response:JSONDecodeError")
            valid = FakeAIScoringProvider().score(payload, context)
            assert valid.output is not None
            item = valid.output.criteria[0].model_copy(
                update={
                    "evidence_refs": ["forged"]
                    if kind == "evidence_ref"
                    else valid.output.criteria[0].evidence_refs,
                    "validation_refs": ["forged"]
                    if kind == "validation_ref"
                    else valid.output.criteria[0].validation_refs,
                }
            )
            return ProviderResponse(
                valid.output.model_copy(update={"criteria": [item]}),
                response_hash="b" * 64,
            )

    result = run_with_provider(monkeypatch, InvalidProvider(), job)
    db.expire_all()
    current = db.get(AIScoringJob, job.id)
    assert result["status"] == "failed"
    expected = (
        "PROVIDER_TIMEOUT"
        if kind == "timeout"
        else "PROVIDER_INVALID_JSON"
        if kind == "invalid_json"
        else "PROVIDER_INVALID_RESPONSE"
    )
    assert current.error_code == expected
    assert current.retryable is (kind == "timeout")
    assert (
        db.scalar(
            select(func.count())
            .select_from(AICriterionSuggestion)
            .where(AICriterionSuggestion.ai_scoring_job_id == job.id)
        )
        == 0
    )
    db.close()


def test_late_stale_result_is_audited_but_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job = scoring_job()

    class LateProvider:
        name, endpoint_mode = "fake-late", "test"

        def score(self, payload: dict[str, Any], context: Any) -> ProviderResponse:
            with SessionLocal() as other:
                current = other.get(AIScoringJob, job.id)
                assert current is not None
                current.status = "stale"
                current.stale_at = now_utc()
                current.generation += 1
                other.commit()
            return FakeAIScoringProvider().score(payload, context)

    assert run_with_provider(monkeypatch, LateProvider(), job)["status"] == "discarded_late"
    db.expire_all()
    assert (
        db.scalar(
            select(func.count())
            .select_from(AICriterionSuggestion)
            .where(AICriterionSuggestion.ai_scoring_job_id == job.id)
        )
        == 0
    )
    invocation = db.scalar(
        select(AIProviderInvocation).where(AIProviderInvocation.ai_scoring_job_id == job.id)
    )
    assert invocation.response_status == "discarded_late"
    assert invocation.error_code == "LATE_RESULT_DISCARDED"
    db.close()


def test_manual_validation_never_creates_a_score(monkeypatch: pytest.MonkeyPatch) -> None:
    db, job = scoring_job()
    criterion = db.scalar(
        select(RubricCriterion).where(RubricCriterion.rubric_version_id == job.rubric_version_id)
    )
    assert criterion is not None
    criterion.validation_mode = "manual_only"
    result_row = db.scalar(
        select(CriterionValidationResult).where(
            CriterionValidationResult.validation_job_id == job.math_validation_job_id
        )
    )
    assert result_row is not None
    result_row.result = "manual_required"
    db.commit()
    assert run_with_provider(monkeypatch, FakeAIScoringProvider(), job)["status"] == "abstained"
    db.expire_all()
    suggestion = db.scalar(
        select(AICriterionSuggestion).where(AICriterionSuggestion.ai_scoring_job_id == job.id)
    )
    assert suggestion.status == "manual_required"
    assert suggestion.suggested_points is None
    assert suggestion.error_codes == ["MANUAL_ONLY"]
    db.close()


def test_failed_retry_creates_new_generation_without_reviving_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job = scoring_job()
    job.status = "failed"
    job.error_code = "PROVIDER_INVALID_RESPONSE"
    db.commit()
    monkeypatch.setattr("app.api.ai_grading._enqueue", lambda *_args: None)
    settings = get_settings()
    old_provider = settings.ai_grading_provider
    settings.ai_grading_provider = "fake"
    try:
        response = retry_job(job.id, db, CurrentActor(job.owner_id, "teacher@example.test"))
    finally:
        settings.ai_grading_provider = old_provider
    db.expire_all()
    source = db.get(AIScoringJob, job.id)
    replacement = db.get(AIScoringJob, uuid.UUID(response["id"]))
    assert replacement.id != source.id
    assert replacement.generation == source.generation + 1
    assert replacement.status == "queued"
    assert source.status == "failed"
    db.close()


def _align_job_with_current_provider(job: AIScoringJob) -> None:
    settings = get_settings()
    job.provider = settings.ai_grading_provider
    job.model = settings.ai_grading_model
    job.prompt_version = settings.ai_grading_prompt_version
    job.schema_version = settings.ai_grading_schema_version
    job.grading_config_version = settings.ai_grading_config_version


@pytest.mark.parametrize("hidden_reads", [1, 2])
def test_create_job_rechecks_and_recovers_idempotency_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    hidden_reads: int,
) -> None:
    db, winner = scoring_job()
    winner.idempotency_key = "concurrent-idempotency-key"
    _align_job_with_current_provider(winner)
    db.commit()
    original_scalar = db.scalar
    idempotency_reads = 0

    def hide_concurrent_winner(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal idempotency_reads
        sql = str(statement)
        if "ai_scoring_jobs.idempotency_key" in sql and "ai_scoring_jobs.owner_id" in sql:
            idempotency_reads += 1
            if idempotency_reads <= hidden_reads:
                return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db, "scalar", hide_concurrent_winner)
    response = create_job(
        CreateJob(
            student_answer_id=winner.student_answer_id,
            rubric_version_id=winner.rubric_version_id,
            idempotency_key=winner.idempotency_key,
        ),
        db,
        CurrentActor(winner.owner_id, "teacher@example.test"),
    )

    assert response["id"] == str(winner.id)
    assert idempotency_reads >= hidden_reads + 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(AIScoringJob)
            .where(AIScoringJob.idempotency_key == winner.idempotency_key)
        )
        == 1
    )
    db.close()


def test_broker_dispatch_failure_is_durable_and_releases_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job = scoring_job()
    job.estimated_cost = Decimal("1.25")
    db.commit()

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("synthetic broker outage")

    monkeypatch.setattr(worker.run_ai_grading, "delay", unavailable)
    ai_grading_api._enqueue(job, db, None)
    db.expire_all()
    current = db.get(AIScoringJob, job.id)
    assert current is not None
    assert current.status == "failed"
    assert current.error_code == "AI_WORKER_UNAVAILABLE"
    assert current.retryable is True
    assert current.estimated_cost == Decimal("0")
    assert current.finished_at is not None
    db.close()


@pytest.mark.parametrize(
    ("status", "expected_cost"),
    [("queued", Decimal("0")), ("preparing", Decimal("0")), ("running", Decimal("1.25"))],
)
def test_worker_outer_failure_persists_terminal_state_and_reconciles_reservation(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_cost: Decimal,
) -> None:
    db, job = scoring_job()
    job.status = status
    job.estimated_cost = Decimal("1.25")
    db.commit()

    def crash(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(worker, "_run_ai_grading", crash)
    result = worker.run_ai_grading.run(str(job.id), job.generation)
    db.expire_all()
    current = db.get(AIScoringJob, job.id)
    assert result == {"status": "failed", "error_code": "AI_WORKER_INTERNAL_ERROR"}
    assert current is not None
    assert current.status == "failed"
    assert current.error_code == "AI_WORKER_INTERNAL_ERROR"
    assert current.retryable is True
    assert current.estimated_cost == expected_cost
    assert current.finished_at is not None
    db.close()


def test_pre_provider_stale_releases_reservation() -> None:
    db, stale_job = scoring_job()
    stale_job.estimated_cost = Decimal("1.25")
    answer = db.get(StudentAnswer, stale_job.student_answer_id)
    assert answer is not None
    answer.status = "draft"
    db.commit()
    result = worker.run_ai_grading.run(str(stale_job.id), stale_job.generation)
    db.expire_all()
    current = db.get(AIScoringJob, stale_job.id)
    assert result["status"] == "stale"
    assert current is not None and current.estimated_cost == Decimal("0")
    db.close()


def test_queued_cancel_releases_reservation() -> None:
    db, queued_job = scoring_job()
    queued_job.estimated_cost = Decimal("1.25")
    db.commit()
    cancel_job(
        queued_job.id,
        db,
        CurrentActor(queued_job.owner_id, "teacher@example.test"),
    )
    db.expire_all()
    cancelled = db.get(AIScoringJob, queued_job.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.estimated_cost == Decimal("0")
    db.close()
