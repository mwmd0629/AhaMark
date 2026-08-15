import io
import uuid
from pathlib import Path

import pytest
from app.api.recognition import question_source_kind, run_recognition_job
from app.core.config import get_settings
from app.main import app
from app.models import (
    PageProcessingResult,
    PageRecognitionStatus,
    Question,
    QuestionCandidate,
    RecognitionBlock,
    RecognitionJob,
    RecognitionStatus,
)
from app.recognition.pipeline import (
    DefaultDocumentConverter,
    PageArtifact,
    PillowPreprocessor,
    ProviderBlock,
    QuestionAnchor,
    RapidOcrProvider,
    RecognitionError,
    derive_question_regions,
    extract_pdf_text_layer,
    fuse_text_sources,
    parse_hierarchical_question_number,
    text_for_question_region,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from pypdf import PdfWriter
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from test_assignments import FakeStorage, active_class, actor_and_db, create

client = TestClient(app)


def image_bytes(format_: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(output, format_)
    return output.getvalue()


def readable_page_image_bytes() -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (700, 900), "white")
    image.save(output, "PNG")
    return output.getvalue()


def text_pdf_bytes(text: str) -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(300, 200))
    document.drawString(24, 120, text)
    document.save()
    return output.getvalue()


def image_pdf_bytes() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(320, 240))
    document.drawInlineImage(Image.open(io.BytesIO(image_bytes())), 0, 0, 320, 240)
    document.save()
    return output.getvalue()


def structured_text_pdf_bytes() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(300, 220))
    document.drawString(24, 185, "Synthetic mathematics assignment")
    document.drawString(24, 145, "2(3) Compute the derivative")
    document.drawString(24, 105, "12(2) Prove the limit")
    document.save()
    return output.getvalue()


def two_page_pdf_bytes(*, include_text: bool = False) -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(320, 240))
    for page_number in (1, 2):
        if include_text:
            document.drawString(
                24,
                160,
                f"{page_number}. Synthetic atomic recognition page with reliable embedded text",
            )
        document.showPage()
    document.save()
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


def test_embedded_pdf_text_layer_has_explicit_source() -> None:
    blocks = extract_pdf_text_layer(
        text_pdf_bytes("Synthetic mathematics assignment question 1 2 3"), 1
    )
    assert len(blocks) == 1
    assert blocks[0].source == "pdf_text:pypdfium2"
    assert "mathematics assignment" in (blocks[0].text or "")
    assert blocks[0].confidence == 1.0


def test_conservative_text_source_fusion_preserves_evidence_and_safe_metrics() -> None:
    pdf = ProviderBlock(
        "text",
        "Synthetic mathematics question x² with enough embedded text",
        None,
        1.0,
        (0.1, 0.1, 0.7, 0.1),
        source="pdf_text:pypdfium2",
    )
    conflicting_ocr = ProviderBlock(
        "text",
        "Synthetic mathematics question x2 with enough embedded text",
        None,
        0.9,
        (0.1, 0.1, 0.7, 0.1),
        source="rapidocr:synthetic",
    )
    missing_ocr = ProviderBlock(
        "text",
        "Supplemental line",
        None,
        0.85,
        (0.1, 0.4, 0.5, 0.08),
        source="rapidocr:synthetic",
    )

    fusion = fuse_text_sources([pdf], [conflicting_ocr, missing_ocr])

    assert [block.status for block in fusion.blocks] == [
        "manual_required",
        "source_conflict",
        "adopted",
    ]
    assert fusion.metrics == {
        "source_conflict_count": 1,
        "math_symbol_conflict_count": 1,
        "missing_region_count": 1,
        "source_agreement_ratio": 0.0,
    }
    assert all(isinstance(value, (int, float, type(None))) for value in fusion.metrics.values())


