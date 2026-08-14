from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.math_validation.stale import stale_for_answer
from app.models import (
    Assignment,
    AuditLog,
    Question,
    QuestionRecognitionEvidence,
    QuestionRegion,
    QuestionStatus,
    RecognitionRevision,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionProcessingJob,
    SubmissionQuestionAnchor,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    now_utc,
)
from app.recognition.answer_evidence import next_revision

AUTOMATIC_CONFIRMATION_VERSION = "strict-auto-confirm-v3"
REGION_MIN_CONFIDENCE = Decimal("0.95")


@dataclass(frozen=True)
class AutomaticConfirmationDecision:
    eligible: bool
    code: str | None = None
    message: str | None = None
    changed_count: int = 0


def _blocked(code: str, message: str) -> AutomaticConfirmationDecision:
    return AutomaticConfirmationDecision(False, code, message)


def _anchored_regions_are_disjoint(
    candidates: list[
        tuple[
            StudentAnswer,
            StudentAnswerRegion,
            SubmissionQuestionAnchor,
            SubmissionPage,
        ]
    ],
) -> bool:
    ordered = sorted(
        candidates,
        key=lambda item: (item[3].page_number, item[1].y, item[1].x),
    )
    for left, right in pairwise(ordered):
        left_region, left_page = left[1], left[3]
        right_region, right_page = right[1], right[3]
        if left_page.id == right_page.id and (left_region.y + left_region.height > right_region.y):
            return False
    return True


