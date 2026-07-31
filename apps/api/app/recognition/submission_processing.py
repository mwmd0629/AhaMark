import io
import math
import re
import uuid
from decimal import Decimal
from typing import Any, cast

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Assignment,
    PaperPage,
    Question,
    QuestionRegion,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionProcessingJob,
    SubmissionQuestionAnchor,
    now_utc,
)
from app.recognition.pipeline import (
    DefaultDocumentConverter,
    PageArtifact,
    ProviderBlock,
    RecognitionError,
    provider_from_settings,
    read_all,
    store_artifact,
)
from app.storage.base import ObjectStorage

PROCESSING_VERSION = "submission-processing-v1"
SEGMENTATION_VERSION = "submission-seg-v1"
ANCHOR_RE = re.compile(
    r"^\s*(?:第\s*)?[（(]?\s*(\d+(?:\.\d+)?(?:[a-zA-Z])?)\s*[）)]?"
    r"(?:\s*题)?(?:[.、:：)\s]|$)",
    re.IGNORECASE,
)


# Override the compatibility pattern above with an encoding-independent expression.
ANCHOR_RE = re.compile(
    r"^\s*(?:(?:\u7b2c|\u9898|[Qq])\s*)?[\uff08(]?\s*(\d+(?:\.\d+)?)"
    r"(?:[\uff08(]?\s*([a-zA-Z])\s*[\uff09)]?)?\s*[\uff09)]?"
    r"(?:\s*\u9898)?(?:[.\u3001:\uff1a)\s]|$)",
    re.IGNORECASE,
)


def _artifact(image: Image.Image) -> PageArtifact:
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return PageArtifact(output.getvalue(), image.width, image.height)


def _average_hash(image: Image.Image) -> str:
    small = ImageOps.grayscale(image).resize((16, 16))
    pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= mean else "0" for pixel in pixels)
    return f"{int(bits, 2):064x}"


def _hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def preprocess_page(rendered: PageArtifact) -> tuple[PageArtifact, dict[str, object]]:
    image = Image.open(io.BytesIO(rendered.content)).convert("RGB")
    detected_rotation = 0
    orientation_confidence = 0.0
    if image.width > image.height * 1.25:
        image = image.rotate(90, expand=True, fillcolor="white")
        detected_rotation = 90
        orientation_confidence = 0.75
    gray = ImageOps.grayscale(image)
    stat = ImageStat.Stat(gray)
    brightness = float(stat.mean[0])
    contrast = float(stat.stddev[0])
    # RMS edge energy is deterministic, cheap, and useful as a blur warning signal.
    edges = gray.filter(ImageFilter.FIND_EDGES)
    blur_score = float(ImageStat.Stat(edges).rms[0])
    sample = list(cast(Any, gray.resize((128, 128)).getdata()))
    white_ratio = sum(1 for value in sample if int(value) >= 245) / 16384
    blank_probability = max(0.0, min(1.0, (white_ratio - 0.80) / 0.20))
    warnings: list[str] = []
    if blur_score < 8:
        warnings.append("LOW_SHARPNESS")
    if brightness < 55:
        warnings.append("TOO_DARK")
    if brightness > 245 and blank_probability < 0.95:
        warnings.append("TOO_BRIGHT")
    if contrast < 8 and blank_probability < 0.95:
        warnings.append("LOW_CONTRAST")
    # Conservative crop: remove only a nearly-uniform white scanner border.
    inverted = ImageOps.invert(gray)
    bbox = inverted.point(lambda value: 255 if value > 12 else 0).getbbox()
    crop = None
    if bbox:
        left, top, right, bottom = bbox
        retained = ((right - left) * (bottom - top)) / (image.width * image.height)
        if 0.65 <= retained < 0.995:
            margin = 8
            bbox = (
                max(0, left - margin),
                max(0, top - margin),
                min(image.width, right + margin),
                min(image.height, bottom + margin),
            )
            image = image.crop(bbox)
            crop = {
                "left": bbox[0],
                "top": bbox[1],
                "right": bbox[2],
                "bottom": bbox[3],
            }
        elif retained < 0.65:
            warnings.append("CROP_ANOMALY")
    processed = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.12)
    processed = processed.filter(ImageFilter.MedianFilter(3)).convert("RGB")
    return _artifact(processed), {
        "blur_score": blur_score,
        "brightness": brightness,
        "contrast": contrast,
        "blank_probability": blank_probability,
        "orientation_confidence": orientation_confidence,
        "rotation": detected_rotation,
        "crop": crop,
        "warnings": warnings,
        "perceptual_hash": _average_hash(processed),
    }


