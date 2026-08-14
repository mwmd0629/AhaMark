import io
import math
import uuid
from collections import Counter
from decimal import Decimal
from typing import cast

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat
from pypdf import PdfReader
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
from app.recognition.question_numbers import normalize_question_number
from app.storage.base import ObjectStorage

PROCESSING_VERSION = "submission-processing-v3"
SEGMENTATION_VERSION = "submission-seg-v3"
CONTENT_PIXEL_CUTOFF = 245
CONTENT_BRIGHTNESS_LIMIT = 210.0
ANCHOR_TOP_PADDING = Decimal("0.01")
ANCHOR_BOTTOM_PADDING = Decimal("0.015")
ANCHOR_CONTENT_BOTTOM_LIMIT = Decimal("0.92")


def _artifact(image: Image.Image) -> PageArtifact:
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return PageArtifact(output.getvalue(), image.width, image.height)


def _anchor_region_candidate(
    block: ProviderBlock,
    next_anchor: ProviderBlock | None,
    page_blocks: list[ProviderBlock],
    fallback_span: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Keep the answer area when the PDF text layer exposes headings only."""
    x, y, _width, _height = (Decimal(str(value)) for value in block.region)
    top = max(Decimal(0), y - ANCHOR_TOP_PADDING)
    next_top = Decimal(str(next_anchor.region[1])) if next_anchor is not None else Decimal(1)
    content_bottoms: list[Decimal] = []
    for page_block in page_blocks:
        _block_x, block_y, _block_width, block_height = (
            Decimal(str(value)) for value in page_block.region
        )
        if (
            page_block is not block
            and block_y > y + Decimal("0.001")
            and block_y < next_top
            and block_y < ANCHOR_CONTENT_BOTTOM_LIMIT
        ):
            content_bottoms.append(block_y + block_height)
    if next_anchor is not None:
        # Some PDFs expose only question headings. In that case, ending at the
        # next heading is the only safe way to retain the unseen answer text.
        bottom = next_top - ANCHOR_TOP_PADDING
    elif content_bottoms:
        bottom = min(
            ANCHOR_CONTENT_BOTTOM_LIMIT,
            max(content_bottoms) + ANCHOR_BOTTOM_PADDING,
        )
    else:
        # For the final question, use the page's typical question spacing
        # instead of extending to the page footer or collapsing to the title.
        bottom = min(ANCHOR_CONTENT_BOTTOM_LIMIT, top + fallback_span)
    bottom = max(top + Decimal("0.02"), bottom)
    left = max(Decimal(0), x - Decimal("0.02"))
    right = Decimal("0.98")
    return left, top, right - left, min(bottom, Decimal(1)) - top


def _average_hash(image: Image.Image) -> str:
    small = ImageOps.grayscale(image).resize((16, 16))
    pixels = cast(list[int], list(small.get_flattened_data()))
    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= mean else "0" for pixel in pixels)
    return f"{int(bits, 2):064x}"


def _hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _content_brightness(image: Image.Image) -> float | None:
    """Measure likely ink/content without letting a sparse white page dominate."""
    histogram = image.histogram()
    content_count = sum(histogram[:CONTENT_PIXEL_CUTOFF])
    if content_count < 32:
        return None
    weighted_sum = sum(
        value * count for value, count in enumerate(histogram[:CONTENT_PIXEL_CUTOFF])
    )
    return weighted_sum / content_count


def _page_metrics(image: Image.Image) -> dict[str, float | None]:
    gray = ImageOps.grayscale(image)
    stat = ImageStat.Stat(gray)
    histogram = gray.histogram()
    pixel_count = sum(histogram)
    white_ratio = sum(histogram[CONTENT_PIXEL_CUTOFF:]) / pixel_count
    return {
        "brightness": float(stat.mean[0]),
        "contrast": float(stat.stddev[0]),
        "blank_probability": max(0.0, min(1.0, (white_ratio - 0.80) / 0.20)),
        "content_brightness": _content_brightness(gray),
    }


def _is_too_bright(metrics: dict[str, float | None]) -> bool:
    brightness = metrics["brightness"]
    blank_probability = metrics["blank_probability"]
    content_brightness = metrics["content_brightness"]
    return bool(
        brightness is not None
        and brightness > 245
        and blank_probability is not None
        and blank_probability < 0.95
        and content_brightness is not None
        and content_brightness > CONTENT_BRIGHTNESS_LIMIT
    )


def preprocess_page(rendered: PageArtifact) -> tuple[PageArtifact, dict[str, object]]:
    image = Image.open(io.BytesIO(rendered.content)).convert("RGB")
    detected_rotation = 0
    orientation_confidence = 0.0
    if image.width > image.height * 1.25:
        image = image.rotate(90, expand=True, fillcolor="white")
        detected_rotation = 90
        orientation_confidence = 0.75
    coordinate_space_size = image.size
    gray = ImageOps.grayscale(image)
    source_metrics = _page_metrics(gray)
    # RMS edge energy is deterministic, cheap, and useful as a blur warning signal.
    edges = gray.filter(ImageFilter.FIND_EDGES)
    blur_score = float(ImageStat.Stat(edges).rms[0])
    warnings: list[str] = []
    if blur_score < 8:
        warnings.append("LOW_SHARPNESS")
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
    processing_gray = ImageOps.grayscale(image)
    brightness_correction_applied = _is_too_bright(source_metrics)
    if brightness_correction_applied:
        # Stretch a genuinely washed-out content range before the normal mild
        # contrast pass. The original rendered artifact is still preserved.
        processing_gray = ImageOps.autocontrast(processing_gray)
    processed = ImageEnhance.Contrast(processing_gray).enhance(1.12)
    processed = processed.filter(ImageFilter.MedianFilter(3)).convert("RGB")
    if crop:
        restored = Image.new("RGB", coordinate_space_size, "white")
        restored.paste(processed, (crop["left"], crop["top"]))
        processed = restored
    processed_metrics = _page_metrics(processed)
    if processed_metrics["brightness"] is not None and processed_metrics["brightness"] < 55:
        warnings.append("TOO_DARK")
    if _is_too_bright(processed_metrics):
        warnings.append("TOO_BRIGHT")
    if (
        processed_metrics["contrast"] is not None
        and processed_metrics["contrast"] < 8
        and source_metrics["blank_probability"] is not None
        and source_metrics["blank_probability"] < 0.95
    ):
        warnings.append("LOW_CONTRAST")
    return _artifact(processed), {
        "blur_score": blur_score,
        "brightness": processed_metrics["brightness"],
        "contrast": processed_metrics["contrast"],
        "blank_probability": source_metrics["blank_probability"],
        "source_brightness": source_metrics["brightness"],
        "source_content_brightness": source_metrics["content_brightness"],
        "processed_content_brightness": processed_metrics["content_brightness"],
        "brightness_correction_applied": brightness_correction_applied,
        "orientation_confidence": orientation_confidence,
        "rotation": detected_rotation,
        "crop": crop,
        "warnings": warnings,
        "perceptual_hash": _average_hash(processed),
    }


def _normalize_question_number(text: str) -> str | None:
    return normalize_question_number(text)


def _pdf_text_blocks(content: bytes, source_page: int) -> list[ProviderBlock]:
    """Extract trustworthy question anchors from a PDF text layer.

    The coordinates are normalized into the same top-left 0–1 space used by
    OCR providers. Malformed or image-only PDFs simply provide no blocks and
    continue through the configured OCR path.
    """

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if source_page < 1 or source_page > len(reader.pages):
            return []
        page = reader.pages[source_page - 1]
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if width <= 0 or height <= 0:
            return []
        blocks: list[ProviderBlock] = []

        def visit(
            text: str,
            current_matrix: list[float],
            text_matrix: list[float],
            _font: object,
            font_size: float,
        ) -> None:
            clean = text.strip()
            if not clean or _normalize_question_number(clean) is None:
                return
            a, b, c, d, e, f = (float(value) for value in current_matrix)
            tx, ty = float(text_matrix[4]), float(text_matrix[5])
            baseline_x = tx * a + ty * c + e
            baseline_y = tx * b + ty * d + f
            scaled_height = max(1.0, abs(float(font_size)) * math.hypot(c, d))
            left = max(0.0, min(1.0, baseline_x / width))
            top = max(0.0, min(1.0, 1 - (baseline_y + scaled_height) / height))
            block_height = max(0.005, min(1 - top, scaled_height * 1.5 / height))
            estimated_width = len(clean) * max(1.0, abs(float(font_size))) * 0.65 / width
            block_width = max(0.01, min(1 - left, estimated_width))
            if block_width > 0 and block_height > 0:
                blocks.append(
                    ProviderBlock(
                        "pdf_text",
                        clean[:120],
                        None,
                        0.99,
                        (left, top, block_width, block_height),
                    )
                )

        page.extract_text(visitor_text=visit)
        return blocks
    except Exception:
        return []


def _region_conflict(
    existing: list[StudentAnswerRegion],
    submission_page_id: uuid.UUID,
    candidate: tuple[Decimal, Decimal, Decimal, Decimal],
) -> bool:
    x, y, width, height = candidate
    area = width * height
    for region in existing:
        if region.submission_page_id != submission_page_id:
            continue
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
            .where(
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == "active",
            )
            .order_by(Question.display_order, Question.question_number)
        )
    )
    _ensure_answers(db, submission, questions)
    normalized_questions = [
        (question, normalize_question_number(question.question_number)) for question in questions
    ]
    number_counts = Counter(number for _question, number in normalized_questions if number)
    by_number = {
        number: question
        for question, number in normalized_questions
        if number is not None and number_counts[number] == 1
    }
    question_order = {question.id: index for index, question in enumerate(questions)}
    answers = {
        answer.question_id: answer
        for answer in db.scalars(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
        )
    }
    prior_system_regions = list(
        db.scalars(
            select(StudentAnswerRegion).where(
                StudentAnswerRegion.student_answer_id.in_(
                    [answer.id for answer in answers.values()]
                ),
                StudentAnswerRegion.source.in_(["ocr", "alignment"]),
                StudentAnswerRegion.status == "confirmed",
                StudentAnswerRegion.confirmation_origin == "system_auto",
            )
        )
    )
    for region in prior_system_regions:
        region.status = "superseded"
        region.region_version += 1
    db.flush()
    db.execute(
        delete(StudentAnswerRegion).where(
            StudentAnswerRegion.student_answer_id.in_([answer.id for answer in answers.values()]),
            StudentAnswerRegion.source.in_(["ocr", "alignment"]),
            StudentAnswerRegion.status.in_(["candidate", "manual_required"]),
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
    teacher_confirmed_answer_ids = {
        region.student_answer_id
        for region in existing_regions
        if region.status == "confirmed" and region.confirmation_origin != "system_auto"
    }
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
                Question.status == "active",
            )
        ).all():
            answer = answers[question.id]
            if answer.id in teacher_confirmed_answer_ids:
                continue
            candidate = (template.x, template.y, template.width, template.height)
            if _region_conflict(existing_regions, page.id, candidate):
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
    discovered: list[
        tuple[SubmissionPage, ProviderBlock, SubmissionQuestionAnchor, Question, Decimal]
    ] = []
    for page in pages:
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
            actionable = question is not None and confidence >= Decimal("0.80")
            anchor = SubmissionQuestionAnchor(
                submission_processing_job_id=job.id,
                submission_page_id=page.id,
                block_index=index,
                text=text[:120],
                normalized_number=normalized,
                candidate_question_id=question.id if question else None,
                confidence=confidence,
                source_kind="pdf_text" if block.block_type == "pdf_text" else "ocr",
                page_version=page.page_version,
                x=x,
                y=y,
                width=width,
                height=height,
                rejection_reason=(
                    None
                    if actionable
                    else (
                        "LOW_ANCHOR_CONFIDENCE"
                        if question
                        else (
                            "AMBIGUOUS_QUESTION_NUMBER"
                            if normalized and number_counts[normalized] > 1
                            else (
                                "UNKNOWN_QUESTION_NUMBER" if normalized else "NOT_QUESTION_ANCHOR"
                            )
                        )
                    )
                ),
            )
            db.add(anchor)
            if actionable and question is not None:
                discovered.append((page, block, anchor, question, confidence))
    db.flush()
    occurrence_counts = Counter(question.id for _, _, _, question, _ in discovered)
    ordered_discovered = sorted(
        discovered,
        key=lambda item: (item[0].page_number, item[1].region[1], item[1].region[0]),
    )
    sequence = [question_order[question.id] for _, _, _, question, _ in ordered_discovered]
    sequence_valid = all(left < right for left, right in zip(sequence, sequence[1:], strict=False))
    by_page: dict[
        uuid.UUID,
        list[tuple[SubmissionPage, ProviderBlock, SubmissionQuestionAnchor, Question, Decimal]],
    ] = {}
    for item in ordered_discovered:
        by_page.setdefault(item[0].id, []).append(item)
    for page in pages:
        anchors = by_page.get(page.id, [])
        anchor_tops = [Decimal(str(item[1].region[1])) for item in anchors]
        anchor_gaps = [
            right - left for left, right in zip(anchor_tops, anchor_tops[1:], strict=False)
        ]
        fallback_span = (
            sorted(anchor_gaps)[len(anchor_gaps) // 2] if anchor_gaps else Decimal("0.15")
        )
        fallback_span = max(Decimal("0.05"), min(Decimal("0.25"), fallback_span))
        for index, (_page, block, anchor, question, confidence) in enumerate(anchors):
            next_anchor = anchors[index + 1][1] if index + 1 < len(anchors) else None
            candidate = _anchor_region_candidate(
                block,
                next_anchor,
                blocks_by_page.get(page.id, []),
                fallback_span,
            )
            answer = answers[question.id]
            if answer.id in teacher_confirmed_answer_ids:
                continue
            if occurrence_counts[question.id] != 1:
                status, reason = "manual_required", "DUPLICATE_QUESTION_ANCHOR"
            elif not sequence_valid:
                status, reason = "manual_required", "OUT_OF_ORDER_QUESTION_ANCHOR"
            else:
                status, reason = "candidate", "QUESTION_ANCHOR"
            if _region_conflict(existing_regions, page.id, candidate):
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
                source_question_anchor_id=anchor.id,
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
            source_content = read_all(storage.get(stored.storage_key))
            source_page = page.source_page_number or 1
            rendered = DefaultDocumentConverter(settings).convert(
                source_content,
                stored.content_type,
                source_page,
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
                page.alignment_confidence = Decimal("0.99") if provider.is_demo else Decimal("0.88")
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
            embedded_blocks = (
                _pdf_text_blocks(source_content, source_page)
                if stored.content_type == "application/pdf"
                else []
            )
            blocks_by_page[page.id] = (
                []
                if page.processing_status == "blank"
                else (
                    embedded_blocks
                    if embedded_blocks
                    else ([] if provider.is_demo else provider.recognize(processed))
                )
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
        if job.status == "completed" and only_page_id is None:
            # A complete, one-to-one, current anchor set can safely advance
            # without asking the teacher to redraw deterministic regions.
            from app.processing.automatic_confirmation import (
                auto_confirm_deterministic_regions,
            )

            auto_confirm_deterministic_regions(
                db,
                owner_id=job.owner_id,
                submission_id=submission.id,
                processing_job_id=job.id,
                processing_run_id=None,
            )
            db.commit()