def auto_confirm_deterministic_regions(
    db: Session,
    *,
    owner_id: uuid.UUID,
    submission_id: uuid.UUID,
    processing_job_id: uuid.UUID,
    processing_run_id: uuid.UUID | None,
) -> AutomaticConfirmationDecision:
    """Confirm only complete, uniquely mapped and non-overlapping regions."""

    submission = db.scalar(
        select(Submission)
        .where(Submission.id == submission_id, Submission.owner_id == owner_id)
        .with_for_update()
    )
    job = db.scalar(
        select(SubmissionProcessingJob)
        .where(
            SubmissionProcessingJob.id == processing_job_id,
            SubmissionProcessingJob.submission_id == submission_id,
            SubmissionProcessingJob.owner_id == owner_id,
        )
        .with_for_update()
    )
    if (
        submission is None
        or job is None
        or submission.finalized_at is not None
        or submission.status in {"finalized", "merged", "voided"}
        or job.status != "completed"
    ):
        return _blocked(
            "SUBMISSION_PROCESSING_NOT_CURRENT",
            "The current submission processing job is not safely confirmable",
        )
    latest_job_id = db.scalar(
        select(SubmissionProcessingJob.id)
        .where(SubmissionProcessingJob.submission_id == submission_id)
        .order_by(SubmissionProcessingJob.created_at.desc(), SubmissionProcessingJob.id)
        .limit(1)
    )
    if latest_job_id != job.id:
        return _blocked(
            "SUBMISSION_PROCESSING_STALE",
            "A newer submission processing job exists",
        )
    assignment = db.get(Assignment, submission.assignment_id)
    if assignment is None or assignment.active_paper_version_id is None:
        return _blocked("ACTIVE_PAPER_REQUIRED", "The active paper is unavailable")
    answers = list(
        db.scalars(
            select(StudentAnswer)
            .join(Question, Question.id == StudentAnswer.question_id)
            .where(
                StudentAnswer.submission_id == submission.id,
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == "active",
            )
            .order_by(StudentAnswer.id)
            .with_for_update()
        )
    )
    if not answers:
        return _blocked("STUDENT_ANSWERS_REQUIRED", "No current answers were materialized")
    answer_ids = [answer.id for answer in answers]
    regions = list(
        db.scalars(
            select(StudentAnswerRegion)
            .where(StudentAnswerRegion.student_answer_id.in_(answer_ids))
            .order_by(StudentAnswerRegion.student_answer_id, StudentAnswerRegion.id)
            .with_for_update()
        )
    )
    by_answer: dict[uuid.UUID, list[StudentAnswerRegion]] = {}
    for region in regions:
        if region.status not in {"confirmed", "rejected", "stale", "superseded", "candidate"}:
            return _blocked(
                "SEGMENTATION_REVIEW_REQUIRED",
                "A segmentation region requires manual review",
            )
        if region.status not in {"rejected", "stale", "superseded"}:
            by_answer.setdefault(region.student_answer_id, []).append(region)
    teacher_precedence: list[dict[str, object]] = []
    teacher_superseded_count = 0
    for answer in answers:
        current = by_answer.get(answer.id, [])
        teacher_regions = [
            region
            for region in current
            if region.status == "confirmed" and region.confirmation_origin == "teacher_explicit"
        ]
        if len(current) <= 1 or not teacher_regions:
            continue
        winner = max(teacher_regions, key=lambda item: (item.created_at, item.id))
        superseded_ids: list[str] = []
        for region in current:
            if region.id == winner.id:
                continue
            region.status = "superseded"
            region.region_version += 1
            superseded_ids.append(str(region.id))
            teacher_superseded_count += 1
        by_answer[answer.id] = [winner]
        teacher_precedence.append(
            {
                "answer_id": str(answer.id),
                "winner_region_id": str(winner.id),
                "superseded_region_ids": superseded_ids,
            }
        )
    if teacher_precedence:
        audit_resource_type = (
            "processing_run" if processing_run_id is not None else "submission_processing_job"
        )
        db.add(
            AuditLog(
                actor_id=owner_id,
                action="processing.segmentation.teacher_region_precedence",
                resource_type=audit_resource_type,
                resource_id=str(processing_run_id or processing_job_id),
                metadata_={
                    "version": AUTOMATIC_CONFIRMATION_VERSION,
                    "submission_id": str(submission_id),
                    "processing_job_id": str(processing_job_id),
                    "reconciled_answers": teacher_precedence,
                },
            )
        )
        db.flush()
    if any(len(by_answer.get(answer.id, [])) != 1 for answer in answers):
        return _blocked(
            "SEGMENTATION_AMBIGUOUS",
            "Every answer must have exactly one current region",
        )

    candidates: list[tuple[StudentAnswer, StudentAnswerRegion]] = []
    anchored_candidates: list[
        tuple[StudentAnswer, StudentAnswerRegion, SubmissionQuestionAnchor, SubmissionPage]
    ] = []
    for answer in answers:
        region = by_answer[answer.id][0]
        if region.status == "confirmed":
            continue
        if region.confidence is None or Decimal(region.confidence) < REGION_MIN_CONFIDENCE:
            return _blocked(
                "SEGMENTATION_NOT_DETERMINISTIC",
                "The answer region is not a high-confidence template mapping",
            )
        page = db.get(SubmissionPage, region.submission_page_id)
        if page is None or page.submission_id != submission.id:
            return _blocked(
                "SEGMENTATION_PAGE_AMBIGUOUS",
                "The source page is not deterministically aligned",
            )
        if region.source == "ocr" and region.reason == "QUESTION_ANCHOR":
            anchor = (
                db.get(SubmissionQuestionAnchor, region.source_question_anchor_id)
                if region.source_question_anchor_id is not None
                else None
            )
            if (
                anchor is None
                or anchor.submission_processing_job_id != job.id
                or anchor.submission_page_id != page.id
                or anchor.candidate_question_id != answer.question_id
                or anchor.rejection_reason is not None
                or anchor.source_kind not in {"pdf_text", "ocr"}
                or anchor.page_version != page.page_version
                or Decimal(anchor.confidence) != Decimal(region.confidence)
                or region.region_version != 1
                or not (region.x <= anchor.x < region.x + region.width)
                or not (region.y <= anchor.y < region.y + region.height)
                or page.processing_error_code is not None
                or bool(page.quality_warnings)
                or page.processing_status != "completed"
                or page.preprocessing_version != job.config_version
            ):
                return _blocked(
                    "SEGMENTATION_ANCHOR_STALE",
                    "The question anchor is missing, ambiguous, or stale",
                )
            anchored_candidates.append((answer, region, anchor, page))
            candidates.append((answer, region))
            continue
        if region.source not in {"alignment", "template"} or region.reason != (
            "ALIGNED_STANDARD_REGION"
        ):
            return _blocked(
                "SEGMENTATION_NOT_DETERMINISTIC",
                "The answer region is not a high-confidence deterministic mapping",
            )
        if (
            page.processing_error_code is not None
            or bool(page.quality_warnings)
            or page.aligned_paper_page_id is None
            or page.alignment_failure_reason is not None
            or page.alignment_confidence is None
            or Decimal(page.alignment_confidence) < REGION_MIN_CONFIDENCE
        ):
            return _blocked(
                "SEGMENTATION_PAGE_AMBIGUOUS",
                "The source page is not deterministically aligned",
            )
        matching_templates = list(
            db.scalars(
                select(QuestionRegion).where(
                    QuestionRegion.question_id == answer.question_id,
                    QuestionRegion.paper_page_id == page.aligned_paper_page_id,
                    QuestionRegion.x == region.x,
                    QuestionRegion.y == region.y,
                    QuestionRegion.width == region.width,
                    QuestionRegion.height == region.height,
                )
            )
        )
        if len(matching_templates) != 1:
            return _blocked(
                "SEGMENTATION_TEMPLATE_MISMATCH",
                "The answer region does not match one exact current template",
            )
        candidates.append((answer, region))

    if anchored_candidates:
        if len(anchored_candidates) != len(answers):
            return _blocked(
                "SEGMENTATION_ANCHOR_INCOMPLETE",
                "Anchor confirmation requires one current anchor for every active answer",
            )
        anchor_ids = {anchor.id for _, _, anchor, _ in anchored_candidates}
        if len(anchor_ids) != len(anchored_candidates):
            return _blocked(
                "SEGMENTATION_ANCHOR_AMBIGUOUS",
                "Question anchors must map one-to-one to active answers",
            )
        actual_order = [
            answer.question_id
            for answer, _, _, _ in sorted(
                anchored_candidates,
                key=lambda item: (item[3].page_number, item[2].y, item[2].x),
            )
        ]
        expected_order = list(
            db.scalars(
                select(Question.id)
                .where(
                    Question.paper_version_id == assignment.active_paper_version_id,
                    Question.status == "active",
                )
                .order_by(Question.display_order, Question.question_number, Question.id)
            )
        )
        if actual_order != expected_order:
            return _blocked(
                "SEGMENTATION_ANCHOR_ORDER_AMBIGUOUS",
                "Question anchors do not form the complete active-question order",
            )
        if not _anchored_regions_are_disjoint(anchored_candidates):
            return _blocked(
                "SEGMENTATION_REGION_OVERLAP",
                "Question regions overlap and require teacher review",
            )

    timestamp = now_utc()
    for answer, region in candidates:
        region.status = "confirmed"
        region.confirmed_by = owner_id
        region.confirmed_at = timestamp
        region.confirmation_origin = "system_auto"
        answer.status = "segmented"
        answer.requires_review = False
    if candidates:
        audit_resource_type = (
            "processing_run" if processing_run_id is not None else "submission_processing_job"
        )
        audit_resource_id = processing_run_id or processing_job_id
        db.add(
            AuditLog(
                actor_id=owner_id,
                action="processing.segmentation.auto_confirm",
                resource_type=audit_resource_type,
                resource_id=str(audit_resource_id),
                metadata_={
                    "version": AUTOMATIC_CONFIRMATION_VERSION,
                    "submission_id": str(submission_id),
                    "processing_job_id": str(processing_job_id),
                    "processing_run_id": (
                        str(processing_run_id) if processing_run_id is not None else None
                    ),
                    "region_ids": [str(region.id) for _, region in candidates],
                    "confirmation_origin": "system_auto",
                },
            )
        )
        db.flush()
    return AutomaticConfirmationDecision(
        True,
        changed_count=len(candidates) + teacher_superseded_count,
    )


