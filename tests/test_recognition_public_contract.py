import io
import json
import uuid

import pytest
from app.main import app
from app.models import PageProcessingResult, QuestionCandidate, RecognitionBlock, RecognitionJob
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image
from reportlab.pdfgen import canvas
from sqlalchemy import select
from test_assignments import FakeStorage, active_class, actor_and_db, create

client = TestClient(app)

SECRET_PROVIDER = "private-provider-C:\\secret\\model.onnx"
SECRET_VERSION = "private-model-sha256-deadbeef"
SECRET_ERROR = "private exception C:\\secret\\weights.bin"


def _text_pdf() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(300, 200))
    document.drawString(24, 120, "1. Public contract PDF fallback contains reliable embedded text")
    document.save()
    return output.getvalue()


def _image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(output, "PNG")
    return output.getvalue()


class _UnavailablePrivateProvider:
    name = SECRET_PROVIDER
    version = SECRET_VERSION
    is_demo = False

    def available(self) -> tuple[bool, str | None]:
        return False, SECRET_ERROR


class _BrokenFormulaProvider:
    name = SECRET_PROVIDER

    def available(self) -> tuple[bool, str | None]:
        raise RuntimeError(SECRET_ERROR)


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_teacher_contract_redacts_private_adapter_details_and_formula_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    monkeypatch.setattr(
        "app.api.recognition.provider_from_settings",
        lambda _settings: _UnavailablePrivateProvider(),
    )
    monkeypatch.setattr(
        "app.api.recognition.formula_provider_from_settings",
        lambda _settings: _BrokenFormulaProvider(),
    )
    try:
        assignment = create(client, active_class(db, actor.id).id)
        assignment_id = assignment["id"]
        empty_readiness = client.get(
            f"/api/assignments/{assignment_id}/recognition/providers"
        ).json()
        assert empty_readiness["can_start"] is False
        assert empty_readiness["text_readiness"] == {
            "mode": "blocked",
            "action_code": "OCR_REQUIRED",
            "limitations": ["IMAGE_PAGES_REQUIRE_OCR"],
        }
        upload = client.post(
            f"/api/assignments/{assignment_id}/files",
            files={"file": ("fallback.pdf", _text_pdf(), "application/pdf")},
        )
        assert upload.status_code == 201
        paper_version_id = client.get(f"/api/assignments/{assignment_id}").json()["paper_version"][
            "id"
        ]

        readiness_response = client.get(f"/api/assignments/{assignment_id}/recognition/providers")
        assert readiness_response.status_code == 200
        readiness = readiness_response.json()
        assert set(readiness) == {
            "provider",
            "version",
            "available",
            "can_start",
            "demo",
            "reason",
            "text_readiness",
            "pdf_text",
            "formula",
        }
        assert readiness["can_start"] is True
        assert readiness["text_readiness"] == {
            "mode": "pdf_fallback_only",
            "action_code": "PDF_TEXT_MAY_REQUIRE_RESCAN_OR_MANUAL",
            "limitations": ["SCANNED_PDF_MAY_REQUIRE_OCR", "IMAGE_PAGES_REQUIRE_OCR"],
        }
        assert readiness["formula"]["available"] is False

        job_response = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": paper_version_id, "idempotency_key": "public-contract"},
        )
        assert job_response.status_code == 201
        job_id = job_response.json()["id"]
        assert job_response.json()["status"] == "completed"

        job = db.scalar(select(RecognitionJob).where(RecognitionJob.id == uuid.UUID(job_id)))
        assert job is not None
        job.provider = SECRET_PROVIDER
        job.provider_version = SECRET_VERSION
        job.config_version = SECRET_VERSION
        job.error_code = "PRIVATE_INTERNAL_FAILURE"
        job.error_message = SECRET_ERROR
        page = db.scalar(
            select(PageProcessingResult).where(PageProcessingResult.recognition_job_id == job.id)
        )
        assert page is not None
        page.error_code = "PRIVATE_INTERNAL_FAILURE"
        page.error_message = SECRET_ERROR
        page.processing_parameters = {
            "private_path": SECRET_ERROR,
            "recognition_sources": [f"rapidocr:{SECRET_VERSION}"],
            "page_quality": {
                "version": SECRET_VERSION,
                "level": "good",
                "issues": [],
                "metrics": {"private_hash": SECRET_VERSION},
            },
            "math_structure": {"version": SECRET_VERSION, "risk_codes": [], "evidence": []},
            "source_conflict_count": 2,
            "math_symbol_conflict_count": 1,
            "missing_region_count": 3,
            "source_agreement_ratio": 0.5,
        }
        for block in db.scalars(
            select(RecognitionBlock).where(RecognitionBlock.recognition_job_id == job.id)
        ):
            block.source = SECRET_PROVIDER
            block.character_boxes = [{"source_index": 7, "text": "A", "x": 0.1}]
        for candidate in db.scalars(
            select(QuestionCandidate).where(QuestionCandidate.recognition_job_id == job.id)
        ):
            candidate.source = SECRET_PROVIDER
        db.commit()

        public_job = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job_id}"
        ).json()
        public_pages = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job_id}/pages"
        ).json()
        public_blocks = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job_id}/blocks"
        ).json()
        public_candidates = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job_id}/candidates"
        ).json()

        assert public_job["provider"] == "local_ocr"
        assert public_job["provider_version"] == "redacted"
        assert public_job["config_version"] == "redacted"
        assert public_job["error_code"] == "RECOGNITION_FAILED"
        assert public_job["error_message"] == "页面识别失败，请重试或人工录入"
        assert public_pages[0]["error_code"] == "RECOGNITION_FAILED"
        assert set(public_pages[0]) == {
            "id",
            "paper_page_id",
            "status",
            "stage",
            "progress",
            "width",
            "height",
            "quality",
            "math_structure",
            "error_code",
            "error_message",
            "rendered_url",
            "processed_url",
            "thumbnail_url",
            "processing_parameters",
        }
        assert public_pages[0]["quality"]["version"] is None
        assert public_pages[0]["math_structure"]["version"] is None
        parameters = public_pages[0]["processing_parameters"]
        assert set(parameters) == {"page_quality", "math_structure", "source_review"}
        assert set(parameters["page_quality"]) == {"version", "level", "issues"}
        assert parameters["page_quality"]["version"] is None
        assert set(parameters["math_structure"]) == {"version", "risk_codes", "evidence"}
        assert parameters["math_structure"]["version"] is None
        assert set(parameters["source_review"]) == {
            "source_conflict_count",
            "math_symbol_conflict_count",
            "missing_region_count",
            "source_agreement_ratio",
        }
        assert public_blocks and all(block["source"] == "ocr" for block in public_blocks)
        assert all(
            "source_index" not in box for block in public_blocks for box in block["character_boxes"]
        )
        assert public_candidates and all(
            candidate["source"] == "ocr" for candidate in public_candidates
        )
        rendered = _serialized(
            [readiness, public_job, public_pages, public_blocks, public_candidates]
        )
        for private_value in (SECRET_PROVIDER, SECRET_VERSION, SECRET_ERROR):
            assert private_value not in rendered

        mixed_upload = client.post(
            f"/api/assignments/{assignment_id}/files",
            files={"file": ("mixed.png", _image(), "image/png")},
        )
        assert mixed_upload.status_code == 201
        mixed_readiness = client.get(
            f"/api/assignments/{assignment_id}/recognition/providers"
        ).json()
        assert mixed_readiness["can_start"] is False
        assert mixed_readiness["text_readiness"]["mode"] == "blocked"
        rejected = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": paper_version_id, "idempotency_key": "mixed-contract"},
        )
        assert rejected.status_code == 503
        assert rejected.json()["code"] == "RECOGNITION_PROVIDER_UNAVAILABLE"
        assert SECRET_ERROR not in rejected.text
    finally:
        app.dependency_overrides.pop(get_storage, None)
