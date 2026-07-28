import uuid
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
from app.api.actor import CurrentActor
from app.api.ai_grading import job_json, retry_job
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
    revision = script.get_revision("0025_ai_grading_audit_contract")
    assert script.get_current_head() == revision.revision
    assert revision.down_revision == "0024_nullable_publish_readiness_due_at"
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
