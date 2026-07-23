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
            question = (
                questions[page.page_number - 1] if page.page_number <= len(questions) else None
            )
            if question:
                answer = db.scalar(
                    select(StudentAnswer).where(
                        StudentAnswer.submission_id == submission.id,
                        StudentAnswer.question_id == question.id,
                    )
                )
                text = "\n".join(block.text for block in blocks if block.text)
                confidence_values = [
                    Decimal(str(block.confidence))
                    for block in blocks
                    if block.confidence is not None
                ]
                confidence = min(confidence_values) if confidence_values else None
                if answer is None:
                    answer = StudentAnswer(
                        submission_id=submission.id,
                        question_id=question.id,
                        question_version_reference=str(
                            assignment.active_paper_version_id if assignment else "unknown"
                        ),
                    )
                    db.add(answer)
                    db.flush()
                previous_effective = (
                    answer.corrected_text
                    if answer.corrected_text is not None
                    else answer.recognized_text
                )
                answer.recognized_text = text or None
                answer.recognized_latex = None
                answer.recognition_confidence = confidence
                answer.recognition_provider = provider.name
                answer.recognition_provider_version = provider.version
                answer.is_blank = not blocks
                answer.status = "blank" if not blocks else "recognized"
                recognized_requires_review = (
                    not blocks
                    or confidence is None
                    or confidence < Decimal(str(settings.recognition_high_confidence))
                )
                current_effective = (
                    answer.corrected_text
                    if answer.corrected_text is not None
                    else answer.recognized_text
                )
                answer_changed = (
                    previous_effective is not None and previous_effective != current_effective
                )
                answer.requires_review = recognized_requires_review or answer_changed
                if answer_changed:
                    answer.status = "stale"
                    for result in db.scalars(
                        select(GradingResult).where(
                            GradingResult.student_answer_id == answer.id,
                            GradingResult.status.in_(["suggested", "accepted", "modified"]),
                        )
                    ).all():
                        result.status = "stale"
                db.execute(
                    delete(StudentAnswerRegion).where(
                        StudentAnswerRegion.student_answer_id == answer.id
                    )
                )
                for block in blocks:
                    db.add(
                        StudentAnswerRegion(
                            student_answer_id=answer.id,
                            submission_page_id=page.id,
                            x=block.region[0],
                            y=block.region[1],
                            width=block.region[2],
                            height=block.region[3],
                            source="ocr",
                            confidence=block.confidence,
                        )
                    )
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
    db.execute(
        delete(SubmissionRecognitionBlock).where(
            SubmissionRecognitionBlock.submission_page_id.in_(
                select(SubmissionPage.id).where(SubmissionPage.submission_id == submission_id)
            )
        )
    )
    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
    ).all()
    for answer in answers:
        answer.status, answer.requires_review = "stale", True
        for result in db.scalars(
            select(GradingResult).where(GradingResult.student_answer_id == answer.id)
        ):
            if result.status not in {"superseded", "rejected"}:
                result.status = "stale"
