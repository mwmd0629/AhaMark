import uuid
from decimal import Decimal

import pytest
from app.main import app
from app.models import (
    Assignment,
    AuditLog,
    GradeRelease,
    PaperPage,
    Question,
    QuestionRecognitionEvidence,
    QuestionRegion,
    RecognitionRevision,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionProcessingJob,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    SubmissionScoreSnapshot,
    TeacherReview,
)
from app.processing.automatic_confirmation import (
    auto_confirm_deterministic_recognition,
    auto_confirm_deterministic_regions,
)
from app.storage.dependencies import get_storage
from sqlalchemy import func, select
from test_submission_workflow import workflow


def region_fixture(
    *,
    confidence: Decimal = Decimal("0.99"),
    extra_region: bool = False,
):
    db, _storage, _batch_id, submission_id, question_id = workflow()
    submission = db.get(Submission, submission_id)
    assert submission is not None
    assignment = db.get(Assignment, submission.assignment_id)
    assert assignment is not None and assignment.active_paper_version_id is not None
    paper_page = db.scalar(
        select(PaperPage).where(PaperPage.paper_version_id == assignment.active_paper_version_id)
    )
    page = db.scalar(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission.id)
        .order_by(SubmissionPage.page_number)
    )
    answer = db.scalar(
        select(StudentAnswer).where(
            StudentAnswer.submission_id == submission.id,
            StudentAnswer.question_id == uuid.UUID(question_id),
        )
    )
    if answer is None:
        answer = StudentAnswer(
            submission_id=submission.id,
            question_id=uuid.UUID(question_id),
            question_version_reference=f"{assignment.active_paper_version_id}:{question_id}",
            status="pending",
            requires_review=True,
        )
        db.add(answer)
        db.flush()
    assert paper_page is not None and page is not None
    page.aligned_paper_page_id = paper_page.id
    page.alignment_confidence = Decimal("0.99")
    page.alignment_failure_reason = None
    page.processing_error_code = None
    page.quality_warnings = []
    coordinates = {
        "x": Decimal("0.10"),
        "y": Decimal("0.20"),
        "width": Decimal("0.30"),
        "height": Decimal("0.40"),
    }
    template = QuestionRegion(
        question_id=answer.question_id,
        paper_page_id=paper_page.id,
        source="manual",
        confidence=Decimal("1"),
        **coordinates,
    )
    job = SubmissionProcessingJob(
        owner_id=submission.owner_id,
        submission_id=submission.id,
        status="completed",
        idempotency_key=f"auto-region-{uuid.uuid4()}",
    )
    region = StudentAnswerRegion(
        student_answer_id=answer.id,
        submission_page_id=page.id,
        source="template",
        confidence=confidence,
        status="candidate",
        reason="ALIGNED_STANDARD_REGION",
        **coordinates,
    )
    db.add_all([template, job, region])
    if extra_region:
        db.add(
            StudentAnswerRegion(
                student_answer_id=answer.id,
                submission_page_id=page.id,
                source="template",
                confidence=Decimal("0.99"),
                status="candidate",
                reason="ALIGNED_STANDARD_REGION",
                x=Decimal("0.55"),
                y=Decimal("0.20"),
                width=Decimal("0.20"),
                height=Decimal("0.40"),
            )
        )
    db.commit()
    return db, submission, answer, region, job


