import hashlib
import io
import json
import uuid
from decimal import Decimal

from PIL import Image
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Assignment,
    Question,
    QuestionRecognitionEvidence,
    QuestionStatus,
    RecognitionRevision,
    RegionEvidenceImage,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    now_utc,
)
from app.recognition.answer_providers import (
    AnswerProviderError,
    normalize_math,
    provider_from_settings,
)
from app.recognition.pipeline import PageArtifact, read_all
from app.storage.base import ObjectStorage


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _crop(content: bytes, region: StudentAnswerRegion, margin: int) -> PageArtifact:
    image = Image.open(io.BytesIO(content)).convert("RGB")
    values = [Decimal(region.x), Decimal(region.y), Decimal(region.width), Decimal(region.height)]
    if (
        min(values) < 0
        or values[2] <= 0
        or values[3] <= 0
        or values[0] + values[2] > 1
        or values[1] + values[3] > 1
    ):
        raise AnswerProviderError("ANSWER_REGION_OUT_OF_BOUNDS", "region is outside page bounds")
    left = max(0, int(float(values[0]) * image.width) - margin)
    top = max(0, int(float(values[1]) * image.height) - margin)
    right = min(image.width, int(float(values[0] + values[2]) * image.width) + margin)
    bottom = min(image.height, int(float(values[1] + values[3]) * image.height) + margin)
    if right <= left or bottom <= top:
        raise AnswerProviderError("ANSWER_REGION_EMPTY", "region has no pixels")
    cropped = image.crop((left, top, right, bottom))
    output = io.BytesIO()
    cropped.save(output, "PNG", optimize=False, compress_level=9)
    return PageArtifact(output.getvalue(), cropped.width, cropped.height)


def _evidence(
    db: Session,
    storage: ObjectStorage,
    settings: Settings,
    job: SubmissionRecognitionJob,
    page: SubmissionPage,
    region: StudentAnswerRegion,
    source_kind: str,
    source_key: str,
    region_order: int,
) -> tuple[RegionEvidenceImage, PageArtifact]:
    input_hash = _digest(
        {
            "source_key": source_key,
            "page_version": page.page_version,
            "region_id": region.id,
            "region_version": region.region_version,
            "bbox": [region.x, region.y, region.width, region.height],
            "margin": settings.answer_recognition_margin_pixels,
            "config": settings.answer_recognition_config_version,
        }
    )
    existing = db.scalar(
        select(RegionEvidenceImage).where(
            RegionEvidenceImage.student_answer_region_id == region.id,
            RegionEvidenceImage.source_kind == source_kind,
            RegionEvidenceImage.page_version == page.page_version,
            RegionEvidenceImage.region_version == region.region_version,
            RegionEvidenceImage.processing_config_version
            == settings.answer_recognition_config_version,
        )
    )
    artifact = _crop(
        read_all(storage.get(source_key)), region, settings.answer_recognition_margin_pixels
    )
    content_hash = hashlib.sha256(artifact.content).hexdigest()
    key = (
        f"answer-evidence/{job.owner_id}/{job.submission_id}/{page.page_version}/"
        f"{region.id}/{region.region_version}/{source_kind}-{content_hash}.png"
    )
    if existing is None:
        existing = RegionEvidenceImage(
            owner_id=job.owner_id,
            submission_id=job.submission_id,
            submission_page_id=page.id,
            student_answer_region_id=region.id,
            source_kind=source_kind,
            object_key=key,
            content_hash=content_hash,
            input_hash=input_hash,
            width=artifact.width,
            height=artifact.height,
            margin_pixels=settings.answer_recognition_margin_pixels,
            source_page_number=page.source_page_number or page.page_number,
            region_order=region_order,
            page_version=page.page_version,
            region_version=region.region_version,
            processing_config_version=settings.answer_recognition_config_version,
            status="ready",
        )
        db.add(existing)
        db.flush()
        storage.put(key, io.BytesIO(artifact.content), len(artifact.content), "image/png")
    elif existing.content_hash != content_hash or existing.input_hash != input_hash:
        existing.status = "stale"
        existing.stale_at = now_utc()
        raise AnswerProviderError("EVIDENCE_VERSION_CONFLICT", "evidence input changed")
    return existing, artifact


