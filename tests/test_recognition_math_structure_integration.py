import copy
import io
import uuid
from dataclasses import replace
from typing import Any

import pytest
from app.api.recognition import run_recognition_job
from app.assignment_generation.service import create_job as create_generation_job
from app.main import app
from app.models import (
    Assignment,
    AssignmentPageAnalysis,
    AssignmentQuestionExtractionCandidate,
    AssignmentQuestionExtractionRegion,
    AssignmentSourceFileAnalysis,
    PageProcessingResult,
    PaperPage,
    PaperVersion,
    Question,
    QuestionCandidate,
    RecognitionBlock,
    RecognitionJob,
    StoredFile,
)
from app.recognition.math_structure import VERSION
from app.recognition.page_quality import PageQualityAssessment
from app.recognition.pipeline import PageArtifact, ProviderBlock
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from sqlalchemy import select
from test_assignments import FakeStorage, active_class, actor_and_db, create

client = TestClient(app)


class StaticProvider:
    name = "synthetic-structure"
    version = "1"
    is_demo = True

    def __init__(self, blocks: list[ProviderBlock]) -> None:
        self.blocks = blocks

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def recognize(self, page: PageArtifact) -> list[ProviderBlock]:
        del page
        return self.blocks


def _block(
    text: str,
    region: tuple[float, float, float, float],
    *,
    block_type: str = "text",
) -> ProviderBlock:
    return ProviderBlock(block_type, text, None, 0.95, region)


def _page_image_bytes() -> bytes:
    image = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(image)
    for row in range(12):
        y = 90 + row * 65
        draw.rectangle((70, y, 700, y + 8), fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _text_pdf_bytes(lines: list[str], *, pages: int = 1) -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(320, 240))
    for page_number in range(pages):
        for line_number, line in enumerate(lines):
            document.drawString(
                24,
                190 - line_number * 30,
                line.replace("{page}", str(page_number + 1)),
            )
        document.showPage()
    document.save()
    return output.getvalue()


def _good_quality(*_args: object, **_kwargs: object) -> PageQualityAssessment:
    return PageQualityAssessment("good", (), False, False)


def _review_quality(*_args: object, **_kwargs: object) -> PageQualityAssessment:
    return PageQualityAssessment("review_required", ("blur",), True, False)