def recognition_fixture(*, job_status: str = "completed", warning: bool = False):
    db, submission, answer, region, _processing_job = region_fixture()
    region.status = "confirmed"
    region.confirmed_by = submission.owner_id
    region.confirmation_origin = "system_auto"
    recognition_job = SubmissionRecognitionJob(
        owner_id=submission.owner_id,
        submission_id=submission.id,
        status=job_status,
        provider="synthetic",
        provider_version="1",
        idempotency_key=f"auto-recognition-{uuid.uuid4()}",
        provider_kind="printed_text",
        config_version="test",
        input_hash="a" * 64,
        output_hash="b" * 64,
        generation=1,
        warning_codes=["OCR_WARNING"] if warning else [],
    )
    db.add(recognition_job)
    db.flush()
    block = SubmissionRecognitionBlock(
        submission_recognition_job_id=recognition_job.id,
        submission_page_id=region.submission_page_id,
        student_answer_region_id=region.id,
        block_index=1,
        text="42",
        normalized_text="42",
        latex=None,
        confidence=Decimal("0.99"),
        status="recognized",
        x=region.x,
        y=region.y,
        width=region.width,
        height=region.height,
        provider="synthetic",
        provider_version="1",
        source_page_number=1,
        block_type="text",
        reading_order=1,
        warning_codes=[],
        requires_review=False,
        recognition_version=1,
        input_hash="c" * 64,
        output_hash="d" * 64,
    )
    db.add(block)
    db.flush()
    evidence = QuestionRecognitionEvidence(
        owner_id=submission.owner_id,
        submission_id=submission.id,
        student_answer_id=answer.id,
        recognition_job_id=recognition_job.id,
        status="recognized",
        block_sources=[{"block_id": str(block.id), "region_id": str(region.id)}],
        normalized_text="42",
        provider_versions={"synthetic": "1"},
        input_hash="e" * 64,
        output_hash="f" * 64,
        recognition_version=1,
        requires_review=False,
    )
    db.add(evidence)
    db.commit()
    return db, submission, answer, block, evidence, recognition_job


def close_fixture(db) -> None:
    app.dependency_overrides.pop(get_storage, None)
    db.close()


def test_region_happy_path_records_system_auto_and_is_idempotent() -> None:
    db, submission, answer, region, job = region_fixture()
    run_id = uuid.uuid4()
    try:
        first = auto_confirm_deterministic_regions(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            processing_job_id=job.id,
            processing_run_id=run_id,
        )
        assert first.eligible and first.changed_count == 1
        assert region.status == "confirmed"
        assert region.confirmation_origin == "system_auto"
        assert region.confirmed_by == submission.owner_id
        assert region.confirmed_at is not None
        assert answer.status == "segmented"
        audit_count = db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "processing.segmentation.auto_confirm")
        )
        assert audit_count == 1

        again = auto_confirm_deterministic_regions(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            processing_job_id=job.id,
            processing_run_id=run_id,
        )
        assert again.eligible and again.changed_count == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "processing.segmentation.auto_confirm")
            )
            == 1
        )
    finally:
        close_fixture(db)


@pytest.mark.parametrize(
    ("confidence", "extra_region", "expected_code"),
    [
        (Decimal("0.94"), False, "SEGMENTATION_NOT_DETERMINISTIC"),
        (Decimal("0.99"), True, "SEGMENTATION_AMBIGUOUS"),
    ],
)
def test_region_ambiguity_or_low_confidence_is_zero_write(
    confidence: Decimal, extra_region: bool, expected_code: str
) -> None:
    db, submission, answer, region, job = region_fixture(
        confidence=confidence, extra_region=extra_region
    )
    try:
        before = (region.status, answer.status, answer.requires_review)
        decision = auto_confirm_deterministic_regions(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            processing_job_id=job.id,
            processing_run_id=uuid.uuid4(),
        )
        assert not decision.eligible and decision.code == expected_code
        assert (region.status, answer.status, answer.requires_review) == before
        assert region.confirmation_origin is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "processing.segmentation.auto_confirm")
            )
            == 0
        )
    finally:
        close_fixture(db)