def _normalize_question_number(text: str) -> str | None:
    match = ANCHOR_RE.match(text)
    if not match:
        return None
    return f"{match.group(1)}{match.group(2) or ''}".lower()


def _region_conflict(
    existing: list[StudentAnswerRegion], candidate: tuple[Decimal, Decimal, Decimal, Decimal]
) -> bool:
    x, y, width, height = candidate
    area = width * height
    for region in existing:
        left, top = max(x, region.x), max(y, region.y)
        right = min(x + width, region.x + region.width)
        bottom = min(y + height, region.y + region.height)
        intersection = max(Decimal(0), right - left) * max(Decimal(0), bottom - top)
        other_area = region.width * region.height
        if intersection and intersection / min(area, other_area) >= Decimal("0.65"):
            return True
    return False


def _ensure_answers(db: Session, submission: Submission, questions: list[Question]) -> None:
    existing = {
        answer.question_id
        for answer in db.scalars(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
        )
    }
    for question in questions:
        if question.id not in existing:
            db.add(
                StudentAnswer(
                    submission_id=submission.id,
                    question_id=question.id,
                    question_version_reference=str(question.paper_version_id),
                    status="manual_segmentation_required",
                    requires_review=True,
                )
            )
    db.flush()


def _segment(
    db: Session,
    job: SubmissionProcessingJob,
    submission: Submission,
    pages: list[SubmissionPage],
    blocks_by_page: dict[uuid.UUID, list[ProviderBlock]],
) -> None:
    assignment = db.get(Assignment, submission.assignment_id)
    if assignment is None or assignment.active_paper_version_id is None:
        raise RecognitionError("ASSIGNMENT_VERSION_MISSING", "作业没有有效试卷版本")
    questions = list(
        db.scalars(
            select(Question)
            .where(Question.paper_version_id == assignment.active_paper_version_id)
            .order_by(Question.display_order, Question.question_number)
        )
    )
    _ensure_answers(db, submission, questions)
    by_number = {question.question_number.strip().lower(): question for question in questions}
    answers = {
        answer.question_id: answer
        for answer in db.scalars(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
        )
    }
    db.execute(
        delete(StudentAnswerRegion).where(
            StudentAnswerRegion.student_answer_id.in_([answer.id for answer in answers.values()]),
            StudentAnswerRegion.source.in_(["ocr", "alignment"]),
            StudentAnswerRegion.status != "confirmed",
        )
    )
    db.execute(
        delete(SubmissionQuestionAnchor).where(
            SubmissionQuestionAnchor.submission_processing_job_id == job.id
        )
    )
    existing_regions = list(
        db.scalars(
            select(StudentAnswerRegion)
            .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
            .where(
                StudentAnswer.submission_id == submission.id,
                StudentAnswerRegion.status.in_(["confirmed", "candidate", "manual_required"]),
            )
        )
    )
    # Template regions are copied only after a high-confidence page alignment.
    # Page ordinal is never used as a question identity signal.
    for page in pages:
        if (
            page.aligned_paper_page_id is None
            or page.alignment_confidence is None
            or page.alignment_confidence < Decimal("0.85")
        ):
            continue
        for template, question in db.execute(
            select(QuestionRegion, Question)
            .join(Question, Question.id == QuestionRegion.question_id)
            .where(
                QuestionRegion.paper_page_id == page.aligned_paper_page_id,
                Question.paper_version_id == assignment.active_paper_version_id,
            )
        ).all():
            answer = answers[question.id]
            candidate = (template.x, template.y, template.width, template.height)
            if _region_conflict(existing_regions, candidate):
                continue
            region = StudentAnswerRegion(
                student_answer_id=answer.id,
                submission_page_id=page.id,
                x=template.x,
                y=template.y,
                width=template.width,
                height=template.height,
                source="alignment",
                confidence=page.alignment_confidence,
                status="candidate",
                reason="ALIGNED_STANDARD_REGION",
                segmentation_version=SEGMENTATION_VERSION,
            )
            db.add(region)
            existing_regions.append(region)
    for page in pages:
        anchors: list[tuple[ProviderBlock, str, Question, Decimal]] = []
        for index, block in enumerate(blocks_by_page.get(page.id, [])):
            text = getattr(block, "text", None) or ""
            normalized = _normalize_question_number(text)
            question = by_number.get(normalized or "")
            raw_confidence = Decimal(str(getattr(block, "confidence", None) or 0))
            # Header/footer and right-column tokens are deliberately down-weighted.
            x, y, width, height = (Decimal(str(value)) for value in block.region)
            confidence = raw_confidence
            if x > Decimal("0.45") or y > Decimal("0.92") or y < Decimal("0.025"):
                confidence *= Decimal("0.55")
            db.add(
                SubmissionQuestionAnchor(
                    submission_processing_job_id=job.id,
                    submission_page_id=page.id,
                    block_index=index,
                    text=text[:120],
                    normalized_number=normalized,
                    candidate_question_id=question.id if question else None,
                    confidence=confidence,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    rejection_reason=(
                        None
                        if question
                        else ("UNKNOWN_QUESTION_NUMBER" if normalized else "NOT_QUESTION_ANCHOR")
                    ),
                )
            )
            if question:
                anchors.append((block, normalized or "", question, confidence))
        anchors.sort(key=lambda item: item[0].region[1])
        for index, (block, _number, question, confidence) in enumerate(anchors):
            x, y, _width, _height = (Decimal(str(value)) for value in block.region)
            next_y = (
                Decimal(str(anchors[index + 1][0].region[1]))
                if index + 1 < len(anchors)
                else Decimal(1)
            )
            candidate = (
                max(Decimal(0), x - Decimal("0.02")),
                max(Decimal(0), y - Decimal("0.01")),
                min(
                    Decimal(1),
                    Decimal("0.98") - max(Decimal(0), x - Decimal("0.02")),
                ),
                max(
                    Decimal("0.02"),
                    min(Decimal(1), next_y) - max(Decimal(0), y - Decimal("0.01")),
                ),
            )
            answer = answers[question.id]
            status = "candidate" if confidence >= Decimal("0.80") else "manual_required"
            reason = "QUESTION_ANCHOR" if status == "candidate" else "LOW_ANCHOR_CONFIDENCE"
            if _region_conflict(existing_regions, candidate):
                status, reason = "manual_required", "HIGH_OVERLAP_CONFLICT"
            region = StudentAnswerRegion(
                student_answer_id=answer.id,
                submission_page_id=page.id,
                x=candidate[0],
                y=candidate[1],
                width=candidate[2],
                height=min(candidate[3], Decimal(1) - candidate[1]),
                source="ocr",
                confidence=confidence,
                status=status,
                reason=reason,
                segmentation_version=SEGMENTATION_VERSION,
            )
            db.add(region)
            existing_regions.append(region)
    # Never infer question identity from a page number. Missing questions remain explicit.
    for question in questions:
        answer = answers[question.id]
        has_region = any(region.student_answer_id == answer.id for region in existing_regions)
        if not has_region:
            answer.status = "manual_segmentation_required"
            answer.requires_review = True