def _discard_late_job_result(
    db: Session,
    current: SubmissionRecognitionJob | None,
    created_block_ids: set[uuid.UUID],
) -> None:
    timestamp = now_utc()
    if current is not None:
        current.warning_codes = sorted(set([*current.warning_codes, "LATE_RESULT_DISCARDED"]))
        current.error_code = "LATE_RESULT_DISCARDED"
        if current.status != "cancelled":
            current.status = "stale"
    if created_block_ids:
        for block in db.scalars(
            select(SubmissionRecognitionBlock).where(
                SubmissionRecognitionBlock.id.in_(created_block_ids),
                SubmissionRecognitionBlock.stale_at.is_(None),
            )
        ):
            block.status, block.requires_review, block.stale_at = (
                "late_discarded",
                True,
                timestamp,
            )
    db.commit()


def _locked_recognition_generation(
    db: Session,
    submission_id: uuid.UUID,
    job_id: uuid.UUID,
) -> tuple[SubmissionRecognitionJob | None, int]:
    db.scalar(
        select(Submission)
        .where(Submission.id == submission_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current = db.scalar(
        select(SubmissionRecognitionJob)
        .where(SubmissionRecognitionJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    maximum = (
        db.scalar(
            select(func.max(SubmissionRecognitionJob.generation)).where(
                SubmissionRecognitionJob.submission_id == submission_id
            )
        )
        or 0
    )
    return current, maximum


def run_answer_evidence_phase(
    db: Session,
    storage: ObjectStorage,
    settings: Settings,
    job_id: uuid.UUID,
    *,
    region_id: uuid.UUID | None = None,
) -> None:
    job_hint = db.get(SubmissionRecognitionJob, job_id)
    if job_hint is None:
        return
    submission = db.scalar(
        select(Submission)
        .where(Submission.id == job_hint.submission_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = db.scalar(
        select(SubmissionRecognitionJob)
        .where(SubmissionRecognitionJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status == "cancelled":
        return
    current_generation = (
        db.scalar(
            select(func.max(SubmissionRecognitionJob.generation)).where(
                SubmissionRecognitionJob.submission_id == job.submission_id
            )
        )
        or 0
    )
    if job.generation < current_generation:
        _discard_late_job_result(db, job, set())
        return
    if job.status == "completed":
        return
    if job.attempt >= job.max_attempts:
        job.status, job.error_code, job.error_message = (
            "failed",
            "RETRY_EXHAUSTED",
            "recognition retry limit reached",
        )
        db.commit()
        return
    if (
        submission is None
        or submission.finalized_at is not None
        or submission.status in {"finalized", "voided"}
    ):
        if job:
            job.status = "failed"
            job.error_code = (
                "VOIDED_SUBMISSION_IMMUTABLE"
                if submission is not None and submission.status == "voided"
                else "FINALIZED_SUBMISSION_IMMUTABLE"
            )
            db.commit()
        return
    assignment = db.get(Assignment, submission.assignment_id)
    if assignment is None or assignment.active_paper_version_id is None:
        job.status, job.error_code, job.error_message = (
            "failed",
            "ACTIVE_PAPER_REQUIRED",
            "the submission assignment has no active paper",
        )
        db.commit()
        return
    generation = job.generation
    regions = list(
        db.scalars(
            select(StudentAnswerRegion)
            .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
            .join(Question, Question.id == StudentAnswer.question_id)
            .join(SubmissionPage, SubmissionPage.id == StudentAnswerRegion.submission_page_id)
            .where(
                StudentAnswer.submission_id == submission.id,
                StudentAnswerRegion.status == "confirmed",
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == QuestionStatus.active,
            )
            .order_by(
                SubmissionPage.page_number,
                StudentAnswerRegion.y,
                StudentAnswerRegion.x,
                StudentAnswerRegion.id,
            )
        ).all()
    )
    answers = list(
        db.scalars(
            select(StudentAnswer)
            .join(Question, Question.id == StudentAnswer.question_id)
            .where(
                StudentAnswer.submission_id == submission.id,
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == QuestionStatus.active,
            )
        ).all()
    )
    answer_versions = {
        answer.id: (
            db.scalar(
                select(func.max(QuestionRecognitionEvidence.recognition_version)).where(
                    QuestionRecognitionEvidence.student_answer_id == answer.id
                )
            )
            or 0
        )
        + 1
        for answer in answers
    }
    next_block_index: dict[uuid.UUID, int] = {}
    incomplete = {
        answer.id
        for answer in answers
        if not any(region.student_answer_id == answer.id for region in regions)
    }
    if incomplete:
        job.status, job.error_code, job.error_message = (
            "failed",
            "SEGMENTATION_INCOMPLETE",
            "all answers require a confirmed region",
        )
        db.commit()
        return
    if region_id:
        regions = [region for region in regions if region.id == region_id]
        if not regions:
            job.status, job.error_code = "failed", "CONFIRMED_REGION_NOT_FOUND"
            db.commit()
            return
    provider = provider_from_settings(settings)
    job.status, job.progress, job.attempt, job.started_at = "running", 0, job.attempt + 1, now_utc()
    job.provider, job.provider_version = provider.name, provider.version
    job.config_version = settings.answer_recognition_config_version
    job.input_hash = _digest(
        [(region.id, region.region_version, region.segmentation_version) for region in regions]
    )
    db.execute(
        delete(SubmissionRecognitionBlock).where(
            SubmissionRecognitionBlock.submission_recognition_job_id == job.id,
            SubmissionRecognitionBlock.student_answer_region_id.is_(None),
        )
    )
    db.commit()
    failures = 0
    failed_page_ids: set[uuid.UUID] = set()
    created: list[SubmissionRecognitionBlock] = []
    created_block_ids: set[uuid.UUID] = set()
    for order, region in enumerate(regions):
        page = db.get(SubmissionPage, region.submission_page_id)
        if page is None:
            failures += 1
            failed_page_ids.add(region.submission_page_id)
            continue
        try:
            active_blocks = list(
                db.scalars(
                    select(SubmissionRecognitionBlock).where(
                        SubmissionRecognitionBlock.student_answer_region_id == region.id,
                        SubmissionRecognitionBlock.stale_at.is_(None),
                    )
                ).all()
            )
            preserved_human_blocks = [
                active
                for active in active_blocks
                if db.scalar(
                    select(RecognitionRevision.id).where(
                        RecognitionRevision.recognition_block_id == active.id,
                        RecognitionRevision.source == "human",
                        RecognitionRevision.stale_at.is_(None),
                    )
                )
                is not None
            ]
            if preserved_human_blocks:
                job.warning_codes = sorted(set([*job.warning_codes, "MANUAL_REVISION_PRESERVED"]))
                created.extend(preserved_human_blocks)
                job.progress = int((order + 1) / max(1, len(regions)) * 100)
                db.commit()
                continue
            if not page.rendered_storage_key or not page.processed_storage_key:
                raise AnswerProviderError(
                    "PAGE_ARTIFACT_UNAVAILABLE", "page processing is incomplete"
                )
            _evidence(
                db,
                storage,
                settings,
                job,
                page,
                region,
                "original",
                page.rendered_storage_key,
                order,
            )
            processed, artifact = _evidence(
                db,
                storage,
                settings,
                job,
                page,
                region,
                "processed",
                page.processed_storage_key,
                order,
            )
            reusable_blocks = [
                block
                for block in active_blocks
                if block.submission_recognition_job_id == job.id
                and block.input_hash == processed.input_hash
                and block.status in {"recognized", "requires_review"}
            ]
            if reusable_blocks:
                created.extend(reusable_blocks)
                continue
            blocks = provider.recognize(artifact, job.provider_kind)  # type: ignore[arg-type]
            if not blocks:
                raise AnswerProviderError("PROVIDER_EMPTY_RESULT", "provider returned no blocks")
            current, current_generation = _locked_recognition_generation(
                db,
                job.submission_id,
                job.id,
            )
            if (
                current is None
                or current.generation != generation
                or generation < current_generation
                or current.status == "cancelled"
            ):
                _discard_late_job_result(db, current, created_block_ids)
                return
            for active in active_blocks:
                active.status, active.requires_review, active.stale_at = (
                    "stale",
                    True,
                    now_utc(),
                )
            for index, result in enumerate(blocks):
                normalized = normalize_math(result.text, result.latex, result.block_type)
                warnings = list(normalized.warnings)
                requires_review = (
                    result.confidence is None
                    or result.confidence < settings.recognition_high_confidence
                    or bool(warnings)
                    or result.block_type == "unknown"
                )
                version = answer_versions[region.student_answer_id]
                if page.id not in next_block_index:
                    next_block_index[page.id] = (
                        db.scalar(
                            select(func.max(SubmissionRecognitionBlock.block_index)).where(
                                SubmissionRecognitionBlock.submission_page_id == page.id
                            )
                        )
                        or 0
                    ) + 1
                output_hash = _digest(
                    [result.block_type, result.text, normalized.text, normalized.latex]
                )
                block = SubmissionRecognitionBlock(
                    submission_recognition_job_id=job.id,
                    submission_page_id=page.id,
                    student_answer_region_id=region.id,
                    region_evidence_image_id=processed.id,
                    source_page_number=page.source_page_number or page.page_number,
                    block_index=next_block_index[page.id],
                    block_type=result.block_type,
                    text=result.text,
                    normalized_text=normalized.text,
                    latex=normalized.latex,
                    confidence=result.confidence,
                    status="requires_review" if requires_review else "recognized",
                    x=result.region[0],
                    y=result.region[1],
                    width=result.region[2],
                    height=result.region[3],
                    reading_order=order * 1000 + index,
                    provider=provider.name,
                    provider_version=provider.version,
                    warning_codes=warnings,
                    requires_review=requires_review,
                    evidence_image_key=processed.object_key,
                    recognition_version=version,
                    input_hash=processed.input_hash,
                    output_hash=output_hash,
                )
                next_block_index[page.id] += 1
                db.add(block)
                db.flush()
                created_block_ids.add(block.id)
                db.add(
                    RecognitionRevision(
                        recognition_block_id=block.id,
                        revision=1,
                        source="worker",
                        raw_text=block.text,
                        normalized_text=block.normalized_text,
                        latex=block.latex,
                        warning_codes=warnings,
                        base_recognition_version=version,
                        confirmed=False,
                    )
                )
                created.append(block)
        except AnswerProviderError as exc:
            failures += 1
            failed_page_ids.add(page.id)
            job.error_code, job.error_message = exc.code, str(exc)
            job.warning_codes = sorted(set([*job.warning_codes, exc.code]))
        job.progress = int((order + 1) / max(1, len(regions)) * 100)
        db.commit()
    current, current_generation = _locked_recognition_generation(
        db,
        job.submission_id,
        job.id,
    )
    if (
        current is None
        or current.generation != generation
        or generation < current_generation
        or current.status == "cancelled"
    ):
        _discard_late_job_result(db, current, created_block_ids)
        return
    for answer in answers:
        answer_blocks = [
            block
            for block in created
            if block.student_answer_region_id
            and (block_region := db.get(StudentAnswerRegion, block.student_answer_region_id))
            is not None
            and block_region.student_answer_id == answer.id
        ]
        if not answer_blocks:
            continue
        sources: list[dict[str, object]] = []
        for block in sorted(answer_blocks, key=lambda item: item.reading_order):
            block_region = db.get(StudentAnswerRegion, block.student_answer_region_id)
            if block_region is None:
                continue
            preserved_human_revision = (
                db.scalar(
                    select(RecognitionRevision.id).where(
                        RecognitionRevision.recognition_block_id == block.id,
                        RecognitionRevision.source == "human",
                        RecognitionRevision.stale_at.is_(None),
                    )
                )
                is not None
            )
            sources.append(
                {
                    "block_id": str(block.id),
                    "page_id": str(block.submission_page_id),
                    "region_id": str(block.student_answer_region_id),
                    "region_version": block_region.region_version,
                    "region_bbox": [
                        str(block_region.x),
                        str(block_region.y),
                        str(block_region.width),
                        str(block_region.height),
                    ],
                    "block_recognition_job_id": str(block.submission_recognition_job_id),
                    "block_recognition_version": block.recognition_version,
                    "preserved_human_revision": preserved_human_revision,
                    "source_page_number": block.source_page_number,
                    "reading_order": block.reading_order,
                }
            )
        text = "\n".join(block.normalized_text or block.text or "" for block in answer_blocks)
        input_hash = _digest([block.input_hash for block in answer_blocks])
        existing_evidence = db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.recognition_job_id == job.id,
                QuestionRecognitionEvidence.student_answer_id == answer.id,
                QuestionRecognitionEvidence.input_hash == input_hash,
                QuestionRecognitionEvidence.stale_at.is_(None),
            )
        )
        if existing_evidence is not None:
            answer.recognized_text = existing_evidence.normalized_text
            answer.requires_review = existing_evidence.requires_review
            answer.status = existing_evidence.status
            continue
        evidence = QuestionRecognitionEvidence(
            owner_id=job.owner_id,
            submission_id=submission.id,
            student_answer_id=answer.id,
            recognition_job_id=job.id,
            status="requires_review"
            if any(block.requires_review for block in answer_blocks)
            else "recognized",
            block_sources=sources,
            normalized_text=text or None,
            latex=None,
            provider_versions={provider.name: provider.version},
            input_hash=input_hash,
            output_hash=_digest([block.output_hash for block in answer_blocks]),
            recognition_version=answer_versions[answer.id],
            requires_review=any(block.requires_review for block in answer_blocks),
        )
        db.add(evidence)
        answer.recognized_text = text or None
        answer.requires_review = evidence.requires_review
        answer.status = evidence.status
    recognized_page_ids = {block.submission_page_id for block in created} - failed_page_ids
    submission_pages = list(
        db.scalars(
            select(SubmissionPage).where(SubmissionPage.submission_id == submission.id)
        ).all()
    )
    for page in submission_pages:
        if page.id in recognized_page_ids:
            page.status = "recognized"
    if submission_pages and all(
        page.status in {"recognized", "blank"} for page in submission_pages
    ):
        submission.status, submission.recognized_at = "recognized", now_utc()
    job.output_hash = _digest([block.output_hash for block in created])
    job.completed_at = now_utc()
    job.status = (
        "partially_completed" if failures and created else ("failed" if failures else "completed")
    )
    if not failures:
        job.error_code = job.error_message = None
    db.commit()


def mark_answer_recognition_stale(db: Session, answer_id: uuid.UUID) -> None:
    timestamp = now_utc()
    regions = select(StudentAnswerRegion.id).where(
        StudentAnswerRegion.student_answer_id == answer_id
    )
    for image in db.scalars(
        select(RegionEvidenceImage).where(
            RegionEvidenceImage.student_answer_region_id.in_(regions),
            RegionEvidenceImage.stale_at.is_(None),
        )
    ):
        image.status, image.stale_at = "stale", timestamp
    for block in db.scalars(
        select(SubmissionRecognitionBlock).where(
            SubmissionRecognitionBlock.student_answer_region_id.in_(regions),
            SubmissionRecognitionBlock.stale_at.is_(None),
        )
    ):
        block.status, block.requires_review, block.stale_at = "stale", True, timestamp
    for evidence in db.scalars(
        select(QuestionRecognitionEvidence).where(
            QuestionRecognitionEvidence.student_answer_id == answer_id,
            QuestionRecognitionEvidence.stale_at.is_(None),
        )
    ):
        evidence.status, evidence.requires_review, evidence.stale_at = "stale", True, timestamp


def next_revision(db: Session, block_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.max(RecognitionRevision.revision)).where(
                RecognitionRevision.recognition_block_id == block_id
            )
        )
        or 0
    ) + 1
