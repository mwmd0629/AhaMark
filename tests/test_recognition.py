import io
import uuid
from pathlib import Path

import pytest
from app.core.config import get_settings
from app.main import app
from app.models import Question
from app.recognition.pipeline import (
    DefaultDocumentConverter,
    PageArtifact,
    PillowPreprocessor,
    RapidOcrProvider,
    RecognitionError,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from pypdf import PdfWriter
from test_assignments import FakeStorage, active_class, actor_and_db, create

client = TestClient(app)


def image_bytes(format_: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(output, format_)
    return output.getvalue()


def printed_text_bytes(text: str, contrast: float = 1.0) -> bytes:
    font_path = Path("C:/Windows/Fonts/simhei.ttf")
    if not font_path.exists():
        pytest.skip("合成中文 fixture 需要系统黑体；fixture 不含个人数据")
    image = Image.new("RGB", (900, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.text((24, 40), text, font=ImageFont.truetype(str(font_path), 54), fill="black")
    if contrast != 1.0:
        image = ImageEnhance.Contrast(image).enhance(contrast)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_image_and_pdf_conversion_and_preprocessing() -> None:
    settings = get_settings()
    converter = DefaultDocumentConverter(settings)
    png = converter.convert(image_bytes(), "image/png", 1)
    assert (png.width, png.height) == (320, 240)
    processed = PillowPreprocessor().process(png, {"rotation": 90, "contrast": True})
    assert (processed.width, processed.height) == (240, 320)
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=200)
    writer.add_blank_page(width=200, height=300)
    output = io.BytesIO()
    writer.write(output)
    rendered = DefaultDocumentConverter(settings).convert(output.getvalue(), "application/pdf", 2)
    assert rendered.height > 0
    try:
        converter.convert(b"broken", "application/pdf", 1)
    except RecognitionError as exc:
        assert exc.code == "PDF_INVALID"
    else:
        raise AssertionError("损坏 PDF 应失败")


def test_fake_job_candidates_corrections_confirmation_and_idempotency() -> None:
    actor, db = actor_and_db()
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment = create(client, active_class(db, actor.id).id)
        aid = assignment["id"]
        upload = client.post(
            f"/api/assignments/{aid}/files",
            files={"file": ("paper.png", image_bytes(), "image/png")},
        )
        assert upload.status_code == 201
        detail = client.get(f"/api/assignments/{aid}").json()
        version_id = detail["paper_version"]["id"]
        payload = {"paper_version_id": version_id, "idempotency_key": "recognition-test-1"}
        first = client.post(f"/api/assignments/{aid}/recognition/jobs?run_now=true", json=payload)
        assert first.status_code == 201, first.text
        job = first.json()
        assert job["status"] == "completed" and job["page_summary"]["completed"] == 1
        duplicate = client.post(f"/api/assignments/{aid}/recognition/jobs", json=payload)
        assert duplicate.json()["id"] == job["id"]
        pages = client.get(f"/api/assignments/{aid}/recognition/jobs/{job['id']}/pages").json()
        assert pages[0]["rendered_url"] and pages[0]["processed_url"]
        candidates = client.get(
            f"/api/assignments/{aid}/recognition/jobs/{job['id']}/candidates"
        ).json()
        candidate = candidates[0]
        assert candidate["suggested_score"] is None
        unknown_url = f"/api/assignments/{aid}/recognition/jobs/{job['id']}/confirm"
        unknown = client.post(unknown_url, json={"candidate_ids": [candidate["id"]]})
        assert unknown.status_code == 200
        unknown_question = db.get(Question, uuid.UUID(unknown.json()["created_question_ids"][0]))
        assert unknown_question is not None and unknown_question.max_score is None
        issues = client.get(f"/api/assignments/{aid}/publish-check").json()["issues"]
        assert any(
            issue["code"] == "QUESTION_SCORE_REQUIRED"
            and issue["question_id"] == str(unknown_question.id)
            and issue["question_number"] == unknown_question.question_number
            for issue in issues
        )
        assert any(issue["code"] == "ASSIGNMENT_TOTAL_SCORE_INCOMPLETE" for issue in issues)
        rubric = client.put(
            f"/api/assignments/{aid}/rubrics/{unknown_question.id}",
            json={"standard_answer": "答案", "items": [{"title": "正确", "points": 1}]},
        )
        assert rubric.status_code == 404
        assert client.post(f"/api/assignments/{aid}/publish").status_code == 422
        edited = client.patch(
            f"/api/assignments/{aid}/recognition/jobs/{job['id']}/candidates/{candidate['id']}",
            json={"content_text": "教师修正", "suggested_score": 5},
        )
        assert edited.json()["status"] == "edited"
        url = f"/api/assignments/{aid}/recognition/jobs/{job['id']}/confirm"
        confirmed = client.post(url, json={"candidate_ids": [candidate["id"]]})
        assert len(confirmed.json()["created_question_ids"]) == 1
        again = client.post(url, json={"candidate_ids": [candidate["id"]]})
        assert again.json()["created_question_ids"] == confirmed.json()["created_question_ids"]
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_unavailable_provider_is_explicit() -> None:
    actor, db = actor_and_db()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "unavailable"
    try:
        assignment = create(client, active_class(db, actor.id).id)
        response = client.get(f"/api/assignments/{assignment['id']}/recognition/providers")
        assert response.json()["available"] is False
    finally:
        settings.recognition_provider = previous


def test_real_rapidocr_printed_text_blank_coordinates_and_failure() -> None:
    provider = RapidOcrProvider()
    available, reason = provider.available()
    if not available:
        pytest.skip(reason or "RapidOCR unavailable")
    content = printed_text_bytes("清晰中文印刷体 AhaMark 123")
    blocks = provider.recognize(PageArtifact(content, 900, 180))
    joined = " ".join(block.text or "" for block in blocks)
    assert "AhaMark" in joined and "123" in joined
    assert blocks and all(block.latex is None for block in blocks)
    assert all(0 <= value <= 1 for block in blocks for value in block.region)
    assert all(block.confidence is None or 0 <= block.confidence <= 1 for block in blocks)
    assert provider.recognize(PageArtifact(image_bytes(), 320, 240)) == []
    low = provider.recognize(PageArtifact(printed_text_bytes("低对比度 456", 0.12), 900, 180))
    assert all(block.status in {"recognized", "low_confidence"} for block in low)
    with pytest.raises(RecognitionError, match="RapidOCR") as exc:
        provider.recognize(PageArtifact(b"broken", 10, 10))
    assert exc.value.code == "OCR_FAILED"


def test_real_rapidocr_blocks_are_persisted() -> None:
    provider = RapidOcrProvider()
    available, reason = provider.available()
    if not available:
        pytest.skip(reason or "RapidOCR unavailable")
    actor, db = actor_and_db()
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "rapidocr"
    try:
        assignment = create(client, active_class(db, actor.id).id)
        aid = assignment["id"]
        upload = client.post(
            f"/api/assignments/{aid}/files",
            files={
                "file": (
                    "synthetic-printed.png",
                    printed_text_bytes("开发验证 AhaMark 789"),
                    "image/png",
                )
            },
        )
        assert upload.status_code == 201
        version_id = client.get(f"/api/assignments/{aid}").json()["paper_version"]["id"]
        job = client.post(
            f"/api/assignments/{aid}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "real-ocr-persist"},
        ).json()
        assert job["status"] == "completed"
        blocks = client.get(f"/api/assignments/{aid}/recognition/jobs/{job['id']}/blocks").json()
        assert blocks and any("AhaMark" in (block["text"] or "") for block in blocks)
        assert all(block["source"].startswith("rapidocr:3.9.2") for block in blocks)
        assert all(block["latex"] is None for block in blocks)
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
