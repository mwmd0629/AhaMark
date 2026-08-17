import copy
import io
import uuid
from dataclasses import replace
from typing import Any

import pytest
from app.api.recognition import run_recognition_job
from app.core.config import get_settings
from app.main import app
from app.models import (
    PageProcessingResult,
    QuestionCandidate,
    QuestionCandidateRegion,
    RecognitionBlock,
    RecognitionJob,
    RecognitionStatus,
)
from app.recognition.page_quality import measure_page_quality
from app.recognition.pipeline import PageArtifact, ProviderBlock
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter
from reportlab.pdfgen import canvas
from sqlalchemy import delete, select
from test_assignments import FakeStorage, active_class, actor_and_db, create

client = TestClient(app)


def _image_bytes(kind: str) -> bytes:
    size = (240, 300) if kind == "rescan" else (1200, 1600)
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    scale_x = size[0] / 1200
    scale_y = size[1] / 1600
    for row in range(10):
        y = round((160 + row * 105) * scale_y)
        draw.rectangle(
            (
                round(120 * scale_x),
                y,
                round(1000 * scale_x),
                y + max(2, round(14 * scale_y)),
            ),
            fill=20,
        )
        draw.rectangle(
            (
                round(120 * scale_x),
                y + max(4, round(28 * scale_y)),
                round(720 * scale_x),
                y + max(6, round(37 * scale_y)),
            ),
            fill=20,
        )
    if kind == "review":
        image = image.filter(ImageFilter.GaussianBlur(6))
    output = io.BytesIO()
    image.convert("RGB").save(output, "PNG")
    return output.getvalue()


def _two_page_text_pdf() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(240, 180))
    for number in (1, 2):
        document.drawString(
            18,
            120,
            f"{number}. Synthetic reliable PDF question with enough embedded text",
        )
        document.showPage()
    document.save()
    return output.getvalue()


def _tiny_reliable_text_pdf() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(120, 150))
    document.setFont("Helvetica", 5)
    document.drawString(6, 100, "1. Synthetic reliable PDF text question with sufficient length")
    document.save()
    return output.getvalue()


def _header_text_pdf() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(320, 420))
    document.drawString(24, 390, "Synthetic reliable PDF header text with sufficient length")
    document.save()
    return output.getvalue()


def _run_uploaded_job(
    db: Any,
    actor: Any,
    storage: FakeStorage,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    idempotency_key: str,
) -> tuple[str, dict[str, Any], set[str]]:
    app.dependency_overrides[get_storage] = lambda: storage
    assignment = create(client, active_class(db, actor.id).id)
    assignment_id = assignment["id"]
    upload = client.post(
        f"/api/assignments/{assignment_id}/files",
        files={"file": (filename, content, content_type)},
    )
    assert upload.status_code == 201, upload.text
    original_keys = set(storage.objects)
    version_id = client.get(f"/api/assignments/{assignment_id}").json()["paper_version"]["id"]
    response = client.post(
        f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
        json={"paper_version_id": version_id, "idempotency_key": idempotency_key},
    )
    assert response.status_code == 201, response.text
    return assignment_id, response.json(), original_keys


@pytest.mark.parametrize(
    ("kind", "expected_level"),
    [("review", "review_required"), ("rescan", "rescan_required")],
)
def test_quality_is_persisted_privately_and_review_blocks_require_manual_action(
    kind: str,
    expected_level: str,
) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous_provider = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment_id, job, _original_keys = _run_uploaded_job(
            db,
            actor,
            storage,
            filename=f"synthetic-{kind}.png",
            content=_image_bytes(kind),
            content_type="image/png",
            idempotency_key=f"quality-{kind}",
        )
        assert job["status"] == "completed"
        job_id = uuid.UUID(job["id"])
        pages = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/pages"
        ).json()
        assert len(pages) == 1
        page = pages[0]
        assert set(page["quality"]) == {"version", "level", "issues"}
        assert page["quality"]["version"] == "pil-page-quality-v1"
        assert page["quality"]["level"] == expected_level
        assert page["quality"]["issues"]
        public_quality = page["processing_parameters"]["page_quality"]
        assert set(public_quality) == {"version", "level", "issues"}
        assert public_quality == page["quality"]
        assert "metrics" not in public_quality

        result = db.scalar(
            select(PageProcessingResult).where(PageProcessingResult.recognition_job_id == job_id)
        )
        assert result is not None
        stored_quality = result.processing_parameters["page_quality"]
        assert set(stored_quality) == {"version", "level", "issues", "metrics"}
        assert stored_quality["level"] == expected_level
        assert stored_quality["metrics"]["quality_score"] == float(result.quality_score)
        assert stored_quality["metrics"]["sharpness_score"] == float(result.blur_score)
        assert stored_quality["metrics"]["shadow_score"] == float(result.shadow_score)
        assert float(result.quality_score) != 0.8
        assert float(result.blur_score) != 0.5
        assert float(result.shadow_score) != 0.5

        blocks = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/blocks"
        ).json()
        assert blocks
        assert all(block["status"] == "manual_required" for block in blocks)

        if expected_level == "rescan_required":
            candidates = client.get(
                f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates"
            ).json()
            assert candidates
            confirmed = client.post(
                f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
                json={"candidate_ids": [candidate["id"] for candidate in candidates]},
            )
            assert confirmed.status_code == 409
            assert confirmed.json()["code"] == "RECOGNITION_PAGE_RESCAN_REQUIRED"
    finally:
        settings.recognition_provider = previous_provider
        app.dependency_overrides.pop(get_storage, None)


