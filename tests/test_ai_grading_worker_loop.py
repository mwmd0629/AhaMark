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
from app.ai_grading.request_contract import scoring_input_version, strict_request_hash
from app.api.actor import CurrentActor
from app.api.ai_grading import (
    CreateJob,
    RetryJobInput,
    ReviewInput,
    create_job,
    job_json,
    retry_criterion,
    retry_job,
)
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
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionRecognitionBlock,
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
    answer.status = "recognition_confirmed"
    answer.corrected_text = "1"
    answer.requires_review = False
    evidence.status = "confirmed"
    evidence.stale_at = None
    evidence.requires_review = False
    page = db.scalar(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission.id)
        .order_by(SubmissionPage.page_number)
    )
    assert page is not None
    region = StudentAnswerRegion(
        student_answer_id=answer.id,
        submission_page_id=page.id,
        x=Decimal("0"),
        y=Decimal("0"),
        width=Decimal("1"),
        height=Decimal("1"),
        status="confirmed",
        confirmed_by=submission.owner_id,
        region_version=1,
    )
    db.add(region)
    db.flush()
    block = SubmissionRecognitionBlock(
        submission_recognition_job_id=evidence.recognition_job_id,
        submission_page_id=page.id,
        student_answer_region_id=region.id,
        block_index=0,
        text="1",
        normalized_text="1",
        status="confirmed",
        x=region.x,
        y=region.y,
        width=region.width,
        height=region.height,
        provider="fake",
        provider_version="test-v1",
        recognition_version=evidence.recognition_version,
        requires_review=False,
    )
    db.add(block)
    db.flush()
    evidence.block_sources = [
        {
            "block_id": str(block.id),
            "region_id": str(region.id),
            "region_version": region.region_version,
            "block_recognition_version": block.recognition_version,
        }
    ]
    job = AIScoringJob(
        owner_id=submission.owner_id,
        assignment_id=submission.assignment_id,
        question_id=question.id,
        submission_id=submission.id,
        student_answer_id=answer.id,
        recognition_evidence_id=evidence.id,
        reference_answer_version_id=reference.id,
        structured_rubric_set_id=validation.structured_rubric_set_id,
        rubric_version_id=rubric.id,
        math_validation_job_id=validation.id,
        question_version=answer.question_version_reference,
        scoring_input_version=scoring_input_version(evidence),
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
        request_hash=strict_request_hash(
            answer=answer,
            evidence=evidence,
            rubric_id=rubric.id,
            rubric_content_hash=rubric.content_hash,
            reference_id=reference.id,
            reference_content_hash=reference.content_hash,
            validation_id=validation.id,
            criterion_stable_key=None,
            provider="fake",
            model=None,
            endpoint_mode="deterministic",
            prompt_version="ai-grading-v1",
            schema_version="criterion-suggestion-v1",
            provider_config_version="stage4-v1",
            grading_config_version="stage4-v1",
        ),
        image_count=0,
        image_bytes=0,
        retryable=False,
    )
    db.add(job)
    db.commit()
    return db, job


