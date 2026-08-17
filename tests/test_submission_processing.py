import io
import uuid
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.main import app
from app.models import (
    Assignment,
    Question,
    QuestionStatus,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionProcessingJob,
    SubmissionQuestionAnchor,
    now_utc,
)
from app.recognition.pipeline import PageArtifact, ProviderBlock
from app.recognition.submission_processing import (
    _anchor_region_candidate,
    _hash_distance,
    _normalize_question_number,
    _pdf_text_blocks,
    _segment,
    preprocess_page,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageOps
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from test_submission_workflow import workflow

client = TestClient(app)


def test_anchor_regions_follow_content_without_overlap_or_page_tail() -> None:
    first_anchor = ProviderBlock("pdf_text", "第 1 题", None, 0.99, (0.08, 0.20, 0.08, 0.025))
    first_answer = ProviderBlock("pdf_text", "answer one", None, 0.99, (0.08, 0.25, 0.40, 0.03))
    second_anchor = ProviderBlock("pdf_text", "第 2 题", None, 0.99, (0.08, 0.50, 0.08, 0.025))
    second_answer = ProviderBlock("pdf_text", "answer two", None, 0.99, (0.08, 0.55, 0.40, 0.04))
    footer = ProviderBlock("pdf_text", "第 1 页", None, 0.99, (0.85, 0.96, 0.08, 0.02))
    blocks = [first_anchor, first_answer, second_anchor, second_answer, footer]

    first = _anchor_region_candidate(first_anchor, second_anchor, blocks, Decimal("0.15"))
    second = _anchor_region_candidate(second_anchor, None, blocks, Decimal("0.15"))

    assert first == (
        Decimal("0.06"),
        Decimal("0.19"),
        Decimal("0.92"),
        Decimal("0.30"),
    )
    assert second == (
        Decimal("0.06"),
        Decimal("0.49"),
        Decimal("0.92"),
        Decimal("0.115"),
    )
    assert first[1] + first[3] <= second[1]
    assert second[1] + second[3] < Decimal("0.70")


def test_heading_only_pdf_keeps_answer_space_for_final_question() -> None:
    first_anchor = ProviderBlock("pdf_text", "第 1 题", None, 0.99, (0.08, 0.20, 0.08, 0.025))
    second_anchor = ProviderBlock("pdf_text", "第 2 题", None, 0.99, (0.08, 0.50, 0.08, 0.025))
    footer = ProviderBlock("pdf_text", "第 1 页", None, 0.99, (0.85, 0.96, 0.08, 0.02))
    blocks = [first_anchor, second_anchor, footer]

    first = _anchor_region_candidate(first_anchor, second_anchor, blocks, Decimal("0.15"))
    second = _anchor_region_candidate(second_anchor, None, blocks, Decimal("0.15"))

    assert first[1] + first[3] == second[1]
    assert second[3] == Decimal("0.15")
    assert second[1] + second[3] < Decimal("0.92")


def artifact(background: str = "white", text: str | None = None) -> PageArtifact:
    image = Image.new("RGB", (600, 800), background)
    if text:
        ImageDraw.Draw(image).text((40, 50), text, fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    return PageArtifact(output.getvalue(), image.width, image.height)


def sparse_ruled_artifact(ink: int) -> PageArtifact:
    image = Image.new("L", (1000, 1000), 255)
    draw = ImageDraw.Draw(image)
    for y in range(100, 700, 10):
        draw.line((80, y, 280, y), fill=ink, width=3)
    output = io.BytesIO()
    image.convert("RGB").save(output, "PNG")
    return PageArtifact(output.getvalue(), image.width, image.height)


def test_quality_metrics_detect_blank_and_duplicate_deterministically() -> None:
    _, blank = preprocess_page(artifact())
    _, same = preprocess_page(artifact())
    _, content = preprocess_page(artifact(text="1. synthetic answer"))
    assert Decimal(str(blank["blank_probability"])) >= Decimal("0.95")
    assert _hash_distance(str(blank["perceptual_hash"]), str(same["perceptual_hash"])) == 0
    assert str(content["perceptual_hash"]) != str(blank["perceptual_hash"])


def test_sparse_white_page_uses_content_brightness_without_false_positive() -> None:
    _, metrics = preprocess_page(sparse_ruled_artifact(0))

    assert float(metrics["source_brightness"]) > 245
    assert Decimal(str(metrics["blank_probability"])) < Decimal("0.95")
    assert float(metrics["source_content_brightness"]) < 210
    assert metrics["brightness_correction_applied"] is False
    assert "TOO_BRIGHT" not in metrics["warnings"]


def test_washed_out_content_is_corrected_and_rechecked() -> None:
    processed, metrics = preprocess_page(sparse_ruled_artifact(235))
    image = Image.open(io.BytesIO(processed.content)).convert("L")

    assert float(metrics["source_brightness"]) > 245
    assert float(metrics["source_content_brightness"]) > 210
    assert metrics["brightness_correction_applied"] is True
    assert float(metrics["processed_content_brightness"]) < 210
    assert image.getextrema()[0] < 50
    assert "TOO_BRIGHT" not in metrics["warnings"]


def test_white_border_crop_preserves_processed_coordinate_space() -> None:
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).rectangle((40, 60, 560, 740), outline="black", width=3)
    output = io.BytesIO()
    image.save(output, "PNG")

    processed, metrics = preprocess_page(PageArtifact(output.getvalue(), 600, 800))
    processed_image = Image.open(io.BytesIO(processed.content)).convert("L")
    source_bbox = (
        ImageOps.invert(image.convert("L")).point(lambda value: 255 if value > 12 else 0).getbbox()
    )
    processed_bbox = (
        ImageOps.invert(processed_image).point(lambda value: 255 if value > 12 else 0).getbbox()
    )

    assert metrics["crop"] is not None
    assert (processed.width, processed.height) == image.size
    assert source_bbox is not None and processed_bbox is not None
    assert all(
        abs(left - right) <= 2 for left, right in zip(source_bbox, processed_bbox, strict=True)
    )


def test_question_anchor_formats_are_normalized_without_unknown_creation() -> None:
    variants = [
        "1",
        "1.",
        "1\u3001",
        "\u7b2c1\u9898",
        "1a",
        "1(a)",
        "\uff081\uff09",
        "(1)",
        "2.1",
        "Q1:",
        "2(3)",
        "12（2）：",
        "第 2(5) 题",
    ]
    assert [_normalize_question_number(value) for value in variants] == [
        "1",
        "1",
        "1",
        "1",
        "1a",
        "1a",
        "1",
        "1",
        "2.1",
        "1",
        "2(3)",
        "12(2)",
        "2(5)",
    ]
    assert _normalize_question_number("\u7b54\u6848\u5f15\u7528\u7b2c 9 \u9898") is None
    assert _normalize_question_number("2026 academic year") is None


def test_numeric_subquestions_bind_to_stable_question_ids_without_page_identity() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    try:
        submission = db.get(Submission, submission_id)
        first = db.get(Question, uuid.UUID(question_id))
        assert submission is not None and first is not None
        first.question_number = "02(03)"
        first.display_order = 0
        expected = {"2(3)": first.id}
        for display_order, number in enumerate(["2(5)", "12(1)", "12(2)"], start=1):
            question = Question(
                paper_version_id=first.paper_version_id,
                question_number=number,
                display_order=display_order,
                question_type="calculation",
                max_score=10,
                status=QuestionStatus.active,
                source="manual",
            )
            db.add(question)
            db.flush()
            expected[number] = question.id
        page = db.scalar(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission.id)
            .order_by(SubmissionPage.page_number)
        )
        assert page is not None
        job = SubmissionProcessingJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            idempotency_key=f"hierarchical-anchors-{uuid.uuid4()}",
            status="running",
        )
        db.add(job)
        db.flush()
        labels = ["2（3）：", "第 2(5) 题", "12(1).", "12（2）"]
        blocks = [
            ProviderBlock(
                "pdf_text",
                label,
                None,
                0.99,
                (0.08, 0.10 + index * 0.18, 0.12, 0.025),
            )
            for index, label in enumerate(labels)
        ]

        _segment(db, job, submission, [page], {page.id: blocks})
        db.commit()

        anchors = list(
            db.scalars(
                select(SubmissionQuestionAnchor)
                .where(SubmissionQuestionAnchor.submission_processing_job_id == job.id)
                .order_by(SubmissionQuestionAnchor.y)
            )
        )
        assert [anchor.normalized_number for anchor in anchors] == list(expected)
        assert {
            anchor.normalized_number: anchor.candidate_question_id for anchor in anchors
        } == expected
        regions = list(
            db.execute(
                select(StudentAnswerRegion, StudentAnswer)
                .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
                .where(StudentAnswer.submission_id == submission.id)
            ).all()
        )
        assert len(regions) == 4
        assert {answer.question_id for _region, answer in regions} == set(expected.values())
        assert {region.reason for region, _answer in regions} == {"QUESTION_ANCHOR"}
        assert {region.submission_page_id for region, _answer in regions} == {page.id}
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_pdf_text_layer_exposes_normalized_question_anchor_coordinates() -> None:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(600, 800))
    document.drawString(60, 680, "Q1: answer")
    document.drawString(60, 520, "Q2: answer")
    document.save()

    blocks = _pdf_text_blocks(output.getvalue(), 1)

    assert [_normalize_question_number(block.text or "") for block in blocks] == ["1", "2"]
    assert all(block.block_type == "pdf_text" for block in blocks)
    assert all(block.confidence == 0.99 for block in blocks)
    assert blocks[0].region[1] < blocks[1].region[1]


