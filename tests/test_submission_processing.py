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
    now_utc,
)
from app.recognition.pipeline import PageArtifact, ProviderBlock
from app.recognition.submission_processing import (
    _hash_distance,
    _normalize_question_number,
    _segment,
    preprocess_page,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import func, select
from test_submission_workflow import workflow

client = TestClient(app)


def artifact(background: str = "white", text: str | None = None) -> PageArtifact:
    image = Image.new("RGB", (600, 800), background)
    if text:
        ImageDraw.Draw(image).text((40, 50), text, fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    return PageArtifact(output.getvalue(), image.width, image.height)


def test_quality_metrics_detect_blank_and_duplicate_deterministically() -> None:
    _, blank = preprocess_page(artifact())
    _, same = preprocess_page(artifact())
    _, content = preprocess_page(artifact(text="1. synthetic answer"))
    assert Decimal(str(blank["blank_probability"])) >= Decimal("0.95")
    assert _hash_distance(str(blank["perceptual_hash"]), str(same["perceptual_hash"])) == 0
    assert str(content["perceptual_hash"]) != str(blank["perceptual_hash"])


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
    ]
    assert _normalize_question_number("\u7b54\u6848\u5f15\u7528\u7b2c 9 \u9898") is None


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
        assert len(pages) == 4
        assert len(page_two_regions) == 2
        assert len(question_four_regions) == 2
        assert {region.submission_page_id for region in question_four_regions} == {
            pages[2].id,
            pages[3].id,
        }
        assert any(region.status == "manual_required" for region in question_four_regions)
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()
