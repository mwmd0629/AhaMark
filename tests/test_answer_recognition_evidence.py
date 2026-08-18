import uuid
from decimal import Decimal

import httpx
import pytest
from app.api.actor import CurrentActor
from app.api.grading import RecognitionStartInput, start_submission_recognition
from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    GradingJob,
    Question,
    QuestionRecognitionEvidence,
    QuestionStatus,
    RecognitionRevision,
    RegionEvidenceImage,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionProcessingJob,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    now_utc,
)
from app.recognition import answer_evidence
from app.recognition.answer_providers import (
    AnswerProviderError,
    FakeAnswerProvider,
    OpenAICompatibleAnswerProvider,
    UnavailableAnswerProvider,
    normalize_math,
    provider_from_settings,
)
from app.recognition.pipeline import PageArtifact, ProviderBlock
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified
from test_submission_workflow import workflow

from workers.celery_app import celery_app

client = TestClient(app)


@pytest.mark.parametrize(
    ("app_env", "expected_type"),
    [
        ("test", FakeAnswerProvider),
        ("development", UnavailableAnswerProvider),
        ("staging", UnavailableAnswerProvider),
        ("production", UnavailableAnswerProvider),
    ],
)
def test_fake_answer_provider_is_available_only_in_test(
    app_env: str,
    expected_type: type[FakeAnswerProvider] | type[UnavailableAnswerProvider],
) -> None:
    settings = Settings.model_construct(
        app_env=app_env,
        answer_recognition_provider="fake",
        recognition_provider="unavailable",
    )
    assert isinstance(provider_from_settings(settings), expected_type)


def prepared(*, two_regions: bool = False) -> tuple[object, object, uuid.UUID, StudentAnswer]:
    db, storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous_recognition_provider = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": f"prepare-{uuid.uuid4()}"},
        )
    finally:
        settings.recognition_provider = previous_recognition_provider
    assert processing.status_code == 201, processing.text
    answer = db.scalar(select(StudentAnswer).where(StudentAnswer.submission_id == submission_id))
    assert answer is not None
    for region in db.scalars(
        select(StudentAnswerRegion).where(StudentAnswerRegion.student_answer_id == answer.id)
    ).all():
        db.delete(region)
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission_id)
        .order_by(SubmissionPage.page_number)
    ).all()
    db.flush()
    db.add(
        StudentAnswerRegion(
            student_answer_id=answer.id,
            submission_page_id=pages[0].id,
            x=Decimal("0"),
            y=Decimal("0"),
            width=Decimal("0.5") if two_regions else Decimal("1"),
            height=Decimal("1"),
            source="manual",
            status="confirmed",
            confirmed_by=db.get(Submission, submission_id).owner_id,
        )
    )
    if two_regions:
        db.add(
            StudentAnswerRegion(
                student_answer_id=answer.id,
                submission_page_id=pages[1].id,
                x=Decimal("0.5"),
                y=Decimal("0"),
                width=Decimal("0.5"),
                height=Decimal("1"),
                source="manual",
                status="confirmed",
                confirmed_by=db.get(Submission, submission_id).owner_id,
            )
        )
    db.commit()
    return db, storage, submission_id, answer