def test_processing_job_is_idempotent_and_region_bounds_are_enforced() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        first = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "processing-idempotent-1"},
        )
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "completed"
        again = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "processing-idempotent-1"},
        )
        assert again.json()["id"] == first.json()["id"]
        assert (
            len(
                db.scalars(
                    select(SubmissionProcessingJob).where(
                        SubmissionProcessingJob.submission_id == submission_id
                    )
                ).all()
            )
            == 1
        )
        pages = db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        ).all()
        assert len(pages) == 3
        initial_rotation = pages[0].rotation
        job = db.get(SubmissionProcessingJob, uuid.UUID(first.json()["id"]))
        assert job is not None
        job.config_version = "submission-processing-v1"
        db.commit()
        retried = client.post(
            f"/api/submissions/{submission_id}/processing-jobs/{job.id}/pages/{pages[0].id}/retry?run_now=true"
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["config_version"] == "submission-processing-v3"
        db.expire_all()
        retried_page = db.get(SubmissionPage, pages[0].id)
        assert retried_page is not None
        assert retried_page.preprocessing_version == "submission-processing-v3"
        answer = db.scalar(
            select(StudentAnswer).where(
                StudentAnswer.submission_id == submission_id,
                StudentAnswer.question_id == uuid.UUID(question_id),
            )
        )
        assert answer is not None
        invalid = client.post(
            f"/api/submissions/{submission_id}/region-candidates",
            json={
                "question_id": question_id,
                "submission_page_id": str(pages[0].id),
                "x": 0.8,
                "y": 0.1,
                "width": 0.3,
                "height": 0.2,
                "source": "manual",
                "status": "confirmed",
            },
        )
        assert invalid.status_code == 422
        rotated = client.post(
            f"/api/submissions/{submission_id}/processing-pages/{pages[0].id}/rotate?run_now=true",
            json={"degrees": 90},
        )
        assert rotated.status_code == 200, rotated.text
        db.expire_all()
        rotated_page = db.get(SubmissionPage, pages[0].id)
        assert rotated_page is not None
        assert rotated_page.rotation == (initial_rotation + 90) % 360

        incomplete = client.get(f"/api/submissions/{submission_id}/segmentation-incomplete")
        assert incomplete.status_code == 200, incomplete.text
        question = db.get(Question, uuid.UUID(question_id))
        assert question is not None
        assert incomplete.json()["questions"] == [
            {
                "id": question_id,
                "question_number": question.question_number,
                "display_order": question.display_order,
            }
        ]
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


@pytest.mark.parametrize(
    ("terminal_status", "has_finalized_at"),
    [
        ("finalized", True),
        ("merged", False),
        ("voided", False),
        ("recognized", True),
    ],
    ids=["finalized", "merged", "voided", "finalized-at"],
)
def test_terminal_submission_rejects_all_processing_mutations_without_writes(
    terminal_status: str,
    has_finalized_at: bool,
) -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        processed = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": f"terminal-prime-{terminal_status}-{has_finalized_at}"},
        )
        assert processed.status_code == 201, processed.text
        submission = db.get(Submission, submission_id)
        page = db.scalar(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        )
        assert submission is not None and page is not None
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        if answer is None:
            answer = StudentAnswer(
                submission_id=submission_id,
                question_id=uuid.UUID(question_id),
                question_version_reference="terminal-editable-guard",
            )
            db.add(answer)
            db.flush()
        region = db.scalar(
            select(StudentAnswerRegion).where(StudentAnswerRegion.student_answer_id == answer.id)
        )
        if region is None:
            region = StudentAnswerRegion(
                student_answer_id=answer.id,
                submission_page_id=page.id,
                x=Decimal("0"),
                y=Decimal("0"),
                width=Decimal("1"),
                height=Decimal("1"),
                source="manual",
                confidence=Decimal("0.95"),
                status="candidate",
            )
            db.add(region)
            db.commit()
        job = db.get(SubmissionProcessingJob, uuid.UUID(processed.json()["id"]))
        assert region is not None and job is not None
        submission.status = terminal_status
        submission.finalized_at = now_utc() if has_finalized_at else None
        db.commit()

        job_count = db.scalar(
            select(func.count())
            .select_from(SubmissionProcessingJob)
            .where(SubmissionProcessingJob.submission_id == submission_id)
        )
        region_count = db.scalar(
            select(func.count())
            .select_from(StudentAnswerRegion)
            .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
            .where(StudentAnswer.submission_id == submission_id)
        )
        job_state = (job.status, job.stage, job.attempt)
        page_state = (page.rotation, page.processing_status, page.page_version)
        region_state = (region.status, region.region_version)
        region_payload = {
            "question_id": str(answer.question_id),
            "submission_page_id": str(page.id),
            "x": float(region.x),
            "y": float(region.y),
            "width": float(region.width),
            "height": float(region.height),
            "source": "manual",
            "status": "confirmed",
        }
        responses = [
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs",
                json={"idempotency_key": f"terminal-new-{terminal_status}-{has_finalized_at}"},
            ),
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs/{job.id}/pages/{page.id}/retry"
            ),
            client.post(
                f"/api/submissions/{submission_id}/processing-pages/{page.id}/rotate",
                json={"degrees": 90},
            ),
            client.post(
                f"/api/submissions/{submission_id}/region-candidates",
                json=region_payload,
            ),
            client.put(
                f"/api/submissions/{submission_id}/region-candidates/{region.id}",
                json=region_payload,
            ),
            client.delete(f"/api/submissions/{submission_id}/region-candidates/{region.id}"),
            client.post(
                f"/api/submissions/{submission_id}/region-candidates/confirm-high-confidence"
            ),
        ]
        for response in responses:
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "FINALIZED_SUBMISSION_IMMUTABLE"
            assert response.json()["details"]["status"] == terminal_status

        db.expire_all()
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubmissionProcessingJob)
                .where(SubmissionProcessingJob.submission_id == submission_id)
            )
            == job_count
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(StudentAnswerRegion)
                .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
                .where(StudentAnswer.submission_id == submission_id)
            )
            == region_count
        )
        current_job = db.get(SubmissionProcessingJob, job.id)
        current_page = db.get(SubmissionPage, page.id)
        current_region = db.get(StudentAnswerRegion, region.id)
        assert (
            current_job is not None
            and (
                current_job.status,
                current_job.stage,
                current_job.attempt,
            )
            == job_state
        )
        assert (
            current_page is not None
            and (
                current_page.rotation,
                current_page.processing_status,
                current_page.page_version,
            )
            == page_state
        )
        assert (
            current_region is not None
            and (
                current_region.status,
                current_region.region_version,
            )
            == region_state
        )
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_low_confidence_anchor_is_below_auto_threshold() -> None:
    block = ProviderBlock("text", "1.", None, 0.55, (0.05, 0.1, 0.1, 0.03))
    assert block.confidence is not None and block.confidence < 0.8