def test_conservative_text_source_fusion_marks_agreement_and_unreliable_layers() -> None:
    reliable = ProviderBlock(
        "text",
        "A sufficiently long embedded text line for reliable evidence",
        None,
        1.0,
        (0.1, 0.1, 0.7, 0.1),
        source="pdf_text:pypdfium2",
    )
    agreement = ProviderBlock(
        "text",
        "A sufficiently long embedded text line for reliable evidence",
        None,
        0.9,
        (0.1, 0.1, 0.7, 0.1),
        source="rapidocr:synthetic",
    )
    agreed = fuse_text_sources([reliable], [agreement])
    assert [block.status for block in agreed.blocks] == ["adopted", "source_agreement"]
    assert agreed.source_agreement_ratio == 1.0

    short = ProviderBlock(
        "text",
        "short",
        None,
        1.0,
        (0.1, 0.1, 0.2, 0.1),
        source="pdf_text:pypdfium2",
    )
    replacement = ProviderBlock(
        "text",
        "OCR replacement",
        None,
        0.8,
        (0.1, 0.1, 0.2, 0.1),
        source="rapidocr:synthetic",
    )
    replaced = fuse_text_sources([short], [replacement])
    assert [block.status for block in replaced.blocks] == ["unreliable_source", "adopted"]
    assert replaced.source_agreement_ratio is None
    assert question_source_kind("mixed:conservative_fusion") == "mixed"


def test_non_pdf_ocr_regions_do_not_suppress_each_other_and_are_order_independent() -> None:
    pdf = ProviderBlock(
        "text",
        "A sufficiently long embedded heading used as reliable PDF evidence",
        None,
        1.0,
        (0.05, 0.05, 0.8, 0.08),
        source="pdf_text:pypdfium2",
    )
    first = ProviderBlock(
        "text",
        "first missing line",
        None,
        0.9,
        (0.1, 0.5, 0.6, 0.08),
        source="rapidocr:synthetic",
    )
    second = ProviderBlock(
        "text",
        "second overlapping missing line",
        None,
        0.85,
        (0.12, 0.51, 0.6, 0.08),
        source="rapidocr:synthetic",
    )

    forward = fuse_text_sources([pdf], [first, second])
    reverse = fuse_text_sources([pdf], [second, first])

    assert forward.missing_region_count == reverse.missing_region_count == 2
    assert sorted((block.text, block.status) for block in forward.blocks[1:]) == sorted(
        (block.text, block.status) for block in reverse.blocks[1:]
    )
    assert all(block.status == "adopted" for block in forward.blocks[1:])


def test_math_whitespace_topology_is_not_normalized_into_false_agreement() -> None:
    pdf = ProviderBlock(
        "text",
        "Synthetic matrix row: 1 2 with enough reliable embedded text",
        None,
        1.0,
        (0.1, 0.1, 0.7, 0.1),
        source="pdf_text:pypdfium2",
    )
    ocr = ProviderBlock(
        "text",
        "Synthetic matrix row: 12 with enough reliable embedded text",
        None,
        0.9,
        (0.1, 0.1, 0.7, 0.1),
        source="rapidocr:synthetic",
    )

    fusion = fuse_text_sources([pdf], [ocr])

    assert [block.status for block in fusion.blocks] == ["manual_required", "source_conflict"]
    assert fusion.math_symbol_conflict_count == 1


def test_multiple_math_conflicts_for_one_pdf_block_are_idempotent_and_order_independent() -> None:
    pdf = ProviderBlock(
        "text",
        "Synthetic formula x² + α with enough reliable embedded text",
        None,
        1.0,
        (0.1, 0.1, 0.7, 0.1),
        source="pdf_text:pypdfium2",
    )
    first = ProviderBlock(
        "text",
        "Synthetic formula x2 + α with enough reliable embedded text",
        None,
        0.9,
        (0.1, 0.1, 0.35, 0.1),
        source="rapidocr:synthetic",
    )
    second = ProviderBlock(
        "text",
        "formula x² + a with enough reliable embedded text",
        None,
        0.85,
        (0.4, 0.1, 0.4, 0.1),
        source="rapidocr:synthetic",
    )

    forward = fuse_text_sources([pdf], [first, second])
    reverse = fuse_text_sources([pdf], [second, first])

    assert forward.math_symbol_conflict_count == reverse.math_symbol_conflict_count == 2
    assert forward.source_conflict_count == reverse.source_conflict_count == 2
    assert forward.blocks[0].status == reverse.blocks[0].status == "manual_required"
    assert sorted((block.text, block.status) for block in forward.blocks[1:]) == sorted(
        (block.text, block.status) for block in reverse.blocks[1:]
    )
    assert all(block.status == "source_conflict" for block in forward.blocks[1:])