def test_confirmed_regions_create_deterministic_evidence_blocks_and_question_sources() -> None:
    db, _storage, submission_id, answer = prepared(two_regions=True)
    region_page_ids = set(
        db.scalars(
            select(StudentAnswerRegion.submission_page_id).where(
                StudentAnswerRegion.student_answer_id == answer.id
            )
        ).all()
    )
    for page in db.scalars(
        select(SubmissionPage).where(
            SubmissionPage.submission_id == submission_id,
            SubmissionPage.id.not_in(region_page_ids),
        )
    ):
        page.status = "blank"
    db.commit()
    settings = get_settings()
    old = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    try:
        response = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={
                "idempotency_key": "answer-evidence-two-regions",
                "provider_kind": "printed_text",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "completed"
        db.expire_all()
        submission = db.get(Submission, submission_id)
        assert submission is not None
        assert submission.status == "recognized"
        assert submission.recognized_at is not None
        assert set(
            db.scalars(
                select(SubmissionPage.status).where(
                    SubmissionPage.submission_id == submission_id,
                    SubmissionPage.id.in_(region_page_ids),
                )
            ).all()
        ) == {"recognized"}
        assert set(
            db.scalars(
                select(SubmissionPage.status).where(SubmissionPage.submission_id == submission_id)
            ).all()
        ).issubset({"recognized", "blank"})
        assert db.scalar(select(func.count()).select_from(RegionEvidenceImage)) == 4
        blocks = db.scalars(
            select(SubmissionRecognitionBlock).order_by(SubmissionRecognitionBlock.reading_order)
        ).all()
        assert len(blocks) == 2
        assert [block.source_page_number for block in blocks] == [1, 1]
        assert len({block.student_answer_region_id for block in blocks}) == 2
        linked_evidence: list[RegionEvidenceImage] = []
        for block in blocks:
            item = db.get(RegionEvidenceImage, block.region_evidence_image_id)
            assert item is not None and item.source_kind == "processed"
            linked_evidence.append(item)
        assert [block.evidence_image_key for block in blocks] == [
            item.object_key for item in linked_evidence
        ]
        original = db.scalar(
            select(RegionEvidenceImage).where(
                RegionEvidenceImage.student_answer_region_id
                == blocks[0].student_answer_region_id,
                RegionEvidenceImage.source_kind == "original",
            )
        )
        assert original is not None
        blocks[0].evidence_image_key = original.object_key
        db.commit()
        block_payloads = client.get(
            f"/api/submissions/{submission_id}/recognition-blocks"
        )
        assert block_payloads.status_code == 200, block_payloads.text
        payload_by_id = {item["id"]: item for item in block_payloads.json()}
        assert payload_by_id[str(blocks[0].id)]["evidence_image_key"] == linked_evidence[
            0
        ].object_key
        evidence = db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.student_answer_id == answer.id
            )
        )
        assert evidence is not None and len(evidence.block_sources) == 2
        again = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={
                "idempotency_key": "answer-evidence-two-regions",
                "provider_kind": "printed_text",
            },
        )
        assert again.json()["id"] == response.json()["id"]
        conflict = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={
                "idempotency_key": "answer-evidence-two-regions",
                "provider_kind": "math_formula",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert "provider_kind" in conflict.json()["details"]["mismatched_fields"]
        assert db.scalar(select(func.count()).select_from(RegionEvidenceImage)) == 4
        replacement = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={
                "idempotency_key": "answer-evidence-new-version",
                "provider_kind": "printed_text",
            },
        )
        assert replacement.status_code == 201, replacement.text
        assert replacement.json()["id"] != response.json()["id"]
        assert db.scalars(
            select(QuestionRecognitionEvidence.recognition_version)
            .where(QuestionRecognitionEvidence.student_answer_id == answer.id)
            .order_by(QuestionRecognitionEvidence.recognition_version)
        ).all() == [1, 2]
    finally:
        settings.answer_recognition_provider = old
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_removed_question_answer_does_not_block_active_answer_recognition() -> None:
    db, _storage, submission_id, active_answer = prepared()
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None
        assignment = db.get(Assignment, submission.assignment_id)
        assert assignment is not None and assignment.active_paper_version_id is not None
        removed_question = Question(
            paper_version_id=assignment.active_paper_version_id,
            question_number="removed-history",
            display_order=99,
            question_type="short_answer",
            status=QuestionStatus.removed,
        )
        db.add(removed_question)
        db.flush()
        removed_answer = StudentAnswer(
            submission_id=submission.id,
            question_id=removed_question.id,
            question_version_reference=str(assignment.active_paper_version_id),
        )
        db.add(removed_answer)
        db.commit()

        response = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "ignore-removed-question-answer"},
        )

        assert response.status_code == 201, response.text
        assert response.json()["status"] == "completed"
        assert db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.student_answer_id == active_answer.id
            )
        ) is not None
        assert db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.student_answer_id == removed_answer.id
            )
        ) is None
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_question_evidence_must_cover_every_confirmed_region_for_grade_and_shared_guard() -> None:
    db, _storage, submission_id, answer = prepared(two_regions=True)
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    try:
        recognition = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "complete-region-coverage"},
        )
        assert recognition.status_code == 201, recognition.text
        answer.requires_review = False
        answer.status = "ready_for_grading"
        db.commit()
        graded = client.post(f"/api/student-answers/{answer.id}/grade")
        assert graded.status_code == 200, graded.text

        evidence = db.scalar(
            select(QuestionRecognitionEvidence)
            .where(QuestionRecognitionEvidence.student_answer_id == answer.id)
            .order_by(QuestionRecognitionEvidence.recognition_version.desc())
        )
        assert evidence is not None and len(evidence.block_sources) == 2
        evidence.block_sources = evidence.block_sources[:1]
        flag_modified(evidence, "block_sources")
        db.commit()

        before = db.scalar(select(func.count()).select_from(GradingJob))
        rejected = client.post(f"/api/student-answers/{answer.id}/grade")
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["code"] == "ANSWER_EVIDENCE_REQUIRED"
        assert rejected.json()["details"]["reason"] == "RECOGNITION_EVIDENCE_STALE"
        assert db.scalar(select(func.count()).select_from(GradingJob)) == before

        submission = db.get(Submission, submission_id)
        assert submission is not None
        eligibility = client.get(
            f"/api/grading-batches/{submission.grading_batch_id}/bulk-accept-eligibility"
        )
        assert eligibility.status_code == 200, eligibility.text
        item = next(
            value for value in eligibility.json()["items"] if value["answer_id"] == str(answer.id)
        )
        assert "RECOGNITION_EVIDENCE_STALE" in item["reasons"]
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_unconfirmed_and_incomplete_segmentation_are_rejected() -> None:
    db, _storage, submission_id, answer = prepared()
    try:
        region = db.scalar(
            select(StudentAnswerRegion).where(StudentAnswerRegion.student_answer_id == answer.id)
        )
        assert region is not None
        region.status = "candidate"
        db.commit()
        response = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "incomplete-segmentation"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "SEGMENTATION_CONFIRMATION_REQUIRED"
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_recognition_requires_latest_completed_processing_before_job_creation() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    try:
        missing = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "processing-missing"},
        )
        assert missing.status_code == 409
        assert missing.json()["code"] == "SUBMISSION_PROCESSING_REQUIRED"
        assert db.scalar(select(func.count()).select_from(SubmissionRecognitionJob)) == 0

        submission = db.get(Submission, submission_id)
        assert submission is not None
        db.add(
            SubmissionProcessingJob(
                owner_id=submission.owner_id,
                submission_id=submission.id,
                status="partially_completed",
                idempotency_key="processing-incomplete",
            )
        )
        db.commit()
        incomplete = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "must-not-dispatch"},
        )
        assert incomplete.status_code == 409
        assert incomplete.json()["code"] == "SUBMISSION_PROCESSING_INCOMPLETE"
        assert incomplete.json()["details"]["status"] == "partially_completed"
        assert db.scalar(select(func.count()).select_from(SubmissionRecognitionJob)) == 0
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_completed_answer_evidence_job_is_a_direct_idempotent_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, storage, submission_id, _answer = prepared()
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    try:
        response = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "direct-repeat"},
        )
        assert response.status_code == 201, response.text
        job_id = uuid.UUID(response.json()["id"])
        before = (
            db.scalar(select(func.count()).select_from(SubmissionRecognitionBlock)),
            db.scalar(select(func.count()).select_from(QuestionRecognitionEvidence)),
        )

        from app.recognition import answer_evidence

        monkeypatch.setattr(
            answer_evidence,
            "provider_from_settings",
            lambda _settings: pytest.fail("completed job must not call provider"),
        )
        answer_evidence.run_answer_evidence_phase(db, storage, settings, job_id)
        after = (
            db.scalar(select(func.count()).select_from(SubmissionRecognitionBlock)),
            db.scalar(select(func.count()).select_from(QuestionRecognitionEvidence)),
        )
        assert after == before
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_completed_processing_async_dispatches_only_answer_evidence_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _storage, submission_id, _answer = prepared()
    dispatched: list[str] = []
    try:
        from workers.celery_app import celery_app

        monkeypatch.setattr(
            celery_app,
            "send_task",
            lambda name, *args, **kwargs: dispatched.append(name),
        )
        response = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs",
            json={"idempotency_key": "answer-evidence-async"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "queued"
        assert dispatched == ["ahamark.answer_recognition.run"]
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_provider_failure_preserves_previous_current_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, storage, submission_id, _answer = prepared()
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    try:
        first = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "preserved-before-failure"},
        )
        assert first.status_code == 201, first.text
        old_block = db.scalar(
            select(SubmissionRecognitionBlock).where(
                SubmissionRecognitionBlock.submission_recognition_job_id
                == uuid.UUID(first.json()["id"])
            )
        )
        submission = db.get(Submission, submission_id)
        assert old_block is not None and submission is not None
        failed_job = SubmissionRecognitionJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            provider="failing",
            provider_version="1",
            provider_kind="printed_text",
            idempotency_key="provider-failure-preserves-old",
            status="queued",
        )
        db.add(failed_job)
        db.commit()

        class FailingProvider:
            name = "failing"
            version = "1"

            def recognize(self, image: PageArtifact, kind: str) -> list[ProviderBlock]:
                raise AnswerProviderError("PROVIDER_UNAVAILABLE", "synthetic failure")

        from app.recognition import answer_evidence

        monkeypatch.setattr(answer_evidence, "provider_from_settings", lambda _: FailingProvider())
        answer_evidence.run_answer_evidence_phase(db, storage, settings, failed_job.id)
        db.refresh(old_block)
        db.refresh(failed_job)
        assert failed_job.status == "failed"
        assert old_block.stale_at is None
        assert old_block.status in {"recognized", "requires_review"}
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_human_revision_is_preserved_stale_propagates_and_finalized_is_read_only() -> None:
    db, _storage, submission_id, answer = prepared()
    settings = get_settings()
    old = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    try:
        client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "human-protection"},
        )
        block = db.scalar(select(SubmissionRecognitionBlock))
        region = db.scalar(
            select(StudentAnswerRegion).where(StudentAnswerRegion.student_answer_id == answer.id)
        )
        assert block is not None and region is not None
        edited = client.patch(
            f"/api/submissions/{submission_id}/recognition-blocks/{block.id}",
            json={"raw_text": "student wrote this", "normalized_text": "student wrote this"},
        )
        assert edited.status_code == 200, edited.text
        assert (
            db.scalar(
                select(RecognitionRevision.id).where(
                    RecognitionRevision.recognition_block_id == block.id,
                    RecognitionRevision.source == "human",
                    RecognitionRevision.stale_at.is_(None),
                )
            )
            is not None
        )
        original_job_id = block.submission_recognition_job_id
        original_recognition_version = block.recognition_version
        rerun = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "human-protection-new-job"},
        )
        assert rerun.status_code == 201, rerun.text
        assert rerun.json()["status"] == "completed", rerun.text
        db.refresh(block)
        job = db.get(SubmissionRecognitionJob, uuid.UUID(rerun.json()["id"]))
        db.refresh(job)
        assert block.text == "student wrote this"
        assert block.stale_at is None
        assert (
            db.scalar(
                select(RecognitionRevision.id).where(
                    RecognitionRevision.recognition_block_id == block.id,
                    RecognitionRevision.source == "human",
                    RecognitionRevision.stale_at.is_(None),
                )
            )
            is not None
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubmissionRecognitionBlock)
                .where(SubmissionRecognitionBlock.submission_recognition_job_id == job.id)
            )
            == 0
        )
        assert "MANUAL_REVISION_PRESERVED" in job.warning_codes
        assert (
            db.scalar(
                select(func.count())
                .select_from(RecognitionRevision)
                .where(RecognitionRevision.source == "human")
            )
            >= 1
        )
        evidence = db.scalar(
            select(QuestionRecognitionEvidence)
            .where(QuestionRecognitionEvidence.student_answer_id == answer.id)
            .order_by(QuestionRecognitionEvidence.recognition_version.desc())
        )
        assert evidence is not None and evidence.recognition_job_id == job.id
        assert len(evidence.block_sources) == 1
        source = evidence.block_sources[0]
        assert source["block_id"] == str(block.id)
        assert source["block_recognition_job_id"] == str(original_job_id)
        assert source["block_recognition_version"] == original_recognition_version
        assert source["preserved_human_revision"] is True
        assert source["region_version"] == region.region_version
        assert source["region_bbox"] == [
            str(region.x),
            str(region.y),
            str(region.width),
            str(region.height),
        ]
        answer.requires_review = False
        answer.status = "ready_for_grading"
        db.commit()
        graded = client.post(f"/api/student-answers/{answer.id}/grade")
        assert graded.status_code == 200, graded.text
        region.region_version += 1
        from app.recognition.answer_evidence import mark_answer_recognition_stale

        mark_answer_recognition_stale(db, answer.id)
        db.commit()
        db.refresh(block)
        assert block.stale_at is not None
        submission = db.get(Submission, submission_id)
        submission.status = "finalized"
        db.commit()
        blocked = client.patch(
            f"/api/submissions/{submission_id}/recognition-blocks/{block.id}",
            json={"raw_text": "must not write"},
        )
        assert blocked.status_code == 409
    finally:
        settings.answer_recognition_provider = old
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_provider_failures_and_conservative_math_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    settings.answer_recognition_base_url = "https://provider.invalid/v1"
    settings.answer_recognition_api_key = "test-only"
    settings.answer_recognition_model = "vision-test"
    provider = OpenAICompatibleAnswerProvider(settings)
    image = PageArtifact(b"png", 1, 1)

    def timeout(*args: object, **kwargs: object) -> object:
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(AnswerProviderError, match="timed out") as timeout_error:
        provider.recognize(image, "printed_text")
    assert timeout_error.value.code == "PROVIDER_TIMEOUT"

    class Response:
        status_code = 429

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(AnswerProviderError) as limited:
        provider.recognize(image, "printed_text")
    assert limited.value.code == "PROVIDER_RATE_LIMITED"

    class Invalid:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Invalid())
    with pytest.raises(AnswerProviderError) as invalid:
        provider.recognize(image, "math_formula")
    assert invalid.value.code == "PROVIDER_INVALID_JSON"

    class Empty:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"blocks":[]}'}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Empty())
    with pytest.raises(AnswerProviderError) as empty:
        provider.recognize(image, "printed_text")
    assert empty.value.code == "PROVIDER_EMPTY_RESULT"

    class Abstain:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"blocks":[],"abstain":true}'}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Abstain())
    with pytest.raises(AnswerProviderError) as abstained:
        provider.recognize(image, "math_formula")
    assert abstained.value.code == "PROVIDER_ABSTAINED"
    with pytest.raises(AnswerProviderError) as unavailable:
        UnavailableAnswerProvider().recognize(image, "math_formula")
    assert unavailable.value.code == "PROVIDER_UNAVAILABLE"

    normalized = normalize_math("ｘ²+□", None, "formula")
    assert normalized.text == "x2+□"
    assert normalized.latex is None
    assert "AMBIGUOUS_CHARACTER" in normalized.warnings
    matrix = normalize_math("1 2\n3 ?", None, "matrix")
    assert "MATRIX_STRUCTURE_UNCERTAIN" in matrix.warnings