def test_manual_region_create_replaces_current_region_for_the_same_answer() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    try:
        submission = db.get(Submission, submission_id)
        page = db.scalar(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        )
        assert submission is not None and page is not None
        assignment = db.get(Assignment, submission.assignment_id)
        assert assignment is not None and assignment.active_paper_version_id is not None
        answer = StudentAnswer(
            submission_id=submission.id,
            question_id=uuid.UUID(question_id),
            question_version_reference=str(assignment.active_paper_version_id),
        )
        db.add(answer)
        db.flush()
        old_region = StudentAnswerRegion(
            student_answer_id=answer.id,
            submission_page_id=page.id,
            x=Decimal("0.10"),
            y=Decimal("0.20"),
            width=Decimal("0.30"),
            height=Decimal("0.40"),
            source="ocr",
            status="confirmed",
            reason="QUESTION_ANCHOR",
            confirmation_origin="system_auto",
        )
        db.add(old_region)
        db.commit()

        response = client.post(
            f"/api/submissions/{submission_id}/region-candidates",
            json={
                "question_id": question_id,
                "submission_page_id": str(page.id),
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
                "source": "manual",
                "status": "confirmed",
                "reason": "TEACHER_DRAWN",
            },
        )

        assert response.status_code == 201, response.text
        db.expire_all()
        persisted_old = db.get(StudentAnswerRegion, old_region.id)
        current = db.scalars(
            select(StudentAnswerRegion).where(
                StudentAnswerRegion.student_answer_id == answer.id,
                StudentAnswerRegion.status.in_(["candidate", "confirmed", "manual_required"]),
            )
        ).all()
        assert persisted_old is not None
        assert persisted_old.status == "superseded" and persisted_old.region_version == 2
        assert len(current) == 1
        assert str(current[0].id) == response.json()["id"]
        assert current[0].confirmation_origin == "teacher_explicit"
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_complete_unique_anchor_sequence_ignores_removed_questions() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None
        assignment = db.get(Assignment, submission.assignment_id)
        assert assignment is not None and assignment.active_paper_version_id is not None
        questions = [db.get(Question, uuid.UUID(question_id))]
        questions.extend(
            Question(
                paper_version_id=assignment.active_paper_version_id,
                question_number=str(number),
                display_order=number - 1,
                question_type="calculation",
                max_score=10,
                status=QuestionStatus.active,
                source="manual",
            )
            for number in range(2, 6)
        )
        removed = Question(
            paper_version_id=assignment.active_paper_version_id,
            question_number="3",
            display_order=99,
            question_type="other",
            max_score=0,
            status=QuestionStatus.removed,
            source="manual",
        )
        db.add_all([*questions[1:], removed])
        page = db.scalar(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission.id)
            .order_by(SubmissionPage.page_number)
        )
        assert page is not None
        page.page_version = 4
        job = SubmissionProcessingJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            idempotency_key=f"complete-anchors-{uuid.uuid4()}",
            status="running",
        )
        db.add(job)
        db.flush()
        blocks = [
            ProviderBlock(
                "pdf_text",
                f"Q{number}:",
                None,
                0.99,
                (0.08, 0.12 + (number - 1) * 0.15, 0.08, 0.025),
            )
            for number in range(1, 6)
        ]

        _segment(db, job, submission, [page], {page.id: blocks})
        db.commit()

        answers = db.scalars(
            select(StudentAnswer)
            .join(Question, Question.id == StudentAnswer.question_id)
            .where(StudentAnswer.submission_id == submission.id, Question.status == "active")
        ).all()
        regions = db.scalars(
            select(StudentAnswerRegion)
            .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
            .join(Question, Question.id == StudentAnswer.question_id)
            .where(StudentAnswer.submission_id == submission.id, Question.status == "active")
        ).all()
        anchors = db.scalars(
            select(SubmissionQuestionAnchor).where(
                SubmissionQuestionAnchor.submission_processing_job_id == job.id
            )
        ).all()
        assert len(answers) == len(regions) == len(anchors) == 5
        assert {region.status for region in regions} == {"candidate"}
        assert {region.reason for region in regions} == {"QUESTION_ANCHOR"}
        assert all(region.source_question_anchor_id for region in regions)
        assert {anchor.source_kind for anchor in anchors} == {"pdf_text"}
        assert {anchor.page_version for anchor in anchors} == {4}
        assert not db.scalars(
            select(StudentAnswer).where(StudentAnswer.question_id == removed.id)
        ).all()

        for region in regions:
            region.status = "confirmed"
            region.confirmation_origin = "system_auto"
            region.confirmed_by = submission.owner_id
            region.confirmed_at = now_utc()
        second_job = SubmissionProcessingJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            idempotency_key=f"repeat-system-anchors-{uuid.uuid4()}",
            status="running",
        )
        db.add(second_job)
        db.flush()
        _segment(db, second_job, submission, [page], {page.id: blocks})
        db.commit()

        repeated_regions = db.scalars(
            select(StudentAnswerRegion)
            .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
            .join(Question, Question.id == StudentAnswer.question_id)
            .where(StudentAnswer.submission_id == submission.id, Question.status == "active")
        ).all()
        current_regions = [
            region
            for region in repeated_regions
            if region.status in {"candidate", "confirmed", "manual_required"}
        ]
        assert len(current_regions) == 5
        assert {region.status for region in current_regions} == {"candidate"}
        assert len([region for region in repeated_regions if region.status == "superseded"]) == 5
        assert not [
            region for region in current_regions if region.reason == "HIGH_OVERLAP_CONFLICT"
        ]

        teacher_region = next(
            region
            for region in current_regions
            if next(
                answer.question_id for answer in answers if answer.id == region.student_answer_id
            )
            == uuid.UUID(question_id)
        )
        teacher_region.status = "confirmed"
        teacher_region.confirmation_origin = "teacher_explicit"
        teacher_region.confirmed_by = submission.owner_id
        teacher_region.confirmed_at = now_utc()
        teacher_region_id = teacher_region.id
        third_job = SubmissionProcessingJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            idempotency_key=f"repeat-teacher-anchors-{uuid.uuid4()}",
            status="running",
        )
        db.add(third_job)
        db.flush()
        _segment(db, third_job, submission, [page], {page.id: blocks})
        db.commit()

        visible_regions = client.get(f"/api/submissions/{submission.id}/region-candidates")
        latest_anchors = client.get(f"/api/submissions/{submission.id}/question-anchors")
        assert visible_regions.status_code == 200, visible_regions.text
        assert latest_anchors.status_code == 200, latest_anchors.text
        assert len(visible_regions.json()) == 5
        assert len(latest_anchors.json()) == 5
        assert not [
            region
            for region in visible_regions.json()
            if region["reason"] == "HIGH_OVERLAP_CONFLICT"
        ]
        persisted_teacher = db.get(StudentAnswerRegion, teacher_region_id)
        assert persisted_teacher is not None
        assert persisted_teacher.status == "confirmed"
        assert persisted_teacher.confirmation_origin == "teacher_explicit"
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_four_page_acceptance_layout_supports_multi_question_and_cross_page() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    try:
        first_answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert first_answer is None
        # Resolve through the submission to avoid relying on any page/question ordinal.
        from app.models import Submission

        submission = db.get(Submission, submission_id)
        assert submission is not None
        assignment = db.get(Assignment, submission.assignment_id)
        assert assignment is not None and assignment.active_paper_version_id is not None
        questions = [
            db.get(Question, uuid.UUID(question_id)),
            *[
                Question(
                    paper_version_id=assignment.active_paper_version_id,
                    question_number=str(number),
                    display_order=number,
                    question_type="single_choice",
                    max_score=10,
                    status=QuestionStatus.active,
                    source="manual",
                )
                for number in (2, 3, 4)
            ],
        ]
        assert questions[0] is not None
        db.add_all(questions[1:])
        pages = db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        ).all()
        fourth = SubmissionPage(
            submission_id=submission_id,
            stored_file_id=pages[-1].stored_file_id,
            page_number=4,
            source_page_number=1,
            status="ready",
        )
        db.add(fourth)
        job = SubmissionProcessingJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            idempotency_key="acceptance-four-pages",
            status="running",
        )
        db.add(job)
        db.flush()
        pages.append(fourth)
        blocks = {
            pages[0].id: [ProviderBlock("text", "1.", None, 0.96, (0.05, 0.08, 0.1, 0.03))],
            pages[1].id: [
                ProviderBlock("text", "2.", None, 0.94, (0.05, 0.08, 0.1, 0.03)),
                ProviderBlock("text", "3.", None, 0.92, (0.05, 0.52, 0.1, 0.03)),
            ],
            pages[2].id: [ProviderBlock("text", "4.", None, 0.95, (0.05, 0.08, 0.1, 0.03))],
            pages[3].id: [ProviderBlock("text", "4.", None, 0.60, (0.05, 0.08, 0.1, 0.03))],
        }
        _segment(db, job, submission, pages, blocks)
        db.commit()
        answers = db.scalars(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        ).all()
        by_number = {
            db.get(Question, answer.question_id).question_number: answer for answer in answers
        }
        page_two_regions = db.scalars(
            select(StudentAnswerRegion).where(StudentAnswerRegion.submission_page_id == pages[1].id)
        ).all()
        question_four_regions = db.scalars(
            select(StudentAnswerRegion).where(
                StudentAnswerRegion.student_answer_id == by_number["4"].id
            )
        ).all()
        rejected_low_anchor = db.scalar(
            select(SubmissionQuestionAnchor).where(
                SubmissionQuestionAnchor.submission_processing_job_id == job.id,
                SubmissionQuestionAnchor.submission_page_id == pages[3].id,
                SubmissionQuestionAnchor.normalized_number == "4",
            )
        )
        assert len(pages) == 4
        assert len(page_two_regions) == 2
        assert len(question_four_regions) == 1
        assert question_four_regions[0].submission_page_id == pages[2].id
        assert rejected_low_anchor is not None
        assert rejected_low_anchor.rejection_reason == "LOW_ANCHOR_CONFIDENCE"
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()
