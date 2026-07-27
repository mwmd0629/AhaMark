import uuid
from decimal import Decimal

import httpx
import pytest
from app.core.config import get_settings
from app.main import app
from app.models import (
    QuestionRecognitionEvidence,
    RecognitionRevision,
    RegionEvidenceImage,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
)
from app.recognition.answer_providers import (
    AnswerProviderError,
    OpenAICompatibleAnswerProvider,
    UnavailableAnswerProvider,
    normalize_math,
)
from app.recognition.pipeline import PageArtifact, ProviderBlock
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_submission_workflow import workflow

client = TestClient(app)


def prepared(*, two_regions: bool = False) -> tuple[object, object, uuid.UUID, StudentAnswer]:
    db, storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    settings.recognition_provider = "fake"
    processing = client.post(
        f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
        json={"idempotency_key": f"prepare-{uuid.uuid4()}"},
    )
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
        assert db.scalar(select(func.count()).select_from(RegionEvidenceImage)) == 4
        blocks = db.scalars(
            select(SubmissionRecognitionBlock).order_by(SubmissionRecognitionBlock.reading_order)
        ).all()
        assert len(blocks) == 2
        assert [block.source_page_number for block in blocks] == [1, 1]
        assert len({block.student_answer_region_id for block in blocks}) == 2
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
        retried = client.post(
            f"/api/submissions/{submission_id}/regions/{region.id}/recognition/retry?run_now=true"
        )
        assert retried.status_code == 200, retried.text
        db.refresh(block)
        job = db.get(SubmissionRecognitionJob, uuid.UUID(retried.json()["job_id"]))
        assert block.text == "student wrote this"
        assert "MANUAL_REVISION_PRESERVED" in job.warning_codes
        assert (
            db.scalar(
                select(func.count())
                .select_from(RecognitionRevision)
                .where(RecognitionRevision.source == "human")
            )
            >= 1
        )
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
    db, storage, submission_id, _answer = prepared()
    settings = get_settings()
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
            current = db.get(SubmissionRecognitionJob, job.id)
            current.generation += 1
            db.commit()
            return [ProviderBlock("text", "obsolete", None, 0.99, (0, 0, 1, 1))]

    from app.recognition import answer_evidence

    monkeypatch.setattr(answer_evidence, "provider_from_settings", lambda _: LateProvider())
    try:
        answer_evidence.run_answer_evidence_phase(db, storage, settings, job.id)
        db.refresh(job)
        assert job.error_code == "LATE_RESULT_DISCARDED"
        block = db.scalar(
            select(SubmissionRecognitionBlock).where(
                SubmissionRecognitionBlock.submission_recognition_job_id == job.id
            )
        )
        assert block is not None and block.status == "late_discarded"
        assert (
            db.scalar(
                select(QuestionRecognitionEvidence).where(
                    QuestionRecognitionEvidence.recognition_job_id == job.id
                )
            )
            is None
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()