def test_recognition_happy_path_is_system_auto_suggestion_only_and_idempotent() -> None:
    db, submission, answer, block, evidence, job = recognition_fixture()
    run_id = uuid.uuid4()
    try:
        first = auto_confirm_deterministic_recognition(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            recognition_job_id=job.id,
            processing_run_id=run_id,
            min_confidence=Decimal("0.95"),
        )
        assert first.eligible and first.changed_count == 1
        assert evidence.status == "confirmed"
        assert evidence.confirmation_origin == "system_auto"
        assert evidence.confirmed_by == submission.owner_id
        assert block.status == "confirmed"
        assert answer.status == "recognition_confirmed"
        revisions = db.scalars(
            select(RecognitionRevision).where(
                RecognitionRevision.recognition_block_id == block.id
            )
        ).all()
        assert len(revisions) == 1
        assert revisions[0].source == "system_auto"
        assert revisions[0].confirmed is True
        assert db.scalar(select(func.count()).select_from(TeacherReview)) == 0
        assert db.scalar(select(func.count()).select_from(SubmissionScoreSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(GradeRelease)) == 0

        again = auto_confirm_deterministic_recognition(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            recognition_job_id=job.id,
            processing_run_id=run_id,
            min_confidence=Decimal("0.95"),
        )
        assert again.eligible and again.changed_count == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(RecognitionRevision)
                .where(RecognitionRevision.recognition_block_id == block.id)
            )
            == 1
        )
    finally:
        close_fixture(db)


@pytest.mark.parametrize(
    ("job_status", "warning"),
    [("partially_completed", False), ("completed", True)],
)
def test_recognition_warning_or_partial_is_zero_write(
    job_status: str, warning: bool
) -> None:
    db, submission, answer, block, evidence, job = recognition_fixture(
        job_status=job_status, warning=warning
    )
    try:
        before = (answer.status, block.status, evidence.status)
        decision = auto_confirm_deterministic_recognition(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            recognition_job_id=job.id,
            processing_run_id=uuid.uuid4(),
            min_confidence=Decimal("0.95"),
        )
        assert not decision.eligible
        assert decision.code == "RECOGNITION_REVIEW_REQUIRED"
        assert (answer.status, block.status, evidence.status) == before
        assert evidence.confirmation_origin is None
        assert db.scalar(select(func.count()).select_from(RecognitionRevision)) == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "processing.recognition.auto_confirm")
            )
            == 0
        )
    finally:
        close_fixture(db)


def test_recognition_multiple_current_evidence_versions_are_zero_write() -> None:
    db, submission, answer, block, evidence, job = recognition_fixture()
    second = QuestionRecognitionEvidence(
        owner_id=submission.owner_id,
        submission_id=submission.id,
        student_answer_id=answer.id,
        recognition_job_id=job.id,
        status="recognized",
        block_sources=[{"block_id": str(block.id)}],
        normalized_text="42",
        provider_versions={"synthetic": "1"},
        input_hash="1" * 64,
        output_hash="2" * 64,
        recognition_version=2,
        requires_review=False,
    )
    db.add(second)
    db.commit()
    try:
        decision = auto_confirm_deterministic_recognition(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            recognition_job_id=job.id,
            processing_run_id=uuid.uuid4(),
            min_confidence=Decimal("0.95"),
        )
        assert not decision.eligible
        assert evidence.status == second.status == "recognized"
        assert evidence.confirmation_origin is None
        assert second.confirmation_origin is None
        assert block.status == "recognized"
        assert db.scalar(select(func.count()).select_from(RecognitionRevision)) == 0
    finally:
        close_fixture(db)