def test_embedded_pdf_text_is_split_into_coordinate_line_blocks_and_anchors() -> None:
    blocks = extract_pdf_text_layer(structured_text_pdf_bytes(), 1)
    assert [block.block_type for block in blocks] == ["text", "question_number", "question_number"]
    assert [parse_hierarchical_question_number(block.text or "") for block in blocks] == [
        None,
        "2(3)",
        "12(2)",
    ]
    assert all(block.source == "pdf_text:pypdfium2" for block in blocks)
    assert all(0 <= value <= 1 for block in blocks for value in block.region)
    assert all(block.region[2] < 1 and block.region[3] < 0.2 for block in blocks)
    assert blocks[0].region[1] < blocks[1].region[1] < blocks[2].region[1]
    assert all(block.character_boxes for block in blocks)
    assert all(
        0 <= float(character_box[key]) <= 1
        for block in blocks
        for character_box in block.character_boxes
        for key in ("x", "y", "width", "height")
    )
    assert "".join(
        str(character_box["text"]) for character_box in blocks[1].character_boxes
    ).startswith("2(3)")


def test_hierarchical_question_number_parser_is_bounded_to_line_start() -> None:
    assert parse_hierarchical_question_number("1. First") == "1"
    assert parse_hierarchical_question_number(" 2 ( 3 ) Second") == "2(3)"
    assert parse_hierarchical_question_number("11(2)：Third") == "11(2)"
    assert parse_hierarchical_question_number("12（2） Fourth") == "12(2)"
    assert parse_hierarchical_question_number("一、求极限") == "1"
    assert parse_hierarchical_question_number("（十）证明") == "10"
    assert parse_hierarchical_question_number("二十一、计算") == "21"
    assert parse_hierarchical_question_number("⑫ 选择正确结论") == "12"
    assert parse_hierarchical_question_number("Answer for 2(3)") is None
    assert parse_hierarchical_question_number("2026 academic year") is None


def test_question_regions_partition_same_page_and_cross_page_boundaries() -> None:
    page_1, page_2, page_3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    anchor_1, anchor_2, anchor_3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    regions = derive_question_regions(
        [page_1, page_2, page_3],
        [
            QuestionAnchor(anchor_1, page_1, 0.7),
            QuestionAnchor(anchor_2, page_3, 0.2),
            QuestionAnchor(anchor_3, page_3, 0.6),
        ],
    )
    assert [row.paper_page_id for row in regions[anchor_1]] == [page_1, page_2, page_3]
    assert [row.y for row in regions[anchor_1]] == pytest.approx([0.7, 0.0, 0.0])
    assert [row.height for row in regions[anchor_1]] == pytest.approx([0.3, 1.0, 0.2])
    assert [row.paper_page_id for row in regions[anchor_2]] == [page_3]
    assert [row.y for row in regions[anchor_2]] == pytest.approx([0.2])
    assert [row.height for row in regions[anchor_2]] == pytest.approx([0.4])
    assert [row.paper_page_id for row in regions[anchor_3]] == [page_3]
    assert [row.y for row in regions[anchor_3]] == pytest.approx([0.6])
    assert [row.height for row in regions[anchor_3]] == pytest.approx([0.4])
    assert all(row.x == 0 and row.width == 1 for rows in regions.values() for row in rows)