def test_migration_head_matches_strict_audit_models() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    audit_revision = script.get_revision("0025_ai_grading_audit_contract")
    materialization_revision = script.get_revision("0026_idempotent_materialization")
    semantic_revision = script.get_revision("0027_semantic_projection")
    orchestrator_revision = script.get_revision("0028_processing_orchestrator")
    auto_confirmation_revision = script.get_revision("0029_processing_auto_confirmation")
    collaboration_revision = script.get_revision("0030_collaborative_grading")
    student_portal_revision = script.get_revision("0031_student_portal")
    joint_exam_revision = script.get_revision("0032_joint_exam_roster")
    structured_only_revision = script.get_revision("0034_structured_rubric_authority")
    question_anchor_revision = script.get_revision("0035_question_anchor_segmentation")
    grading_review_revision = script.get_revision("0036_grading_review_commands")
    rubric_templates_revision = script.get_revision("0037_rubric_templates")
    question_structure_revision = script.get_revision("0038_question_structure_reviews")
    pdf_content_revision = script.get_revision("0039_pdf_content_sources")
    assert pdf_content_revision.down_revision == question_structure_revision.revision
    character_boxes_revision = script.get_revision("0040_recognition_character_boxes")
    assert character_boxes_revision.down_revision == pdf_content_revision.revision
    reference_binding_revision = script.get_revision("0041_reference_answer_source_bindings")
    assert reference_binding_revision.down_revision == character_boxes_revision.revision
    answer_binding_revision = script.get_revision("0042_answer_candidate_source_binding")
    assert answer_binding_revision.down_revision == reference_binding_revision.revision
    textbook_matches_revision = script.get_revision("0043_textbook_source_matches")
    assert textbook_matches_revision.down_revision == answer_binding_revision.revision
    textbook_indexes_revision = script.get_revision("0044_textbook_content_indexes")
    assert textbook_indexes_revision.down_revision == textbook_matches_revision.revision
    textbook_question_indexes_revision = script.get_revision("0045_textbook_question_only_indexes")
    assert textbook_question_indexes_revision.down_revision == textbook_indexes_revision.revision
    textbook_libraries_revision = script.get_revision("0046_textbook_libraries")
    assert textbook_libraries_revision.down_revision == textbook_question_indexes_revision.revision
    formula_recognition_revision = script.get_revision("0047_formula_recognition_candidates")
    assert formula_recognition_revision.down_revision == textbook_libraries_revision.revision
    class_resources_revision = script.get_revision("0048_class_resources")
    assert class_resources_revision.down_revision == formula_recognition_revision.revision
    assert script.get_current_head() == class_resources_revision.revision
    assert question_structure_revision.down_revision == rubric_templates_revision.revision
    assert rubric_templates_revision.down_revision == grading_review_revision.revision
    assert grading_review_revision.down_revision == question_anchor_revision.revision
    assert question_anchor_revision.down_revision == structured_only_revision.revision
    assert structured_only_revision.down_revision == "0033_joint_exam_class_authorization"
    assert joint_exam_revision.down_revision == student_portal_revision.revision
    assert student_portal_revision.down_revision == collaboration_revision.revision
    assert collaboration_revision.down_revision == auto_confirmation_revision.revision
    assert orchestrator_revision.down_revision == semantic_revision.revision
    assert semantic_revision.down_revision == materialization_revision.revision
    assert materialization_revision.down_revision == audit_revision.revision
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
    evidence = db.get(QuestionRecognitionEvidence, job.recognition_evidence_id)
    assert evidence is not None
    allowed_refs = {
        f"recognition:{job.recognition_evidence_id}",
        *(str(source["block_id"]) for source in evidence.block_sources),
    }
    assert suggestion.evidence_refs
    assert set(suggestion.evidence_refs) <= allowed_refs
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
        response = retry_job(
            job.id,
            RetryJobInput(idempotency_key="stable-retry", expected_generation=job.generation),
            db,
            CurrentActor(job.owner_id, "teacher@example.test"),
        )
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


def test_strict_create_and_retry_replay_only_the_same_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, seed = scoring_job()
    monkeypatch.setattr("app.api.ai_grading._enqueue", lambda *_args: None)
    settings = get_settings()
    old_provider = settings.ai_grading_provider
    settings.ai_grading_provider = "fake"
    actor = CurrentActor(seed.owner_id, "teacher@example.test")
    try:
        data = CreateJob(
            student_answer_id=seed.student_answer_id,
            idempotency_key="strict-create-stable",
        )
        first = create_job(data, db, actor)
        replay = create_job(data, db, actor)
        assert replay["id"] == first["id"]
        with pytest.raises(ApiProblem) as conflict:
            create_job(data.model_copy(update={"criterion_stable_key": "result"}), db, actor)
        assert conflict.value.code == "IDEMPOTENCY_KEY_CONFLICT"

        source = db.get(AIScoringJob, uuid.UUID(first["id"]))
        assert source is not None
        retry_data = RetryJobInput(
            idempotency_key="strict-retry-stable",
            expected_generation=source.generation,
        )
        retried = retry_job(source.id, retry_data, db, actor)
        retried_again = retry_job(source.id, retry_data, db, actor)
        assert retried_again["id"] == retried["id"]
        with pytest.raises(ApiProblem) as stale_generation:
            retry_job(
                source.id,
                retry_data.model_copy(update={"expected_generation": source.generation + 1}),
                db,
                actor,
            )
        assert stale_generation.value.code == "AI_RETRY_GENERATION_CONFLICT"
        criterion_retry = RetryJobInput(
            idempotency_key="strict-criterion-retry-stable",
            expected_generation=source.generation,
        )
        criterion_first = retry_criterion(source.id, "answer", criterion_retry, db, actor)
        criterion_replay = retry_criterion(source.id, "answer", criterion_retry, db, actor)
        assert criterion_replay["id"] == criterion_first["id"]
    finally:
        settings.ai_grading_provider = old_provider
        db.close()