def test_recognition_block_sources_must_exactly_cover_current_answer_blocks() -> None:
    db, submission, answer, block, evidence, job = recognition_fixture()
    omitted = SubmissionRecognitionBlock(
        submission_recognition_job_id=job.id,
        submission_page_id=block.submission_page_id,
        student_answer_region_id=block.student_answer_region_id,
        block_index=2,
        text="ambiguous",
        normalized_text="ambiguous",
        confidence=Decimal("0.20"),
        status="recognized",
        x=block.x,
        y=block.y,
        width=block.width,
        height=block.height,
        provider="synthetic",
        provider_version="1",
        source_page_number=1,
        block_type="unknown",
        reading_order=2,
        warning_codes=["LOW_CONFIDENCE"],
        requires_review=True,
        recognition_version=1,
        input_hash="3" * 64,
        output_hash="4" * 64,
    )
    db.add(omitted)
    db.commit()
    try:
        decision = auto_confirm_deterministic_recognition(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            recognition_job_id=job.id,
            processing_run_id=uuid.uuid4(),
            min_confidence=Decimal("0.95"),
        )
        assert not decision.eligible
        assert evidence.status == "recognized"
        assert block.status == omitted.status == "recognized"
        assert db.scalar(select(func.count()).select_from(RecognitionRevision)) == 0
    finally:
        close_fixture(db)


def test_recognition_block_source_must_belong_to_evidence_answer() -> None:
    db, submission, answer, block, evidence, job = recognition_fixture()
    assignment = db.get(Assignment, submission.assignment_id)
    assert assignment is not None and assignment.active_paper_version_id is not None
    other_question = Question(
        paper_version_id=assignment.active_paper_version_id,
        question_number="2",
        display_order=2,
        question_type="short_answer",
        content_text="Other",
        max_score=Decimal("1"),
    )
    db.add(other_question)
    db.flush()
    other_answer = StudentAnswer(
        submission_id=submission.id,
        question_id=other_question.id,
        question_version_reference=f"{assignment.active_paper_version_id}:{other_question.id}",
        status="recognized",
        requires_review=False,
    )
    db.add(other_answer)
    db.flush()
    other_region = StudentAnswerRegion(
        student_answer_id=other_answer.id,
        submission_page_id=block.submission_page_id,
        x=Decimal("0.60"),
        y=Decimal("0.20"),
        width=Decimal("0.20"),
        height=Decimal("0.20"),
        source="template",
        confidence=Decimal("0.99"),
        status="confirmed",
        reason="ALIGNED_STANDARD_REGION",
        confirmed_by=submission.owner_id,
        confirmation_origin="system_auto",
    )
    db.add(other_region)
    db.flush()
    other_block = SubmissionRecognitionBlock(
        submission_recognition_job_id=job.id,
        submission_page_id=block.submission_page_id,
        student_answer_region_id=other_region.id,
        block_index=2,
        text="other",
        normalized_text="other",
        confidence=Decimal("0.99"),
        status="recognized",
        x=other_region.x,
        y=other_region.y,
        width=other_region.width,
        height=other_region.height,
        provider="synthetic",
        provider_version="1",
        source_page_number=1,
        block_type="text",
        reading_order=2,
        warning_codes=[],
        requires_review=False,
        recognition_version=1,
        input_hash="5" * 64,
        output_hash="6" * 64,
    )
    db.add(other_block)
    db.flush()
    other_evidence = QuestionRecognitionEvidence(
        owner_id=submission.owner_id,
        submission_id=submission.id,
        student_answer_id=other_answer.id,
        recognition_job_id=job.id,
        status="recognized",
        block_sources=[{"block_id": str(block.id)}],
        normalized_text="other",
        provider_versions={"synthetic": "1"},
        input_hash="7" * 64,
        output_hash="8" * 64,
        recognition_version=1,
        requires_review=False,
    )
    evidence.block_sources = [{"block_id": str(other_block.id)}]
    db.add(other_evidence)
    db.commit()
    try:
        decision = auto_confirm_deterministic_recognition(
            db,
            owner_id=submission.owner_id,
            submission_id=submission.id,
            recognition_job_id=job.id,
            processing_run_id=uuid.uuid4(),
            min_confidence=Decimal("0.95"),
        )
        assert not decision.eligible
        assert evidence.status == other_evidence.status == "recognized"
        assert block.status == other_block.status == "recognized"
        assert db.scalar(select(func.count()).select_from(RecognitionRevision)) == 0
    finally:
        close_fixture(db)