def auto_confirm_deterministic_recognition(
    db: Session,
    *,
    owner_id: uuid.UUID,
    submission_id: uuid.UUID,
    recognition_job_id: uuid.UUID,
    processing_run_id: uuid.UUID,
    min_confidence: Decimal,
) -> AutomaticConfirmationDecision:
    submission = db.scalar(
        select(Submission)
        .where(Submission.id == submission_id, Submission.owner_id == owner_id)
        .with_for_update()
    )
    job = db.scalar(
        select(SubmissionRecognitionJob)
        .where(
            SubmissionRecognitionJob.id == recognition_job_id,
            SubmissionRecognitionJob.submission_id == submission_id,
            SubmissionRecognitionJob.owner_id == owner_id,
        )
        .with_for_update()
    )
    if (
        submission is None
        or job is None
        or submission.finalized_at is not None
        or submission.status in {"finalized", "merged", "voided"}
        or job.status != "completed"
        or bool(job.warning_codes)
    ):
        return _blocked(
            "RECOGNITION_REVIEW_REQUIRED",
            "The recognition job is incomplete or contains warnings",
        )
    latest_job_id = db.scalar(
        select(SubmissionRecognitionJob.id)
        .where(SubmissionRecognitionJob.submission_id == submission_id)
        .order_by(SubmissionRecognitionJob.generation.desc(), SubmissionRecognitionJob.id)
        .limit(1)
    )
    if latest_job_id != job.id:
        return _blocked("RECOGNITION_INPUT_STALE", "A newer recognition generation exists")
    assignment = db.get(Assignment, submission.assignment_id)
    if assignment is None or assignment.active_paper_version_id is None:
        return _blocked("ACTIVE_PAPER_REQUIRED", "The active paper is unavailable")
    answers = list(
        db.scalars(
            select(StudentAnswer)
            .join(Question, Question.id == StudentAnswer.question_id)
            .where(
                StudentAnswer.submission_id == submission_id,
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == QuestionStatus.active,
            )
            .order_by(StudentAnswer.id)
            .with_for_update()
        )
    )
    if not answers:
        return _blocked("STUDENT_ANSWERS_REQUIRED", "No answers are available")
    evidence_rows: list[tuple[StudentAnswer, QuestionRecognitionEvidence]] = []
    for answer in answers:
        current_evidence = list(
            db.scalars(
                select(QuestionRecognitionEvidence)
                .where(
                    QuestionRecognitionEvidence.student_answer_id == answer.id,
                    QuestionRecognitionEvidence.recognition_job_id == job.id,
                    QuestionRecognitionEvidence.stale_at.is_(None),
                )
                .order_by(QuestionRecognitionEvidence.recognition_version.desc())
                .limit(2)
                .with_for_update()
            )
        )
        if len(current_evidence) != 1:
            return _blocked(
                "RECOGNITION_EVIDENCE_AMBIGUOUS",
                "Every answer requires exactly one current recognition evidence row",
            )
        evidence = current_evidence[0]
        if evidence.status == "confirmed" and not evidence.requires_review:
            evidence_rows.append((answer, evidence))
            continue
        if (
            evidence.status != "recognized"
            or evidence.requires_review
            or not evidence.block_sources
        ):
            return _blocked(
                "RECOGNITION_EVIDENCE_REVIEW_REQUIRED",
                "Recognition evidence is not deterministically confirmable",
            )
        evidence_rows.append((answer, evidence))

    all_evidence_confirmed = all(
        evidence.status == "confirmed" and not evidence.requires_review
        for _, evidence in evidence_rows
    )
    effective_min_confidence = max(min_confidence, REGION_MIN_CONFIDENCE)

    region_rows = list(
        db.scalars(
            select(StudentAnswerRegion)
            .where(
                StudentAnswerRegion.student_answer_id.in_(
                    [answer.id for answer, _ in evidence_rows]
                ),
                StudentAnswerRegion.status == "confirmed",
            )
            .with_for_update()
        )
    )
    region_answer = {region.id: region.student_answer_id for region in region_rows}
    region_counts = {
        answer.id: sum(region.student_answer_id == answer.id for region in region_rows)
        for answer, _ in evidence_rows
    }
    if any(count != 1 for count in region_counts.values()):
        return _blocked(
            "RECOGNITION_REGION_AMBIGUOUS",
            "Every answer requires exactly one confirmed current region",
        )
    current_blocks = list(
        db.scalars(
            select(SubmissionRecognitionBlock)
            .where(
                SubmissionRecognitionBlock.submission_recognition_job_id == job.id,
                SubmissionRecognitionBlock.stale_at.is_(None),
            )
            .with_for_update()
        )
    )
    blocks_by_answer: dict[uuid.UUID, dict[uuid.UUID, SubmissionRecognitionBlock]] = {
        answer.id: {} for answer, _ in evidence_rows
    }
    for block in current_blocks:
        region_id = block.student_answer_region_id
        answer_id = region_answer.get(region_id) if region_id is not None else None
        if answer_id is None:
            return _blocked(
                "RECOGNITION_BLOCK_SCOPE_AMBIGUOUS",
                "A current recognition block is outside the current answer regions",
            )
        blocks_by_answer[answer_id][block.id] = block

    blocks_by_id: dict[uuid.UUID, SubmissionRecognitionBlock] = {}
    for answer, evidence in evidence_rows:
        referenced_ids: set[uuid.UUID] = set()
        for source in evidence.block_sources:
            try:
                block_id = uuid.UUID(str(source["block_id"]))
            except (KeyError, TypeError, ValueError):
                return _blocked(
                    "RECOGNITION_EVIDENCE_MALFORMED",
                    "Recognition evidence contains an invalid block source",
                )
            referenced_ids.add(block_id)
        if len(referenced_ids) != len(evidence.block_sources):
            return _blocked(
                "RECOGNITION_EVIDENCE_MALFORMED",
                "Recognition evidence contains duplicate block sources",
            )
        expected_blocks = blocks_by_answer[answer.id]
        if referenced_ids != set(expected_blocks):
            return _blocked(
                "RECOGNITION_BLOCK_COVERAGE_MISMATCH",
                "Recognition evidence must cover exactly the current blocks for its answer",
            )
        for block_id in referenced_ids:
            block = expected_blocks[block_id]
            if (
                block.submission_recognition_job_id != job.id
                or block.stale_at is not None
                or block.status not in {"recognized", "confirmed"}
                or block.requires_review
                or block.block_type == "unknown"
                or bool(block.warning_codes)
                or block.confidence is None
                or Decimal(block.confidence) < effective_min_confidence
                or block.input_hash is None
                or block.output_hash is None
            ):
                return _blocked(
                    "RECOGNITION_BLOCK_REVIEW_REQUIRED",
                    "A recognition block is ambiguous, stale, or low confidence",
                )
            has_human_revision = db.scalar(
                select(RecognitionRevision.id)
                .where(
                    RecognitionRevision.recognition_block_id == block.id,
                    RecognitionRevision.source == "human",
                    RecognitionRevision.stale_at.is_(None),
                )
                .limit(1)
            )
            if has_human_revision is not None:
                return _blocked(
                    "RECOGNITION_HUMAN_EDIT_PRESENT",
                    "Human-edited recognition must be explicitly reviewed",
                )
            blocks_by_id[block.id] = block

    if all_evidence_confirmed:
        return AutomaticConfirmationDecision(True)

    changed_evidence = [
        (answer, evidence)
        for answer, evidence in evidence_rows
        if evidence.status != "confirmed" or evidence.requires_review
    ]
    timestamp = now_utc()
    for answer, evidence in changed_evidence:
        evidence.status = "confirmed"
        evidence.requires_review = False
        evidence.confirmed_at = timestamp
        evidence.confirmed_by = owner_id
        evidence.confirmed_revision = (evidence.confirmed_revision or 0) + 1
        evidence.confirmation_origin = "system_auto"
        answer.status = "recognition_confirmed"
        answer.requires_review = False
        stale_for_answer(db, answer.id, "RECOGNITION_CONFIRMATION_CHANGED")
    for block in blocks_by_id.values():
        block.status = "confirmed"
        block.requires_review = False
        block.confirmed_at = timestamp
        block.confirmed_by = owner_id
        db.add(
            RecognitionRevision(
                recognition_block_id=block.id,
                revision=next_revision(db, block.id),
                source="system_auto",
                raw_text=block.text,
                normalized_text=block.normalized_text,
                latex=block.latex,
                warning_codes=block.warning_codes,
                editor_id=owner_id,
                base_recognition_version=block.recognition_version,
                confirmed=True,
            )
        )
    if changed_evidence:
        db.add(
            AuditLog(
                actor_id=owner_id,
                action="processing.recognition.auto_confirm",
                resource_type="processing_run",
                resource_id=str(processing_run_id),
                metadata_={
                    "version": AUTOMATIC_CONFIRMATION_VERSION,
                    "submission_id": str(submission_id),
                    "recognition_job_id": str(recognition_job_id),
                    "evidence_ids": [str(evidence.id) for _, evidence in changed_evidence],
                    "confirmation_origin": "system_auto",
                },
            )
        )
        db.flush()
    return AutomaticConfirmationDecision(
        True,
        changed_count=len(changed_evidence),
    )