def test_reliable_pdf_text_is_reviewable_but_not_hard_blocked_as_rescan() -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous_provider = settings.recognition_provider
    settings.recognition_provider = "unavailable"
    try:
        assignment_id, job, _original_keys = _run_uploaded_job(
            db,
            actor,
            storage,
            filename="synthetic-tiny-reliable.pdf",
            content=_tiny_reliable_text_pdf(),
            content_type="application/pdf",
            idempotency_key="quality-reliable-pdf",
        )
        assert job["status"] == "completed"
        page = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/pages"
        ).json()[0]
        assert "low_resolution" in page["quality"]["issues"]
        assert page["quality"]["level"] == "review_required"
        candidates = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates"
        ).json()
        assert candidates

        confirmed = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidate["id"] for candidate in candidates]},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["created_question_ids"]
    finally:
        settings.recognition_provider = previous_provider
        app.dependency_overrides.pop(get_storage, None)


def test_confirm_can_select_one_page_from_a_complete_multi_page_job() -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous_provider = settings.recognition_provider
    settings.recognition_provider = "unavailable"
    try:
        assignment_id, job, _original_keys = _run_uploaded_job(
            db,
            actor,
            storage,
            filename="synthetic-two-page-confirm.pdf",
            content=_two_page_text_pdf(),
            content_type="application/pdf",
            idempotency_key="quality-two-page-partial-confirm",
        )
        candidates = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates"
        ).json()
        assert len(candidates) >= 2

        confirmed = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidates[0]["id"]]},
        )

        assert confirmed.status_code == 200, confirmed.text
        assert len(confirmed.json()["created_question_ids"]) == 1
    finally:
        settings.recognition_provider = previous_provider
        app.dependency_overrides.pop(get_storage, None)


def test_pdf_header_does_not_exempt_severely_degraded_ocr_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MixedPageProvider:
        name = "rapidocr"
        version = "synthetic-mixed"
        is_demo = True

        def available(self) -> tuple[bool, str | None]:
            return True, None

        def recognize(self, _page: PageArtifact) -> list[ProviderBlock]:
            return [
                ProviderBlock(
                    "question_number",
                    "1. Synthetic OCR-only body question",
                    None,
                    0.9,
                    (0.08, 0.65, 0.84, 0.12),
                    source="rapidocr:synthetic-mixed",
                )
            ]

    clear_metrics = measure_page_quality(PageArtifact(_image_bytes("good"), 1200, 1600))
    severe_metrics = replace(
        clear_metrics,
        sharpness_score=0.1,
        contrast_score=0.1,
        quality_score=0.1,
        issues=("blur", "low_contrast"),
    )
    monkeypatch.setattr(
        "app.api.recognition.provider_from_settings", lambda _settings: MixedPageProvider()
    )
    monkeypatch.setattr("app.api.recognition.measure_page_quality", lambda _page: severe_metrics)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        assignment_id, job, _original_keys = _run_uploaded_job(
            db,
            actor,
            storage,
            filename="synthetic-header-mixed-body.pdf",
            content=_header_text_pdf(),
            content_type="application/pdf",
            idempotency_key="quality-header-mixed-body",
        )

        assert job["status"] == "completed"
        page = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/pages"
        ).json()[0]
        assert page["quality"]["level"] == "rescan_required"
        blocks = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/blocks"
        ).json()
        assert any(block["source"] == "ocr" for block in blocks)
        assert all(
            block["status"] == "manual_required"
            for block in blocks
            if block["status"] in {"adopted", "manual_required"}
        )
        candidates = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates"
        ).json()
        confirmed = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidate["id"] for candidate in candidates]},
        )
        assert confirmed.status_code == 409
        assert confirmed.json()["code"] == "RECOGNITION_PAGE_RESCAN_REQUIRED"
    finally:
        app.dependency_overrides.pop(get_storage, None)