def _run_job(
    db: Any,
    actor: Any,
    storage: FakeStorage,
    *,
    content: bytes,
    content_type: str,
    key: str,
) -> tuple[str, dict[str, Any], set[str]]:
    app.dependency_overrides[get_storage] = lambda: storage
    assignment = create(client, active_class(db, actor.id).id)
    assignment_id = assignment["id"]
    extension = ".pdf" if content_type == "application/pdf" else ".png"
    upload = client.post(
        f"/api/assignments/{assignment_id}/files",
        files={"file": (f"synthetic-{key}{extension}", content, content_type)},
    )
    assert upload.status_code == 201, upload.text
    original_keys = set(storage.objects)
    version_id = client.get(f"/api/assignments/{assignment_id}").json()["paper_version"]["id"]
    response = client.post(
        f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
        json={"paper_version_id": version_id, "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return assignment_id, response.json(), original_keys


def _page_payload(assignment_id: str, job_id: str) -> dict[str, Any]:
    response = client.get(f"/api/assignments/{assignment_id}/recognition/jobs/{job_id}/pages")
    assert response.status_code == 200, response.text
    return response.json()[0]


@pytest.mark.parametrize(
    ("blocks", "expected_code", "expected_indexes"),
    [
        (
            [
                _block(
                    "1. Synthetic question", (0.05, 0.03, 0.50, 0.05), block_type="question_number"
                ),
                _block("x² + y₁ = α ∈ B", (0.10, 0.18, 0.35, 0.06)),
            ],
            "FORMULA_REVIEW_REQUIRED",
            [1],
        ),
        (
            [
                _block(
                    "1. Synthetic question", (0.05, 0.03, 0.50, 0.05), block_type="question_number"
                ),
                _block(r"\frac{1}{2}+\sqrt{x}", (0.10, 0.18, 0.35, 0.06)),
            ],
            "FORMULA_REVIEW_REQUIRED",
            [1],
        ),
        (
            [
                _block(
                    "1. Synthetic question", (0.05, 0.03, 0.50, 0.05), block_type="question_number"
                ),
                _block("1+0", (0.10, 0.18, 0.05, 0.03)),
                _block("2+0", (0.24, 0.18, 0.05, 0.03)),
                _block("3+0", (0.10, 0.27, 0.05, 0.03)),
                _block("4+0", (0.24, 0.27, 0.05, 0.03)),
            ],
            "MATH_LAYOUT_REVIEW_REQUIRED",
            [1, 2, 3, 4],
        ),
        (
            [
                _block(
                    "1. Left column question heading",
                    (0.05, 0.05, 0.30, 0.05),
                    block_type="question_number",
                ),
                _block("Left column second sentence", (0.05, 0.27, 0.30, 0.05)),
                _block("Right column first sentence", (0.55, 0.08, 0.30, 0.05)),
                _block("Right column second sentence", (0.55, 0.30, 0.30, 0.05)),
            ],
            "READING_ORDER_CONFLICT",
            [0, 1, 2, 3],
        ),
    ],
)
def test_structure_signals_are_persisted_and_only_evidence_blocks_become_manual(
    monkeypatch: pytest.MonkeyPatch,
    blocks: list[ProviderBlock],
    expected_code: str,
    expected_indexes: list[int],
) -> None:
    from app.api import recognition as recognition_api

    monkeypatch.setattr(
        recognition_api, "provider_from_settings", lambda _settings: StaticProvider(blocks)
    )
    monkeypatch.setattr(recognition_api, "assess_page_quality", _good_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        assignment_id, job, _ = _run_job(
            db,
            actor,
            storage,
            content=_page_image_bytes(),
            content_type="image/png",
            key=f"structure-{expected_code.lower()}-{uuid.uuid4()}",
        )
        assert job["status"] == "completed"
        page = _page_payload(assignment_id, job["id"])
        public = page["math_structure"]
        assert "structure_risks" not in page
        assert public == page["processing_parameters"]["math_structure"]
        assert set(public) == {"version", "risk_codes", "evidence"}
        assert public["version"] == VERSION
        assert expected_code in public["risk_codes"]
        matching = public["evidence"][public["risk_codes"].index(expected_code)]
        assert matching["block_indexes"] == expected_indexes
        assert set(matching) == {"block_indexes", "region"}

        stored = db.scalar(
            select(PageProcessingResult).where(
                PageProcessingResult.recognition_job_id == uuid.UUID(job["id"])
            )
        )
        assert stored is not None
        private = stored.processing_parameters["math_structure"]
        assert private["version"] == VERSION
        assert private["risk_codes"] == public["risk_codes"]
        assert private["evidence"] == public["evidence"]
        serialized_evidence = repr(private["evidence"])
        assert all((block.text or "") not in serialized_evidence for block in blocks)

        persisted_blocks = list(
            db.scalars(
                select(RecognitionBlock)
                .where(RecognitionBlock.recognition_job_id == uuid.UUID(job["id"]))
                .order_by(RecognitionBlock.display_order)
            ).all()
        )
        assert [item.text for item in persisted_blocks] == [item.text for item in blocks]
        for index, item in enumerate(persisted_blocks):
            assert item.status == ("manual_required" if index in expected_indexes else "adopted")
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_plain_stems_and_table_or_geometry_labels_do_not_create_structure_risks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    blocks = [
        _block("1. 说明下列说法是否正确", (0.05, 0.05, 0.42, 0.05), block_type="question_number"),
        _block("Compute the value and explain your reasoning", (0.05, 0.14, 0.42, 0.05)),
        _block("Name", (0.08, 0.30, 0.12, 0.04)),
        _block("Score", (0.28, 0.30, 0.12, 0.04)),
        _block("Alice", (0.08, 0.39, 0.12, 0.04)),
        _block("Ten", (0.28, 0.39, 0.12, 0.04)),
        _block("A", (0.60, 0.30, 0.03, 0.03)),
        _block("B", (0.78, 0.30, 0.03, 0.03)),
        _block("C", (0.68, 0.48, 0.03, 0.03)),
        _block("D", (0.84, 0.48, 0.03, 0.03)),
    ]
    monkeypatch.setattr(
        recognition_api, "provider_from_settings", lambda _settings: StaticProvider(blocks)
    )
    monkeypatch.setattr(recognition_api, "assess_page_quality", _good_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        assignment_id, job, _ = _run_job(
            db,
            actor,
            storage,
            content=_page_image_bytes(),
            content_type="image/png",
            key=f"structure-negative-{uuid.uuid4()}",
        )
        page = _page_payload(assignment_id, job["id"])
        assert page["math_structure"] == {
            "version": VERSION,
            "risk_codes": [],
            "evidence": [],
        }
        response_blocks = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/blocks"
        ).json()
        assert all(item["status"] == "adopted" for item in response_blocks)
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_quality_manual_status_is_not_a_math_source_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    ordinary = [
        _block(
            "1. Synthetic ordinary question with enough text",
            (0.08, 0.12, 0.70, 0.08),
            block_type="question_number",
        )
    ]
    monkeypatch.setattr(
        recognition_api, "provider_from_settings", lambda _settings: StaticProvider(ordinary)
    )
    monkeypatch.setattr(recognition_api, "assess_page_quality", _review_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        assignment_id, job, _ = _run_job(
            db,
            actor,
            storage,
            content=_page_image_bytes(),
            content_type="image/png",
            key=f"quality-not-math-conflict-{uuid.uuid4()}",
        )
        page = _page_payload(assignment_id, job["id"])
        assert page["quality"]["level"] == "review_required"
        assert page["math_structure"]["risk_codes"] == []
        assert page["processing_parameters"]["source_conflict_count"] == 0
        assert page["processing_parameters"]["math_symbol_conflict_count"] == 0
        blocks = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/blocks"
        ).json()
        assert [item["status"] for item in blocks] == ["manual_required"]
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_real_math_source_conflict_is_preserved_with_structure_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    pdf_block = _block(
        "1. Synthetic formula x² + α with enough reliable embedded text",
        (0.08, 0.12, 0.75, 0.08),
        block_type="question_number",
    )
    pdf_block = replace(pdf_block, source="pdf_text:synthetic")
    ocr_block = replace(
        _block(
            "1. Synthetic formula x2 + a with enough reliable embedded text",
            (0.08, 0.12, 0.75, 0.08),
            block_type="question_number",
        ),
        source="rapidocr:synthetic",
    )
    conflict_provider = StaticProvider([ocr_block])
    conflict_provider.name = "rapidocr"
    monkeypatch.setattr(
        recognition_api, "provider_from_settings", lambda _settings: conflict_provider
    )
    monkeypatch.setattr(
        recognition_api, "extract_pdf_text_layer", lambda _content, _page: [pdf_block]
    )
    monkeypatch.setattr(recognition_api, "assess_page_quality", _good_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        assignment_id, job, _ = _run_job(
            db,
            actor,
            storage,
            content=_text_pdf_bytes(["1. Synthetic source conflict with enough text"]),
            content_type="application/pdf",
            key=f"real-math-conflict-{uuid.uuid4()}",
        )
        page = _page_payload(assignment_id, job["id"])
        assert page["math_structure"]["risk_codes"] == ["FORMULA_REVIEW_REQUIRED"]
        assert page["processing_parameters"]["source_conflict_count"] == 1
        assert page["processing_parameters"]["math_symbol_conflict_count"] == 1
        blocks = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/blocks"
        ).json()
        assert sorted(item["status"] for item in blocks) == ["manual_required", "source_conflict"]
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_reliable_pdf_text_does_not_remove_formula_structure_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    monkeypatch.setattr(recognition_api, "assess_page_quality", _good_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        assignment_id, job, _ = _run_job(
            db,
            actor,
            storage,
            content=_text_pdf_bytes(
                [r"1. Synthetic reliable PDF formula \frac{1}{2} with sufficient text"]
            ),
            content_type="application/pdf",
            key=f"reliable-pdf-structure-{uuid.uuid4()}",
        )
        assert job["status"] == "completed"
        page = _page_payload(assignment_id, job["id"])
        assert page["processing_parameters"]["text_layer_sufficient"] is True
        assert "FORMULA_REVIEW_REQUIRED" in page["math_structure"]["risk_codes"]
        blocks = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/blocks"
        ).json()
        assert blocks[0]["source"].startswith("pdf_text:")
        assert blocks[0]["status"] == "manual_required"
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_reading_order_risk_requires_edit_before_direct_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    blocks = [
        _block(
            "1. Left column question heading",
            (0.05, 0.05, 0.30, 0.05),
            block_type="question_number",
        ),
        _block("Left column second sentence", (0.05, 0.27, 0.30, 0.05)),
        _block("Right column first sentence", (0.55, 0.08, 0.30, 0.05)),
        _block("Right column second sentence", (0.55, 0.30, 0.30, 0.05)),
    ]
    monkeypatch.setattr(
        recognition_api, "provider_from_settings", lambda _settings: StaticProvider(blocks)
    )
    monkeypatch.setattr(recognition_api, "assess_page_quality", _good_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        assignment_id, job, _ = _run_job(
            db,
            actor,
            storage,
            content=_page_image_bytes(),
            content_type="image/png",
            key=f"reading-confirm-{uuid.uuid4()}",
        )
        candidates = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates"
        ).json()
        assert len(candidates) == 1
        candidate = candidates[0]
        denied = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidate["id"]]},
        )
        assert denied.status_code == 409
        assert denied.json()["code"] == "READING_ORDER_CONFLICT"

        status_only = client.patch(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates/{candidate['id']}",
            json={"status": "edited"},
        )
        assert status_only.status_code == 200, status_only.text
        still_denied = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidate["id"]]},
        )
        assert still_denied.status_code == 409
        assert still_denied.json()["code"] == "READING_ORDER_CONFLICT"

        number_only = client.patch(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates/{candidate['id']}",
            json={"temporary_number": "1（教师核号）"},
        )
        assert number_only.status_code == 200, number_only.text
        still_denied = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidate["id"]]},
        )
        assert still_denied.status_code == 409
        assert still_denied.json()["code"] == "READING_ORDER_CONFLICT"

        no_op_content = client.patch(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates/{candidate['id']}",
            json={"content_text": candidate["content_text"]},
        )
        assert no_op_content.status_code == 200, no_op_content.text
        still_denied = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidate["id"]]},
        )
        assert still_denied.status_code == 409
        assert still_denied.json()["code"] == "READING_ORDER_CONFLICT"

        edited = client.patch(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates/{candidate['id']}",
            json={"content_text": "1. Teacher verified the complete multi-column question"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "edited"
        confirmed = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [candidate["id"]]},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["created_question_ids"]
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_same_page_candidate_outside_reading_evidence_can_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    blocks = [
        _block(
            "1. Left column question heading",
            (0.05, 0.05, 0.30, 0.05),
            block_type="question_number",
        ),
        _block("Left column second sentence", (0.05, 0.24, 0.30, 0.05)),
        _block("Right column first sentence", (0.55, 0.08, 0.30, 0.05)),
        _block("Right column second sentence", (0.55, 0.27, 0.30, 0.05)),
        _block(
            "2. Independent lower question with complete ordinary text",
            (0.05, 0.66, 0.75, 0.06),
            block_type="question_number",
        ),
    ]
    monkeypatch.setattr(
        recognition_api, "provider_from_settings", lambda _settings: StaticProvider(blocks)
    )
    monkeypatch.setattr(recognition_api, "assess_page_quality", _good_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        assignment_id, job, _ = _run_job(
            db,
            actor,
            storage,
            content=_page_image_bytes(),
            content_type="image/png",
            key=f"reading-unrelated-region-{uuid.uuid4()}",
        )
        page = _page_payload(assignment_id, job["id"])
        assert page["math_structure"]["risk_codes"] == ["READING_ORDER_CONFLICT"]
        evidence_region = page["math_structure"]["evidence"][0]["region"]
        assert evidence_region[1] + evidence_region[3] < 0.66

        candidates = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/candidates"
        ).json()
        assert [candidate["temporary_number"] for candidate in candidates] == ["1", "2"]
        lower = candidates[1]
        confirmed = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/confirm",
            json={"candidate_ids": [lower["id"]]},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert len(confirmed.json()["created_question_ids"]) == 1
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_bulk_accept_rechecks_dynamic_reading_risk_and_skips_otherwise_eligible_candidate() -> None:
    actor, db = actor_and_db()
    assignment_payload = create(client, active_class(db, actor.id).id)
    assignment = db.get(Assignment, uuid.UUID(assignment_payload["id"]))
    assert assignment is not None
    stored = StoredFile(
        owner_id=actor.id,
        storage_key=f"tests/{uuid.uuid4()}.pdf",
        original_name="synthetic-reading-risk.pdf",
        content_type="application/pdf",
        size=10,
        checksum="d" * 64,
        status="ready",
    )
    paper = PaperVersion(
        assignment_id=assignment.id,
        version=1,
        status="draft",
        source_type="manual",
        created_by=actor.id,
    )
    db.add_all([stored, paper])
    db.flush()
    page = PaperPage(
        paper_version_id=paper.id,
        stored_file_id=stored.id,
        page_number=1,
        source_page_number=1,
        status="ready",
    )
    db.add(page)
    assignment.active_paper_version_id = paper.id
    db.flush()
    job, revision, _ = create_generation_job(
        db,
        actor.id,
        assignment.id,
        f"bulk-reading-risk-{uuid.uuid4()}",
        "unavailable",
        None,
    )
    source = AssignmentSourceFileAnalysis(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        stored_file_id=stored.id,
        source_snapshot_hash=job.source_snapshot_hash,
        detected_mime_type="application/pdf",
        checksum=stored.checksum,
        page_count=1,
        content_mode="text",
        text_source="pdf_text",
        content_mode_confidence=1,
        suggested_role="question_paper",
        role_confidence=1,
        suggested_answer_source="not_applicable",
        answer_source_confidence=1,
        analysis_status="confirmed",
        teacher_confirmed_role="question_paper",
    )
    db.add(source)
    db.flush()
    candidate = AssignmentQuestionExtractionCandidate(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        paper_version_id=paper.id,
        candidate_version=1,
        question_number="1",
        question_type="calculation",
        content_text="Synthetic otherwise eligible question",
        max_score=5,
        field_confidences={},
        overall_confidence=0.95,
        extraction_method="pdf_text_anchor",
        evidence={},
        warning_codes=[],
        status="suggested",
        manual_required=False,
        source_snapshot_hash=job.source_snapshot_hash,
    )
    db.add(candidate)
    db.flush()
    db.add_all(
        [
            AssignmentQuestionExtractionRegion(
                candidate_id=candidate.id,
                paper_page_id=page.id,
                display_order=0,
                region_type="stem",
                x=0.05,
                y=0.05,
                width=0.35,
                height=0.30,
                confidence=0.95,
            ),
            AssignmentPageAnalysis(
                owner_id=actor.id,
                assignment_id=assignment.id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                paper_page_id=page.id,
                source_file_analysis_id=source.id,
                source_snapshot_hash=job.source_snapshot_hash,
                status="ready",
                content_mode="text",
                text_source="pdf_text",
                content_mode_confidence=1,
                text_character_count=100,
                quality_score=1,
                metrics={
                    "page_quality": {"level": "good", "issues": []},
                    "math_structure": {
                        "version": VERSION,
                        "risk_codes": ["READING_ORDER_CONFLICT"],
                        "evidence": [{"block_indexes": [0, 1, 2, 3], "region": [0, 0, 0.85, 0.40]}],
                    },
                },
            ),
        ]
    )
    db.commit()

    response = client.post(
        f"/api/assignment-draft-revisions/{revision.id}"
        "/question-extraction-candidates/accept-eligible",
        json={
            "expected_draft_revision_edit_version": revision.teacher_edit_version,
            "expected_paper_version_id": str(paper.id),
            "expected_source_snapshot": job.source_snapshot_hash,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "accepted_candidate_ids": [],
        "accepted_count": 0,
        "server_decided": True,
    }
    db.expire_all()
    candidate = db.get(AssignmentQuestionExtractionCandidate, candidate.id)
    assert candidate is not None
    assert candidate.status == "suggested"
    assert candidate.materialized_question_id is None
    assert db.scalar(select(Question.id).where(Question.paper_version_id == paper.id)) is None


def test_detector_failure_on_second_page_publishes_no_partial_rows_or_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    original_detect = recognition_api.detect_math_structure_risks
    calls = 0

    def fail_second_detection(blocks: list[ProviderBlock]) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic second-page structure detector failure")
        return original_detect(blocks)

    monkeypatch.setattr(recognition_api, "detect_math_structure_risks", fail_second_detection)
    monkeypatch.setattr(recognition_api, "assess_page_quality", _good_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        _assignment_id, job, original_keys = _run_job(
            db,
            actor,
            storage,
            content=_text_pdf_bytes(
                [r"{page}. Synthetic reliable formula \frac{1}{2} with sufficient text"], pages=2
            ),
            content_type="application/pdf",
            key=f"structure-second-page-failure-{uuid.uuid4()}",
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
        app.dependency_overrides.pop(get_storage, None)


def test_structure_evidence_rolls_back_and_new_artifacts_are_cleaned_on_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import recognition as recognition_api

    blocks = [
        _block(
            "1. Synthetic formula question", (0.05, 0.05, 0.50, 0.05), block_type="question_number"
        ),
        _block("x² + α", (0.10, 0.18, 0.25, 0.05)),
    ]
    monkeypatch.setattr(
        recognition_api, "provider_from_settings", lambda _settings: StaticProvider(blocks)
    )
    monkeypatch.setattr(recognition_api, "assess_page_quality", _good_quality)
    actor, db = actor_and_db()
    storage = FakeStorage()
    try:
        _assignment_id, created, _ = _run_job(
            db,
            actor,
            storage,
            content=_page_image_bytes(),
            content_type="image/png",
            key=f"structure-commit-rollback-{uuid.uuid4()}",
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
        old_structure = {
            row.paper_page_id: copy.deepcopy(row.processing_parameters["math_structure"])
            for row in old_results
        }
        old_block_ids = set(
            db.scalars(
                select(RecognitionBlock.id).where(RecognitionBlock.recognition_job_id == job_id)
            ).all()
        )
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
        job.status = "queued"
        job.completed_at = None
        db.commit()

        original_commit = db.commit
        commit_calls = 0

        def fail_final_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise RuntimeError("synthetic final structure commit failure")
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
            row.paper_page_id: row.processing_parameters["math_structure"]
            for row in current_results
        } == old_structure
        assert (
            set(
                db.scalars(
                    select(RecognitionBlock.id).where(RecognitionBlock.recognition_job_id == job_id)
                ).all()
            )
            == old_block_ids
        )
        assert set(storage.objects) == old_object_keys
        assert set(storage.delete_calls).isdisjoint(old_artifact_keys)
        assert db.get(RecognitionJob, job_id).status == "failed"
    finally:
        app.dependency_overrides.pop(get_storage, None)