def test_question_region_text_joins_trusted_lines_and_rejects_fake_blocks() -> None:
    page = uuid.uuid4()
    region = derive_question_regions([page], [QuestionAnchor(uuid.uuid4(), page, 0.2)]).popitem()[1]
    blocks = [
        (
            page,
            ProviderBlock(
                "question_number",
                "1. 求极限",
                None,
                1,
                (0.1, 0.2, 0.4, 0.05),
                source="pdf_text:pypdfium2",
            ),
        ),
        (
            page,
            ProviderBlock(
                "text",
                "并说明理由。",
                None,
                1,
                (0.1, 0.3, 0.4, 0.05),
                source="pdf_text:pypdfium2",
            ),
        ),
        (page, ProviderBlock("text", "测试题", None, 0.95, (0.1, 0.4, 0.4, 0.05), source="fake:1")),
    ]

    assert text_for_question_region(blocks, region) == "1. 求极限\n并说明理由。"


def test_pdf_text_anchors_create_hierarchical_question_candidates() -> None:
    actor, db = actor_and_db()
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "unavailable"
    try:
        assignment = create(client, active_class(db, actor.id).id)
        aid = assignment["id"]
        upload = client.post(
            f"/api/assignments/{aid}/files",
            files={
                "file": (
                    "synthetic-structured-text.pdf",
                    structured_text_pdf_bytes(),
                    "application/pdf",
                )
            },
        )
        assert upload.status_code == 201
        version_id = client.get(f"/api/assignments/{aid}").json()["paper_version"]["id"]
        job = client.post(
            f"/api/assignments/{aid}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "pdf-text-anchors"},
        ).json()
        assert job["status"] == "completed"
        candidates = client.get(
            f"/api/assignments/{aid}/recognition/jobs/{job['id']}/candidates"
        ).json()
        assert [candidate["temporary_number"] for candidate in candidates] == ["2(3)", "12(2)"]
        assert candidates[0]["content_text"] == "2(3) Compute the derivative"
        assert all(candidate["source"] == "pdf_text:pypdfium2" for candidate in candidates)
        assert all(
            candidate["quality_stats"]["text_source"] == "pdf_text" for candidate in candidates
        )
        assert all(
            candidate["quality_stats"]["suspicious_character_count"] == 0
            for candidate in candidates
        )
        assert all(candidate["regions"] for candidate in candidates)
        assert all(
            float(region["x"]) == 0 and float(region["width"]) == 1
            for candidate in candidates
            for region in candidate["regions"]
        )
        assert len(candidates[0]["regions"]) == 1
        assert float(candidates[0]["regions"][0]["height"]) < 0.3
        assert float(candidates[1]["regions"][0]["height"]) > 0.4
        pages = client.get(f"/api/assignments/{aid}/recognition/jobs/{job['id']}/pages").json()
        page_quality = pages[0]["processing_parameters"]["text_quality"]
        assert page_quality["text_source"] == "pdf_text"
        assert page_quality["character_count"] > 0
        assert page_quality["low_confidence_block_count"] == 0
        assert page_quality["suspicious_character_count"] == 0
        recognition_blocks = client.get(
            f"/api/assignments/{aid}/recognition/jobs/{job['id']}/blocks"
        ).json()
        anchor_blocks = [
            block for block in recognition_blocks if block["block_type"] == "question_number"
        ]
        assert anchor_blocks and all(block["character_boxes"] for block in anchor_blocks)
        confirmed = client.post(
            f"/api/assignments/{aid}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidate["id"] for candidate in candidates]},
        ).json()
        questions = [
            db.get(Question, uuid.UUID(item)) for item in confirmed["created_question_ids"]
        ]
        assert questions and all(
            question is not None and question.source == "pdf_text" for question in questions
        )
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_text_pdf_completes_without_ocr_provider() -> None:
    actor, db = actor_and_db()
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "unavailable"
    try:
        assignment = create(client, active_class(db, actor.id).id)
        aid = assignment["id"]
        upload = client.post(
            f"/api/assignments/{aid}/files",
            files={
                "file": (
                    "synthetic-text.pdf",
                    text_pdf_bytes("Synthetic mathematics assignment question 1 2 3"),
                    "application/pdf",
                )
            },
        )
        assert upload.status_code == 201
        version_id = client.get(f"/api/assignments/{aid}").json()["paper_version"]["id"]
        job = client.post(
            f"/api/assignments/{aid}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "pdf-text-no-ocr"},
        ).json()
        assert job["status"] == "completed"
        blocks = client.get(f"/api/assignments/{aid}/recognition/jobs/{job['id']}/blocks").json()
        assert blocks and all(block["source"] == "pdf_text:pypdfium2" for block in blocks)
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_scanned_or_blank_pdf_fails_explicitly_without_ocr_provider() -> None:
    actor, db = actor_and_db()
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "unavailable"
    try:
        assignment = create(client, active_class(db, actor.id).id)
        aid = assignment["id"]
        upload = client.post(
            f"/api/assignments/{aid}/files",
            files={"file": ("synthetic-scan.pdf", image_pdf_bytes(), "application/pdf")},
        )
        assert upload.status_code == 201
        version_id = client.get(f"/api/assignments/{aid}").json()["paper_version"]["id"]
        job = client.post(
            f"/api/assignments/{aid}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "scan-no-ocr"},
        ).json()
        assert job["status"] == "failed"
        assert job["error_code"] == "RECOGNITION_PROVIDER_UNAVAILABLE"
        pages = client.get(f"/api/assignments/{aid}/recognition/jobs/{job['id']}/pages").json()
        assert pages == []
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


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
            files={"file": ("paper.png", readable_page_image_bytes(), "image/png")},
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