@pytest.mark.parametrize("missing", ["regions", "page_result"])
def test_confirm_requires_nonempty_regions_and_complete_page_results(missing: str) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous_provider = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment_id, job, _original_keys = _run_uploaded_job(
            db,
            actor,
            storage,
            filename=f"synthetic-confirm-{missing}.png",
            content=_image_bytes("good"),
            content_type="image/png",
            idempotency_key=f"quality-confirm-{missing}",
        )
        job_id = uuid.UUID(job["id"])
        candidates = list(
            db.scalars(
                select(QuestionCandidate).where(QuestionCandidate.recognition_job_id == job_id)
            ).all()
        )
        assert candidates
        if missing == "regions":
            db.execute(
                delete(QuestionCandidateRegion).where(
                    QuestionCandidateRegion.question_candidate_id.in_(
                        [candidate.id for candidate in candidates]
                    )
                )
            )
        else:
            db.execute(
                delete(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job_id
                )
            )
        db.commit()

        response = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [str(candidate.id) for candidate in candidates]},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "RECOGNITION_RESULTS_NOT_READY"
    finally:
        settings.recognition_provider = previous_provider
        app.dependency_overrides.pop(get_storage, None)


def test_quality_measurement_failure_on_second_page_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous_provider = settings.recognition_provider
    settings.recognition_provider = "unavailable"
    from app.api import recognition as recognition_api

    original_measure = recognition_api.measure_page_quality
    calls = 0

    def fail_second_measurement(page: PageArtifact) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic quality measurement failure")
        return original_measure(page)

    monkeypatch.setattr(recognition_api, "measure_page_quality", fail_second_measurement)
    try:
        _assignment_id, job, original_keys = _run_uploaded_job(
            db,
            actor,
            storage,
            filename="synthetic-two-page-quality.pdf",
            content=_two_page_text_pdf(),
            content_type="application/pdf",
            idempotency_key="quality-second-page-failure",
        )
        job_id = uuid.UUID(job["id"])
        assert calls == 2
        assert job["status"] == "failed"
        assert (
            list(
                db.scalars(
                    select(PageProcessingResult).where(
                        PageProcessingResult.recognition_job_id == job_id
                    )
                ).all()
            )
            == []
        )
        assert (
            list(
                db.scalars(
                    select(RecognitionBlock).where(RecognitionBlock.recognition_job_id == job_id)
                ).all()
            )
            == []
        )
        assert (
            list(
                db.scalars(
                    select(QuestionCandidate).where(QuestionCandidate.recognition_job_id == job_id)
                ).all()
            )
            == []
        )
        assert set(storage.objects) == original_keys
    finally:
        settings.recognition_provider = previous_provider
        app.dependency_overrides.pop(get_storage, None)


def test_quality_state_rolls_back_and_new_artifacts_are_cleaned_on_final_commit_failure() -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous_provider = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        _assignment_id, created, _original_keys = _run_uploaded_job(
            db,
            actor,
            storage,
            filename="synthetic-quality-rollback.png",
            content=_image_bytes("good"),
            content_type="image/png",
            idempotency_key="quality-final-commit-failure",
        )
        assert created["status"] == "completed"
        job_id = uuid.UUID(created["id"])
        old_results = list(
            db.scalars(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job_id
                )
            ).all()
        )
        old_result_ids = {row.id for row in old_results}
        old_quality = {
            row.paper_page_id: copy.deepcopy(row.processing_parameters["page_quality"])
            for row in old_results
        }
        old_object_keys = set(storage.objects)
        old_artifact_keys = {
            key
            for row in old_results
            for key in (
                row.rendered_storage_key,
                row.processed_storage_key,
                row.thumbnail_storage_key,
            )
            if key
        }
        job = db.get(RecognitionJob, job_id)
        assert job is not None
        job.status = RecognitionStatus.queued
        job.completed_at = None
        db.commit()

        original_commit = db.commit
        commit_calls = 0

        def fail_final_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise RuntimeError("synthetic final quality commit failure")
            original_commit()

        db.commit = fail_final_commit  # type: ignore[method-assign]
        try:
            run_recognition_job(db, storage, job_id)
        finally:
            db.commit = original_commit  # type: ignore[method-assign]

        db.expire_all()
        current_results = list(
            db.scalars(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job_id
                )
            ).all()
        )
        assert {row.id for row in current_results} == old_result_ids
        assert {
            row.paper_page_id: row.processing_parameters["page_quality"] for row in current_results
        } == old_quality
        assert set(storage.objects) == old_object_keys
        assert set(storage.delete_calls).isdisjoint(old_artifact_keys)
        assert db.get(RecognitionJob, job_id).status == RecognitionStatus.failed
    finally:
        settings.recognition_provider = previous_provider
        app.dependency_overrides.pop(get_storage, None)