def test_late_worker_result_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    db, storage, submission_id, answer = prepared()
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    first = client.post(
        f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
        json={"idempotency_key": "current-before-late"},
    )
    assert first.status_code == 201, first.text
    old_block = db.scalar(
        select(SubmissionRecognitionBlock).where(
            SubmissionRecognitionBlock.submission_recognition_job_id
            == uuid.UUID(first.json()["id"])
        )
    )
    old_evidence = db.scalar(
        select(QuestionRecognitionEvidence).where(
            QuestionRecognitionEvidence.student_answer_id == answer.id
        )
    )
    assert old_block is not None and old_evidence is not None
    job = SubmissionRecognitionJob(
        owner_id=db.get(Submission, submission_id).owner_id,
        submission_id=submission_id,
        provider="late-provider",
        provider_version="1",
        provider_kind="printed_text",
        idempotency_key="late-result",
        status="queued",
    )
    db.add(job)
    db.commit()

    class LateProvider:
        name = "late-provider"
        version = "1"

        def recognize(self, image: PageArtifact, kind: str) -> list[ProviderBlock]:
            with SessionLocal() as independent_db:
                current = independent_db.get(SubmissionRecognitionJob, job.id)
                assert current is not None
                current.generation += 1
                independent_db.commit()
            return [ProviderBlock("text", "obsolete", None, 0.99, (0, 0, 1, 1))]

    from app.recognition import answer_evidence

    monkeypatch.setattr(answer_evidence, "provider_from_settings", lambda _: LateProvider())
    try:
        answer_evidence.run_answer_evidence_phase(db, storage, settings, job.id)
        db.refresh(job)
        db.refresh(old_block)
        db.refresh(old_evidence)
        assert job.error_code == "LATE_RESULT_DISCARDED"
        assert old_block.stale_at is None
        assert old_evidence.stale_at is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubmissionRecognitionBlock)
                .where(SubmissionRecognitionBlock.submission_recognition_job_id == job.id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(QuestionRecognitionEvidence).where(
                    QuestionRecognitionEvidence.recognition_job_id == job.id
                )
            )
            is None
        )
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_new_recognition_job_generation_fences_inflight_and_preexisting_old_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, storage, submission_id, _answer = prepared()
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    monkeypatch.setattr(celery_app, "send_task", lambda *_args, **_kwargs: None)

    class CreatesNewGeneration:
        name = "fake"
        version = "test-v1"
        created = False

        def recognize(self, image: PageArtifact, kind: str) -> list[ProviderBlock]:
            if not self.created:
                self.created = True
                submission = db.get(Submission, submission_id)
                assert submission is not None
                newer = start_submission_recognition(
                    submission_id,
                    RecognitionStartInput(
                        idempotency_key="recognition-newer-inflight"
                    ),
                    db,
                    CurrentActor(submission.owner_id, "teacher@example.test"),
                    storage,
                    False,
                )
                assert newer["generation"] == 2
            return [ProviderBlock("text", "obsolete", None, 0.99, (0, 0, 1, 1))]

    monkeypatch.setattr(
        answer_evidence, "provider_from_settings", lambda _settings: CreatesNewGeneration()
    )
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None
        old = start_submission_recognition(
            submission_id,
            RecognitionStartInput(idempotency_key="recognition-old-inflight"),
            db,
            CurrentActor(submission.owner_id, "teacher@example.test"),
            storage,
            False,
        )
        answer_evidence.run_answer_evidence_phase(
            db, storage, settings, uuid.UUID(old["id"])
        )
        db.expire_all()
        old_row = db.get(SubmissionRecognitionJob, uuid.UUID(old["id"]))
        assert old_row is not None and old_row.status == "stale"
        assert old_row.error_code == "LATE_RESULT_DISCARDED"
        old_id = old_row.id
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubmissionRecognitionBlock)
                .where(SubmissionRecognitionBlock.submission_recognition_job_id == old_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(QuestionRecognitionEvidence)
                .where(QuestionRecognitionEvidence.recognition_job_id == old_id)
            )
            == 0
        )
        newer = db.scalar(
            select(SubmissionRecognitionJob).where(
                SubmissionRecognitionJob.submission_id == submission_id,
                SubmissionRecognitionJob.generation == 2,
            )
        )
        assert newer is not None and newer.status == "queued"
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()

    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None and newer is not None
        third = start_submission_recognition(
            submission_id,
            RecognitionStartInput(idempotency_key="recognition-preexisting-third"),
            db,
            CurrentActor(submission.owner_id, "teacher@example.test"),
            storage,
            False,
        )
        assert third["generation"] == 3
        monkeypatch.setattr(
            answer_evidence,
            "provider_from_settings",
            lambda _settings: pytest.fail("superseded recognition must not call provider"),
        )
        answer_evidence.run_answer_evidence_phase(
            db,
            storage,
            settings,
            newer.id,
        )
        db.expire_all()
        old_row = db.get(SubmissionRecognitionJob, newer.id)
        newer_row = db.get(SubmissionRecognitionJob, uuid.UUID(third["id"]))
        assert old_row is not None and old_row.status == "stale"
        assert old_row.error_code == "LATE_RESULT_DISCARDED"
        assert newer_row is not None and newer_row.status == "queued"
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_late_second_region_discards_committed_first_region_and_retry_does_not_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, storage, submission_id, answer = prepared(two_regions=True)
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    primed = client.post(
        f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
        json={"idempotency_key": "prime-two-region-evidence"},
    )
    assert primed.status_code == 201, primed.text
    db.refresh(answer)
    primed_text = answer.recognized_text
    primed_evidence_count = db.scalar(select(func.count()).select_from(QuestionRecognitionEvidence))
    submission = db.get(Submission, submission_id)
    assert submission is not None
    job = SubmissionRecognitionJob(
        owner_id=submission.owner_id,
        submission_id=submission_id,
        provider="late-second-region",
        provider_version="1",
        provider_kind="printed_text",
        idempotency_key="late-second-region",
        status="queued",
        max_attempts=3,
    )
    db.add(job)
    db.commit()

    class LateSecondRegionProvider:
        name = "late-second-region"
        version = "1"
        calls = 0

        def recognize(self, image: PageArtifact, kind: str) -> list[ProviderBlock]:
            self.calls += 1
            if self.calls == 2:
                with SessionLocal() as independent_db:
                    current = independent_db.get(SubmissionRecognitionJob, job.id)
                    assert current is not None
                    current.generation += 1
                    independent_db.commit()
            return [
                ProviderBlock(
                    "text",
                    f"attempt-block-{self.calls}",
                    None,
                    0.99,
                    (0, 0, 1, 1),
                )
            ]

    provider = LateSecondRegionProvider()
    from app.recognition import answer_evidence

    monkeypatch.setattr(answer_evidence, "provider_from_settings", lambda _: provider)
    try:
        answer_evidence.run_answer_evidence_phase(db, storage, settings, job.id)
        db.expire_all()
        job = db.get(SubmissionRecognitionJob, job.id)
        assert job is not None
        late_blocks = list(
            db.scalars(
                select(SubmissionRecognitionBlock).where(
                    SubmissionRecognitionBlock.submission_recognition_job_id == job.id
                )
            ).all()
        )
        assert job.error_code == "LATE_RESULT_DISCARDED"
        assert len(late_blocks) == 1
        assert late_blocks[0].status == "late_discarded"
        assert late_blocks[0].stale_at is not None
        assert late_blocks[0].requires_review is True
        late_block_ids = {block.id for block in late_blocks}
        assert (
            db.scalar(
                select(func.count())
                .select_from(QuestionRecognitionEvidence)
                .where(QuestionRecognitionEvidence.recognition_job_id == job.id)
            )
            == 0
        )
        db.refresh(answer)
        assert answer.recognized_text == primed_text
        assert (
            db.scalar(select(func.count()).select_from(QuestionRecognitionEvidence))
            == primed_evidence_count
        )

        answer_evidence.run_answer_evidence_phase(db, storage, settings, job.id)
        db.expire_all()
        job = db.get(SubmissionRecognitionJob, job.id)
        assert job is not None
        current_blocks = list(
            db.scalars(
                select(SubmissionRecognitionBlock).where(
                    SubmissionRecognitionBlock.submission_recognition_job_id == job.id,
                    SubmissionRecognitionBlock.stale_at.is_(None),
                )
            ).all()
        )
        assert job.status == "completed"
        assert len(current_blocks) == 2
        assert late_block_ids.isdisjoint({block.id for block in current_blocks})
        assert {block.text for block in current_blocks} == {
            "attempt-block-3",
            "attempt-block-4",
        }
        evidence = db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.recognition_job_id == job.id
            )
        )
        assert evidence is not None
        assert late_block_ids.isdisjoint(
            {uuid.UUID(source["block_id"]) for source in evidence.block_sources}
        )
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_late_cleanup_preserves_existing_same_job_human_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, storage, submission_id, answer = prepared(two_regions=True)
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    pages = list(
        db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        ).all()
    )
    submission = db.get(Submission, submission_id)
    assert submission is not None and len(pages) >= 3
    third_region = StudentAnswerRegion(
        student_answer_id=answer.id,
        submission_page_id=pages[2].id,
        x=Decimal("0"),
        y=Decimal("0"),
        width=Decimal("1"),
        height=Decimal("1"),
        source="manual",
        status="confirmed",
        confirmed_by=submission.owner_id,
    )
    db.add(third_region)
    db.commit()
    primed = client.post(
        f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
        json={"idempotency_key": "same-job-human-prime"},
    )
    assert primed.status_code == 201, primed.text
    job = db.get(SubmissionRecognitionJob, uuid.UUID(primed.json()["id"]))
    assert job is not None
    human_block = db.scalar(
        select(SubmissionRecognitionBlock).where(
            SubmissionRecognitionBlock.submission_recognition_job_id == job.id,
            SubmissionRecognitionBlock.student_answer_region_id == third_region.id,
        )
    )
    assert human_block is not None
    edited = client.patch(
        f"/api/submissions/{submission_id}/recognition-blocks/{human_block.id}",
        json={"raw_text": "preserve me", "normalized_text": "preserve me"},
    )
    assert edited.status_code == 200, edited.text
    human_revision = db.scalar(
        select(RecognitionRevision).where(
            RecognitionRevision.recognition_block_id == human_block.id,
            RecognitionRevision.source == "human",
            RecognitionRevision.stale_at.is_(None),
        )
    )
    assert human_revision is not None
    db.refresh(answer)
    primed_text = answer.recognized_text
    primed_evidence_count = db.scalar(select(func.count()).select_from(QuestionRecognitionEvidence))
    for machine_block in db.scalars(
        select(SubmissionRecognitionBlock).where(
            SubmissionRecognitionBlock.submission_recognition_job_id == job.id,
            SubmissionRecognitionBlock.id != human_block.id,
            SubmissionRecognitionBlock.stale_at.is_(None),
        )
    ):
        machine_block.status = "stale"
        machine_block.stale_at = now_utc()
    job.status = "queued"
    job.generation += 1
    job.max_attempts = 3
    db.commit()

    class SameJobLateProvider:
        name = "same-job-late"
        version = "1"
        calls = 0

        def recognize(self, image: PageArtifact, kind: str) -> list[ProviderBlock]:
            self.calls += 1
            if self.calls == 2:
                with SessionLocal() as independent_db:
                    current = independent_db.get(SubmissionRecognitionJob, job.id)
                    assert current is not None
                    current.generation += 1
                    independent_db.commit()
            return [
                ProviderBlock(
                    "text",
                    f"same-job-attempt-{self.calls}",
                    None,
                    0.99,
                    (0, 0, 1, 1),
                )
            ]

    provider = SameJobLateProvider()
    from app.recognition import answer_evidence

    monkeypatch.setattr(answer_evidence, "provider_from_settings", lambda _: provider)
    try:
        answer_evidence.run_answer_evidence_phase(db, storage, settings, job.id)
        db.expire_all()
        job = db.get(SubmissionRecognitionJob, job.id)
        human_block = db.get(SubmissionRecognitionBlock, human_block.id)
        human_revision = db.get(RecognitionRevision, human_revision.id)
        assert job is not None and job.error_code == "LATE_RESULT_DISCARDED"
        assert human_block is not None and human_block.status == "human_edited"
        assert human_block.stale_at is None
        assert human_revision is not None and human_revision.stale_at is None
        late_block = db.scalar(
            select(SubmissionRecognitionBlock).where(
                SubmissionRecognitionBlock.submission_recognition_job_id == job.id,
                SubmissionRecognitionBlock.text == "same-job-attempt-1",
            )
        )
        assert late_block is not None
        assert late_block.status == "late_discarded"
        assert late_block.stale_at is not None
        late_block_id = late_block.id
        assert (
            db.scalar(select(func.count()).select_from(QuestionRecognitionEvidence))
            == primed_evidence_count
        )
        db.refresh(answer)
        assert answer.recognized_text == primed_text

        answer_evidence.run_answer_evidence_phase(db, storage, settings, job.id)
        db.expire_all()
        job = db.get(SubmissionRecognitionJob, job.id)
        human_block = db.get(SubmissionRecognitionBlock, human_block.id)
        human_revision = db.get(RecognitionRevision, human_revision.id)
        assert job is not None and job.status == "completed"
        assert human_block is not None and human_block.status == "human_edited"
        assert human_block.stale_at is None
        assert human_revision is not None and human_revision.stale_at is None
        newest_evidence = db.scalar(
            select(QuestionRecognitionEvidence)
            .where(QuestionRecognitionEvidence.recognition_job_id == job.id)
            .order_by(QuestionRecognitionEvidence.recognition_version.desc())
        )
        assert newest_evidence is not None
        assert late_block_id not in {
            uuid.UUID(source["block_id"]) for source in newest_evidence.block_sources
        }
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_legacy_page_retry_fails_closed_without_dispatch_or_state_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _storage, submission_id, _answer = prepared()
    settings = get_settings()
    previous = settings.answer_recognition_provider
    settings.answer_recognition_provider = "fake"
    try:
        created = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "page-retry-source"},
        )
        assert created.status_code == 201, created.text
        job = db.get(SubmissionRecognitionJob, uuid.UUID(created.json()["id"]))
        page = db.scalar(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        )
        assert job is not None and page is not None
        before = (job.status, page.status)
        from workers.celery_app import celery_app

        monkeypatch.setattr(
            celery_app,
            "send_task",
            lambda *args, **kwargs: pytest.fail("legacy worker must not be dispatched"),
        )
        response = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs/{job.id}/pages/{page.id}/retry"
        )
        assert response.status_code == 409
        assert response.json()["code"] == "PAGE_RETRY_RESEGMENTATION_REQUIRED"
        db.refresh(job)
        db.refresh(page)
        assert (job.status, page.status) == before
    finally:
        settings.answer_recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_completed_processing_without_answers_cannot_create_recognition_job() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None
        db.add(
            SubmissionProcessingJob(
                owner_id=submission.owner_id,
                submission_id=submission.id,
                status="completed",
                idempotency_key="completed-without-answers",
            )
        )
        db.commit()
        response = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "must-not-create-empty-answer-job"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "SEGMENTATION_CONFIRMATION_REQUIRED"
        assert response.json()["details"]["reason"] == "ANSWERS_MISSING"
        assert db.scalar(select(func.count()).select_from(SubmissionRecognitionJob)) == 0
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()