def test_worker_rejects_task_criterion_contract_drift() -> None:
    db, job = scoring_job()
    result = worker.run_ai_grading.run(str(job.id), job.generation, "result")
    db.expire_all()
    current = db.get(AIScoringJob, job.id)
    assert result == {
        "status": "stale",
        "error_code": "AI_REQUEST_CONTRACT_MISMATCH",
    }
    assert current is not None
    assert current.error_code == "AI_REQUEST_CONTRACT_MISMATCH"
    assert current.stale_at is not None
    db.close()


def test_new_job_generation_fences_inflight_and_preexisting_old_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, old_job = scoring_job()
    monkeypatch.setattr("app.api.ai_grading._enqueue", lambda *_args: None)
    settings = get_settings()
    old_provider = settings.ai_grading_provider
    settings.ai_grading_provider = "fake"
    actor = CurrentActor(old_job.owner_id, "teacher@example.test")

    class CreatesNewGeneration:
        name, endpoint_mode = "fake-generation-fence", "test"

        def score(self, payload: dict[str, Any], context: Any) -> ProviderResponse:
            with SessionLocal() as other:
                created = create_job(
                    CreateJob(
                        student_answer_id=old_job.student_answer_id,
                        idempotency_key="newer-while-provider-running",
                    ),
                    other,
                    actor,
                )
                assert created["generation"] == old_job.generation + 1
            return FakeAIScoringProvider().score(payload, context)

    try:
        result = run_with_provider(monkeypatch, CreatesNewGeneration(), old_job)
        assert result == {"status": "discarded_late"}
        db.expire_all()
        current_old = db.get(AIScoringJob, old_job.id)
        newer = db.scalar(
            select(AIScoringJob).where(
                AIScoringJob.student_answer_id == old_job.student_answer_id,
                AIScoringJob.generation == old_job.generation + 1,
            )
        )
        invocation = db.scalar(
            select(AIProviderInvocation).where(AIProviderInvocation.ai_scoring_job_id == old_job.id)
        )
        assert current_old is not None and current_old.status == "stale"
        assert current_old.error_code == "LATE_RESULT_DISCARDED"
        assert newer is not None and newer.status == "queued"
        assert invocation is not None
        assert invocation.response_status == "discarded_late"
        assert invocation.error_code == "LATE_RESULT_DISCARDED"
        assert (
            db.scalar(
                select(func.count())
                .select_from(AICriterionSuggestion)
                .where(AICriterionSuggestion.ai_scoring_job_id == old_job.id)
            )
            == 0
        )

        preexisting_old = newer
        third = create_job(
            CreateJob(
                student_answer_id=old_job.student_answer_id,
                idempotency_key="preexisting-third-generation",
            ),
            db,
            actor,
        )
        assert third["generation"] == preexisting_old.generation + 1
        monkeypatch.setattr(
            worker,
            "provider_from_settings",
            lambda _settings: pytest.fail("superseded generation must not call provider"),
        )
        assert worker.run_ai_grading.run(str(preexisting_old.id), preexisting_old.generation) == {
            "status": "discarded_late"
        }
    finally:
        settings.ai_grading_provider = old_provider
        db.close()


def test_dispatch_failure_is_retryable_and_same_key_recovers_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, seed = scoring_job()
    settings = get_settings()
    old_provider = settings.ai_grading_provider
    settings.ai_grading_provider = "fake"
    calls = 0

    class Task:
        id = "recovered-task"

    def dispatch(*_args: Any, **_kwargs: Any) -> Task:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("broker unavailable")
        return Task()

    monkeypatch.setattr(worker.run_ai_grading, "delay", dispatch)
    actor = CurrentActor(seed.owner_id, "teacher@example.test")
    data = CreateJob(
        student_answer_id=seed.student_answer_id,
        idempotency_key="dispatch-recovery",
    )
    try:
        failed = create_job(data, db, actor)
        assert failed["status"] == "failed"
        assert failed["error_code"] == "WORKER_UNAVAILABLE"
        assert failed["retryable"] is True
        recovered = create_job(data, db, actor)
        assert recovered["id"] == failed["id"]
        assert recovered["status"] == "queued"
        assert recovered["error_code"] is None
        assert recovered["retryable"] is False
        assert create_job(data, db, actor)["id"] == failed["id"]
        assert calls == 2
    finally:
        settings.ai_grading_provider = old_provider
        db.close()