def run_submission_processing(
    db: Session,
    storage: ObjectStorage,
    settings: Settings,
    job_id: uuid.UUID,
    only_page_id: uuid.UUID | None = None,
) -> None:
    job = db.get(SubmissionProcessingJob, job_id)
    if job is None or job.status == "cancelled":
        return
    submission = db.get(Submission, job.submission_id)
    if submission is None:
        return
    job.status, job.started_at, job.attempt = "running", now_utc(), job.attempt + 1
    db.commit()
    pages = list(
        db.scalars(
            select(SubmissionPage)
            .where(
                SubmissionPage.submission_id == submission.id,
                *((SubmissionPage.id == only_page_id,) if only_page_id is not None else ()),
            )
            .order_by(SubmissionPage.page_number)
        )
    )
    provider = provider_from_settings(settings)
    failures = 0
    blocks_by_page: dict[uuid.UUID, list[ProviderBlock]] = {}
    prior_hashes: list[tuple[uuid.UUID, str]] = []
    for index, page in enumerate(pages):
        try:
            page.processing_status = "running"
            db.commit()
            from app.models import StoredFile

            stored = db.get(StoredFile, page.stored_file_id)
            if stored is None or stored.owner_id != submission.owner_id:
                raise RecognitionError("SOURCE_FILE_NOT_FOUND", "原始文件不存在")
            rendered = DefaultDocumentConverter(settings).convert(
                read_all(storage.get(stored.storage_key)),
                stored.content_type,
                page.source_page_number or 1,
            )
            if page.rotation:
                source_image = Image.open(io.BytesIO(rendered.content)).convert("RGB")
                source_image = source_image.rotate(-page.rotation, expand=True, fillcolor="white")
                rendered = _artifact(source_image)
            processed, metrics = preprocess_page(rendered)
            if page.processed_storage_key is not None:
                page.page_version += 1
            prefix = f"submission-processing/{job.owner_id}/{page.id}/{PROCESSING_VERSION}"
            page.rendered_storage_key = f"{prefix}/original.png"
            page.processed_storage_key = f"{prefix}/processed.png"
            page.thumbnail_storage_key = f"{prefix}/thumbnail.png"
            store_artifact(storage, page.rendered_storage_key, rendered)
            store_artifact(storage, page.processed_storage_key, processed)
            thumb = Image.open(io.BytesIO(processed.content))
            thumb.thumbnail((360, 360))
            store_artifact(storage, page.thumbnail_storage_key, _artifact(thumb))
            page.width, page.height = rendered.width, rendered.height
            page.blur_score = Decimal(str(metrics["blur_score"]))
            page.brightness = Decimal(str(metrics["brightness"]))
            page.contrast = Decimal(str(metrics["contrast"]))
            page.blank_probability = Decimal(str(metrics["blank_probability"]))
            page.orientation_confidence = Decimal(str(metrics["orientation_confidence"]))
            if not page.rotation and metrics["rotation"]:
                page.rotation = int(cast(int, metrics["rotation"]))
            page.preprocessing_version = PROCESSING_VERSION
            page.quality_warnings = cast(list[str], metrics["warnings"])
            page.perceptual_hash = str(metrics["perceptual_hash"])
            page.processing_error_code = page.processing_error_message = None
            assignment = db.get(Assignment, submission.assignment_id)
            standard_pages = (
                list(
                    db.scalars(
                        select(PaperPage).where(
                            PaperPage.paper_version_id == assignment.active_paper_version_id
                        )
                    )
                )
                if assignment and assignment.active_paper_version_id
                else []
            )
            aspect = rendered.width / max(1, rendered.height)
            matches = [
                candidate
                for candidate in standard_pages
                if candidate.width
                and candidate.height
                and abs(candidate.width / candidate.height - aspect) <= 0.03
            ]
            if len(matches) == 1:
                matched = matches[0]
                matched_width = matched.width or rendered.width
                matched_height = matched.height or rendered.height
                page.aligned_paper_page_id = matched.id
                page.alignment_transform = {
                    "type": "scale",
                    "scale_x": rendered.width / matched_width,
                    "scale_y": rendered.height / matched_height,
                }
                page.alignment_confidence = (
                    Decimal("0.99") if provider.is_demo else Decimal("0.88")
                )
                page.alignment_failure_reason = None
            else:
                page.aligned_paper_page_id = None
                page.alignment_transform = None
                page.alignment_confidence = Decimal("0")
                page.alignment_failure_reason = (
                    "AMBIGUOUS_SIZE_MATCH" if matches else "NO_SIZE_MATCH"
                )
            duplicate = next(
                (
                    prior_id
                    for prior_id, prior_hash in prior_hashes
                    if _hash_distance(prior_hash, page.perceptual_hash) <= 3
                ),
                None,
            )
            page.duplicate_of_page_id = duplicate
            if duplicate:
                page.quality_warnings = [*page.quality_warnings, "DUPLICATE_PAGE"]
            prior_hashes.append((page.id, page.perceptual_hash))
            page.processing_status = (
                "blank" if page.blank_probability >= Decimal("0.95") else "completed"
            )
            blocks_by_page[page.id] = (
                []
                if page.processing_status == "blank" or provider.is_demo
                else provider.recognize(processed)
            )
            job.progress = math.floor((index + 1) * 70 / max(1, len(pages)))
            db.commit()
        except Exception as exc:
            db.rollback()
            failed = db.get(SubmissionPage, page.id)
            if failed:
                failed.processing_status = "failed"
                failed.processing_error_code = (
                    exc.code if isinstance(exc, RecognitionError) else "PAGE_PROCESSING_FAILED"
                )
                failed.processing_error_message = str(exc)[:500]
            failures += 1
            db.commit()
    if only_page_id is None:
        job.stage = "segmentation"
        db.commit()
        try:
            _segment(db, job, submission, pages, blocks_by_page)
            db.commit()
        except Exception as exc:
            db.rollback()
            failures = max(1, failures)
            job = db.get(SubmissionProcessingJob, job_id)
            if job:
                job.error_code = (
                    exc.code if isinstance(exc, RecognitionError) else "SEGMENTATION_FAILED"
                )
                job.error_message = str(exc)[:500]
            db.commit()
    job = db.get(SubmissionProcessingJob, job_id)
    if job:
        job.progress = 100
        job.completed_at = now_utc()
        job.status = (
            "partially_completed"
            if failures and failures < len(pages)
            else ("failed" if failures else "completed")
        )
        job.stage = "completed"
        db.commit()