def _upload_two_page_recognition_fixture(
    db: object,
    actor: object,
    storage: FakeStorage,
    *,
    include_text: bool = False,
) -> tuple[str, str, set[str]]:
    app.dependency_overrides[get_storage] = lambda: storage
    assignment = create(client, active_class(db, actor.id).id)  # type: ignore[attr-defined]
    assignment_id = assignment["id"]
    upload = client.post(
        f"/api/assignments/{assignment_id}/files",
        files={
            "file": (
                "synthetic-atomic-two-page.pdf",
                two_page_pdf_bytes(include_text=include_text),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    version_id = client.get(f"/api/assignments/{assignment_id}").json()["paper_version"]["id"]
    return assignment_id, version_id, set(storage.objects)


def test_atomic_batch_discards_first_page_when_second_page_is_damaged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    original_convert = DefaultDocumentConverter.convert

    def fail_second_page(
        converter: DefaultDocumentConverter,
        content: bytes,
        content_type: str,
        source_page: int,
    ) -> PageArtifact:
        if source_page == 2:
            raise RecognitionError("PDF_INVALID", "synthetic damaged second page")
        return original_convert(converter, content, content_type, source_page)

    monkeypatch.setattr(DefaultDocumentConverter, "convert", fail_second_page)
    try:
        assignment_id, version_id, original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage, include_text=True
        )
        job = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "atomic-damaged-page"},
        ).json()

        assert job["status"] == "failed"
        assert (
            list(
                db.scalars(
                    select(RecognitionBlock).where(
                        RecognitionBlock.recognition_job_id == uuid.UUID(job["id"])
                    )
                ).all()
            )
            == []
        )
        assert (
            list(
                db.scalars(
                    select(QuestionCandidate).where(
                        QuestionCandidate.recognition_job_id == uuid.UUID(job["id"])
                    )
                ).all()
            )
            == []
        )
        page_results = list(
            db.scalars(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == uuid.UUID(job["id"])
                )
            ).all()
        )
        assert not any(row.status == PageRecognitionStatus.completed for row in page_results)
        assert set(storage.objects) == original_keys
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_atomic_batch_discards_first_page_when_provider_fails_on_second_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SecondPageFailureProvider:
        name = "synthetic-atomic"
        version = "1"
        is_demo = True

        def __init__(self) -> None:
            self.calls = 0

        def available(self) -> tuple[bool, str | None]:
            return True, None

        def recognize(self, page: PageArtifact) -> list[ProviderBlock]:
            self.calls += 1
            if self.calls == 2:
                raise RecognitionError("OCR_FAILED", "synthetic second-page provider failure")
            return [
                ProviderBlock(
                    "question_number",
                    "1. synthetic provider output",
                    None,
                    0.95,
                    (0.08, 0.1, 0.84, 0.12),
                )
            ]

    actor, db = actor_and_db()
    storage = FakeStorage()
    provider = SecondPageFailureProvider()
    monkeypatch.setattr("app.api.recognition.provider_from_settings", lambda _settings: provider)
    try:
        assignment_id, version_id, original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        job = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "atomic-provider-failure"},
        ).json()

        assert provider.calls == 2
        assert job["status"] == "failed"
        assert (
            list(
                db.scalars(
                    select(RecognitionBlock).where(
                        RecognitionBlock.recognition_job_id == uuid.UUID(job["id"])
                    )
                ).all()
            )
            == []
        )
        assert (
            list(
                db.scalars(
                    select(QuestionCandidate).where(
                        QuestionCandidate.recognition_job_id == uuid.UUID(job["id"])
                    )
                ).all()
            )
            == []
        )
        assert set(storage.objects) == original_keys
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_atomic_batch_final_commit_failure_restores_old_rows_and_deletes_new_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment_id, version_id, _original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        created = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "atomic-commit-rollback"},
        ).json()
        assert created["status"] == "completed"
        job_id = uuid.UUID(created["id"])
        old_block_ids = set(
            db.scalars(
                select(RecognitionBlock.id).where(RecognitionBlock.recognition_job_id == job_id)
            ).all()
        )
        old_candidate_ids = set(
            db.scalars(
                select(QuestionCandidate.id).where(QuestionCandidate.recognition_job_id == job_id)
            ).all()
        )
        old_page_keys = {
            key
            for row in db.scalars(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job_id
                )
            ).all()
            for key in (
                row.rendered_storage_key,
                row.processed_storage_key,
                row.thumbnail_storage_key,
            )
            if key
        }
        old_object_keys = set(storage.objects)
        job_record = db.get(RecognitionJob, job_id)
        assert job_record is not None
        job_record.status = RecognitionStatus.queued
        db.commit()

        original_commit = db.commit
        commit_calls = 0

        def fail_publish_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise RuntimeError("synthetic final recognition commit failure")
            original_commit()

        db.commit = fail_publish_commit  # type: ignore[method-assign]
        try:
            run_recognition_job(db, storage, job_id)
        finally:
            db.commit = original_commit  # type: ignore[method-assign]

        assert (
            set(
                db.scalars(
                    select(RecognitionBlock.id).where(RecognitionBlock.recognition_job_id == job_id)
                ).all()
            )
            == old_block_ids
        )
        assert (
            set(
                db.scalars(
                    select(QuestionCandidate.id).where(
                        QuestionCandidate.recognition_job_id == job_id
                    )
                ).all()
            )
            == old_candidate_ids
        )
        current_page_keys = {
            key
            for row in db.scalars(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job_id
                )
            ).all()
            for key in (
                row.rendered_storage_key,
                row.processed_storage_key,
                row.thumbnail_storage_key,
            )
            if key
        }
        assert current_page_keys == old_page_keys
        assert set(storage.objects) == old_object_keys
        assert set(storage.delete_calls).isdisjoint(old_page_keys)
        assert db.get(RecognitionJob, job_id).status == RecognitionStatus.failed
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_old_artifact_query_failure_deletes_this_attempt_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment_id, version_id, original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        monkeypatch.setattr(recognition_api, "dispatch_recognition_job", lambda _db, _job: None)
        created = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs",
            json={"paper_version_id": version_id, "idempotency_key": "old-key-query-failure"},
        ).json()
        job_id = uuid.UUID(created["id"])
        original_scalars = db.scalars

        def fail_old_key_query(statement: object, *args: object, **kwargs: object) -> object:
            new_artifacts_exist = any(key.startswith("recognition/") for key in storage.objects)
            if new_artifacts_exist and "page_processing_results" in str(statement):
                raise RuntimeError("synthetic old artifact query failure")
            return original_scalars(statement, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(db, "scalars", fail_old_key_query)
        run_recognition_job(db, storage, job_id)
        monkeypatch.setattr(db, "scalars", original_scalars)

        assert set(storage.objects) == original_keys
        assert db.get(RecognitionJob, job_id).status == RecognitionStatus.failed
        assert (
            db.scalar(
                select(func.count())
                .select_from(PageProcessingResult)
                .where(PageProcessingResult.recognition_job_id == job_id)
            )
            == 0
        )
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_two_page_success_publishes_complete_batch() -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment_id, version_id, original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        job = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "atomic-two-page-success"},
        ).json()
        job_id = uuid.UUID(job["id"])

        assert job["status"] == "completed"
        assert job["page_summary"] == {"total": 2, "completed": 2, "failed": 0, "stale": 0}
        assert (
            len(
                db.scalars(
                    select(PageProcessingResult).where(
                        PageProcessingResult.recognition_job_id == job_id,
                        PageProcessingResult.status == PageRecognitionStatus.completed,
                    )
                ).all()
            )
            == 2
        )
        assert (
            len(
                db.scalars(
                    select(RecognitionBlock).where(RecognitionBlock.recognition_job_id == job_id)
                ).all()
            )
            == 2
        )
        assert (
            len(
                db.scalars(
                    select(QuestionCandidate).where(QuestionCandidate.recognition_job_id == job_id)
                ).all()
            )
            == 2
        )
        assert len(set(storage.objects) - original_keys) == 6
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_retry_page_queues_whole_batch_without_mutating_committed_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    monkeypatch.setattr("app.api.recognition.dispatch_recognition_job", lambda _db, _job: None)
    try:
        assignment_id, version_id, _original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        job = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "atomic-page-retry"},
        ).json()
        job_id = uuid.UUID(job["id"])
        page_results = list(
            db.scalars(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job_id
                )
            ).all()
        )
        old_keys = {
            key
            for row in page_results
            for key in (
                row.rendered_storage_key,
                row.processed_storage_key,
                row.thumbnail_storage_key,
            )
            if key
        }

        response = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}"
            f"/pages/{page_results[0].paper_page_id}/retry"
        )

        assert response.status_code == 200, response.text
        db.expire_all()
        assert db.get(RecognitionJob, job_id).status == RecognitionStatus.queued
        assert (
            client.get(
                f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/blocks"
            ).status_code
            == 409
        )
        assert (
            client.get(
                f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates"
            ).status_code
            == 409
        )
        assert (
            client.get(
                f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/pages"
            ).json()
            == []
        )
        current_results = list(
            db.scalars(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job_id
                )
            ).all()
        )
        assert all(row.status == PageRecognitionStatus.completed for row in current_results)
        assert {
            key
            for row in current_results
            for key in (
                row.rendered_storage_key,
                row.processed_storage_key,
                row.thumbnail_storage_key,
            )
            if key
        } == old_keys
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_stale_attempt_failure_does_not_overwrite_newer_running_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment_id, version_id, _original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        created = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "stale-attempt-owner"},
        ).json()
        job_id = uuid.UUID(created["id"])
        job = db.get(RecognitionJob, job_id)
        assert job is not None
        job.status = RecognitionStatus.queued
        job.completed_at = None
        db.commit()
        old_objects = set(storage.objects)
        old_block_ids = set(
            db.scalars(
                select(RecognitionBlock.id).where(RecognitionBlock.recognition_job_id == job_id)
            ).all()
        )

        def supersede_attempt(*_args: object, **_kwargs: object) -> object:
            current = db.get(RecognitionJob, job_id)
            assert current is not None
            current.attempt += 1
            current.status = RecognitionStatus.running
            db.commit()
            raise RecognitionError("OCR_FAILED", "synthetic stale attempt failure")

        monkeypatch.setattr("app.api.recognition._preflight_recognition_page", supersede_attempt)
        run_recognition_job(db, storage, job_id)

        db.expire_all()
        current = db.get(RecognitionJob, job_id)
        assert current is not None
        assert current.status == RecognitionStatus.running
        assert current.attempt == created["attempt"] + 2
        assert current.error_code is None
        assert set(storage.objects) == old_objects
        assert (
            set(
                db.scalars(
                    select(RecognitionBlock.id).where(RecognitionBlock.recognition_job_id == job_id)
                ).all()
            )
            == old_block_ids
        )
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_retry_rejects_teacher_edited_results_without_hiding_completed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    monkeypatch.setattr("app.api.recognition.dispatch_recognition_job", lambda _db, _job: None)
    try:
        assignment_id, version_id, _original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        created = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "protected-retry"},
        ).json()
        candidates_url = (
            f"/api/assignments/{assignment_id}/recognition/jobs/{created['id']}/candidates"
        )
        candidates = client.get(candidates_url).json()
        edited = client.patch(
            f"{candidates_url}/{candidates[0]['id']}", json={"content_text": "教师保留内容"}
        )
        assert edited.status_code == 200
        pages = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{created['id']}/pages"
        ).json()

        retry = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{created['id']}/retry"
        )
        page_retry = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{created['id']}"
            f"/pages/{pages[0]['paper_page_id']}/retry"
        )

        assert retry.status_code == 409
        assert retry.json()["code"] == "RECOGNITION_RETRY_REQUIRES_NEW_JOB"
        assert page_retry.status_code == 409
        assert page_retry.json()["code"] == "RECOGNITION_RETRY_REQUIRES_NEW_JOB"
        db.expire_all()
        assert (
            db.get(RecognitionJob, uuid.UUID(created["id"])).status == RecognitionStatus.completed
        )
        assert client.get(candidates_url).status_code == 200
        assert (
            client.get(
                f"/api/assignments/{assignment_id}/recognition/jobs/{created['id']}/blocks"
            ).status_code
            == 200
        )
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_worker_storage_failure_marks_claimed_queued_job_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers.tasks.ocr import run_recognition

    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment_id, version_id, _original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        created = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "worker-storage-failure"},
        ).json()
        job_id = uuid.UUID(created["id"])
        job = db.get(RecognitionJob, job_id)
        assert job is not None
        job.status = RecognitionStatus.queued
        job.completed_at = None
        db.commit()

        def fail_storage() -> object:
            raise RuntimeError("synthetic storage dependency failure")

        monkeypatch.setattr("workers.tasks.ocr.get_storage", fail_storage)
        with pytest.raises(RuntimeError, match="storage dependency"):
            run_recognition.run(str(job_id))

        db.expire_all()
        current = db.get(RecognitionJob, job_id)
        assert current is not None
        assert current.status == RecognitionStatus.failed
        assert current.attempt == created["attempt"] + 1
        assert current.error_code == "RECOGNITION_FAILED"
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)


def test_worker_storage_failure_does_not_overwrite_new_attempt_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers.tasks.ocr import run_recognition

    actor, db = actor_and_db()
    storage = FakeStorage()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assignment_id, version_id, _original_keys = _upload_two_page_recognition_fixture(
            db, actor, storage
        )
        created = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "worker-storage-takeover"},
        ).json()
        job_id = uuid.UUID(created["id"])
        job = db.get(RecognitionJob, job_id)
        assert job is not None
        job.status = RecognitionStatus.queued
        job.completed_at = None
        db.commit()

        def supersede_then_fail_storage() -> object:
            db.expire_all()
            newer = db.get(RecognitionJob, job_id)
            assert newer is not None
            newer.attempt += 1
            newer.status = RecognitionStatus.running
            db.commit()
            raise RuntimeError("synthetic stale worker storage failure")

        monkeypatch.setattr("workers.tasks.ocr.get_storage", supersede_then_fail_storage)
        with pytest.raises(RuntimeError, match="stale worker"):
            run_recognition.run(str(job_id))

        db.expire_all()
        current = db.get(RecognitionJob, job_id)
        assert current is not None
        assert current.status == RecognitionStatus.running
        assert current.attempt == created["attempt"] + 2
        assert current.error_code is None
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
