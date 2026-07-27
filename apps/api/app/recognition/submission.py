import io
import uuid
from decimal import Decimal

from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Assignment,
    GradingResult,
    Question,
    QuestionStatus,
    StoredFile,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    now_utc,
)
from app.recognition.pipeline import (
    DefaultDocumentConverter,
    PageArtifact,
    PillowPreprocessor,
    RecognitionError,
    derivative_key,
    provider_from_settings,
    read_all,
    store_artifact,
)
from app.storage.base import ObjectStorage


def thumbnail(page: PageArtifact) -> PageArtifact:
    image = Image.open(io.BytesIO(page.content)).convert("RGB")
    image.thumbnail((320, 320))
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return PageArtifact(output.getvalue(), image.width, image.height)


def run_submission_recognition_job(
    db: Session, storage: ObjectStorage, job_id: uuid.UUID, page_id: uuid.UUID | None = None
) -> None:
    job_record = db.get(SubmissionRecognitionJob, job_id)
    if job_record is None or (job_record.status == "completed" and page_id is None):
        return
    submission = db.get(Submission, job_record.submission_id)
    if submission is None or submission.owner_id != job_record.owner_id:
        return
    settings = get_settings()
    provider = provider_from_settings(settings)
    available, reason = provider.available()
    if not available:
        job_record.status, job_record.error_code, job_record.error_message = (
            "failed",
            "RECOGNITION_PROVIDER_UNAVAILABLE",
            reason,
        )
        db.commit()
        return
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission.id)
        .order_by(SubmissionPage.page_number)
    ).all()
    if page_id:
        pages = [page for page in pages if page.id == page_id]
    assignment = db.get(Assignment, submission.assignment_id)
    questions = (
        db.scalars(
            select(Question)
            .where(
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == QuestionStatus.active,
            )
            .order_by(Question.display_order)
        ).all()
        if assignment and assignment.active_paper_version_id
        else []
    )
    job_record.status = "running"
    # Every active question gets an answer record up front. Question/page association
    # is represented only by StudentAnswerRegion; page order is never treated as a
    # question number.
    for question in questions:
        answer = db.scalar(
            select(StudentAnswer).where(
                StudentAnswer.submission_id == submission.id,
                StudentAnswer.question_id == question.id,
            )
        )
        if answer is None:
            db.add(
                StudentAnswer(
                    submission_id=submission.id,
                    question_id=question.id,
                    question_version_reference=str(
                        assignment.active_paper_version_id if assignment else "unknown"
                    ),
                    status="manual_segmentation_required",
                    requires_review=True,
                )
            )
    db.flush()
    # A single-question paper has an unambiguous whole-page template fallback.
    # Multi-question papers never receive an order-based guess and require template
    # regions or explicit teacher segmentation.
    if len(questions) == 1 and pages:
        answer = db.scalar(
            select(StudentAnswer).where(
                StudentAnswer.submission_id == submission.id,
                StudentAnswer.question_id == questions[0].id,
            )
        )
        existing_region = (
            db.scalar(
                select(StudentAnswerRegion.id).where(
                    StudentAnswerRegion.student_answer_id == answer.id
                )
            )
            if answer
            else None
        )
        if answer and existing_region is None:
            db.add(
                StudentAnswerRegion(
                    student_answer_id=answer.id,
                    submission_page_id=pages[0].id,
                    x=0,
                    y=0,
                    width=1,
                    height=1,
                    source="template",
                    confidence=Decimal("1"),
                    status="confirmed",
                    confirmed_by=job_record.owner_id,
                    confirmed_at=now_utc(),
                )
            )
    db.commit()
    failures = 0
    for page in pages:
        page.status = "processing"
        db.commit()
        try:
            stored = db.get(StoredFile, page.stored_file_id)
            if stored is None or stored.owner_id != submission.owner_id:
                raise RecognitionError("SOURCE_FILE_NOT_FOUND", "原始文件不存在")
            content = read_all(storage.get(stored.storage_key))
            rendered = DefaultDocumentConverter(settings).convert(
                content, stored.content_type, page.source_page_number or 1
            )
            processed = PillowPreprocessor().process(rendered, {"rotation": page.rotation})
            page.rendered_storage_key = derivative_key(
                job_record.owner_id, job_record.id, page.id, "rendered"
            )
            page.processed_storage_key = derivative_key(
                job_record.owner_id, job_record.id, page.id, "processed"
            )
            page.thumbnail_storage_key = derivative_key(
                job_record.owner_id, job_record.id, page.id, "thumbnail"
            )
            store_artifact(storage, page.rendered_storage_key, rendered)
            store_artifact(storage, page.processed_storage_key, processed)
            store_artifact(storage, page.thumbnail_storage_key, thumbnail(processed))
            blocks = provider.recognize(processed)
            db.execute(
                delete(SubmissionRecognitionBlock).where(
                    SubmissionRecognitionBlock.submission_page_id == page.id
                )
            )
            for index, block in enumerate(blocks):
                db.add(
                    SubmissionRecognitionBlock(
                        submission_recognition_job_id=job_record.id,
                        submission_page_id=page.id,
                        block_index=index,
                        text=block.text,
                        latex=None,
                        confidence=block.confidence,
                        status=block.status,
                        x=block.region[0],
                        y=block.region[1],
                        width=block.region[2],
                        height=block.region[3],
                        provider=provider.name,
                        provider_version=provider.version,
                    )
                )
            page.width, page.height = rendered.width, rendered.height
            page.status = "blank" if not blocks else "recognized"
            db.commit()
        except Exception as exc:
            db.rollback()
            failed_page = db.get(SubmissionPage, page.id)
            if failed_page:
                failed_page.status = "failed"
            failures += 1
            current_job = db.get(SubmissionRecognitionJob, job_record.id)
            if current_job:
                current_job.error_code = (
                    exc.code if isinstance(exc, RecognitionError) else "OCR_FAILED"
                )
                current_job.error_message = str(exc)[:500]
            db.commit()
    # Aggregate OCR only through confirmed answer regions. This naturally supports
    # multiple questions on one page and one question spanning multiple pages.
    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
    ).all()
    for answer in answers:
        regions = db.scalars(
            select(StudentAnswerRegion).where(
                StudentAnswerRegion.student_answer_id == answer.id,
                StudentAnswerRegion.status == "confirmed",
            )
        ).all()
        if not regions:
            answer.status, answer.requires_review = "manual_segmentation_required", True
            continue
        selected: list[SubmissionRecognitionBlock] = []
        for region in regions:
            recognized_blocks = db.scalars(
                select(SubmissionRecognitionBlock)
                .where(SubmissionRecognitionBlock.submission_page_id == region.submission_page_id)
                .order_by(SubmissionRecognitionBlock.block_index)
            ).all()
            for recognized_row in recognized_blocks:
                center_x = Decimal(recognized_row.x) + Decimal(recognized_row.width) / 2
                center_y = Decimal(recognized_row.y) + Decimal(recognized_row.height) / 2
                if Decimal(region.x) <= center_x <= Decimal(region.x) + Decimal(
                    region.width
                ) and Decimal(region.y) <= center_y <= Decimal(region.y) + Decimal(region.height):
                    selected.append(recognized_row)
        previous_effective = (
            answer.corrected_text if answer.corrected_text is not None else answer.recognized_text
        )
        text = "\n".join(block.text for block in selected if block.text)
        confidences = [
            Decimal(block.confidence) for block in selected if block.confidence is not None
        ]
        confidence = min(confidences) if confidences else None
        answer.recognized_text = text or None
        answer.recognized_latex = None
        answer.recognition_confidence = confidence
        answer.recognition_provider = provider.name
        answer.recognition_provider_version = provider.version
        answer.is_blank = not selected
        answer.status = "blank" if not selected else "recognized"
        current_effective = (
            answer.corrected_text if answer.corrected_text is not None else answer.recognized_text
        )
        changed = previous_effective is not None and previous_effective != current_effective
        answer.requires_review = (
            len(regions) > 1
            or not selected
            or confidence is None
            or confidence < Decimal(str(settings.recognition_high_confidence))
            or changed
        )
        if changed:
            answer.status = "stale"
            for result in db.scalars(
                select(GradingResult).where(
                    GradingResult.student_answer_id == answer.id,
                    GradingResult.status.in_(["suggested", "accepted", "modified"]),
                )
            ).all():
                result.status = "stale"
    db.commit()
    final_job = db.get(SubmissionRecognitionJob, job_id)
    if final_job:
        final_job.status = (
            "partially_completed"
            if failures and failures < len(pages)
            else ("failed" if failures else "completed")
        )
        if not failures:
            final_job.error_code = final_job.error_message = None
            submission.status, submission.recognized_at = "recognized", now_utc()
        db.commit()


def mark_submission_stale(db: Session, submission_id: uuid.UUID) -> None:
    from app.math_validation.stale import stale_for_answer
    from app.recognition.answer_evidence import mark_answer_recognition_stale

    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
    ).all()
    for answer in answers:
        stale_for_answer(db, answer.id, "SCORING_INPUT_CHANGED")
        mark_answer_recognition_stale(db, answer.id)
        answer.status, answer.requires_review = "stale", True
        for result in db.scalars(
            select(GradingResult).where(GradingResult.student_answer_id == answer.id)
        ):
            if result.status not in {"superseded", "rejected"}:
                result.status = "stale"
