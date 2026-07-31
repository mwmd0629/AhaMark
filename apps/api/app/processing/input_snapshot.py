from __future__ import annotations

import uuid
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.assignment_central_review import (
    _answer_content_payload,
    _rubric_content_payload,
    validate_current_projection_under_locks,
)
from app.models import (
    Assignment,
    AssignmentReviewSession,
    AssignmentStatus,
    GradingBatch,
    PaperVersion,
    Question,
    QuestionRecognitionEvidence,
    QuestionStatus,
    RecognitionRevision,
    ReferenceAnswerVersion,
    RegionEvidenceImage,
    RubricCriterion,
    StructuredRubricVersion,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    VersionStatus,
)
from app.processing.contracts import (
    PROCESSING_INPUT_SCHEMA,
    ProcessingInputError,
    ProcessingInputSnapshot,
    canonical_hash,
)
from app.semantic_content import semantic_hash


def _fail(code: str, message: str) -> NoReturn:
    raise ProcessingInputError(code, message)


def _current_question_version(question: Question) -> str:
    return f"{question.paper_version_id}:{question.updated_at.isoformat()}"


def _latest_confirmed_formals(
    db: Session, question: Question
) -> tuple[ReferenceAnswerVersion, StructuredRubricVersion, list[RubricCriterion]]:
    rubric = db.scalar(
        select(StructuredRubricVersion)
        .where(
            StructuredRubricVersion.question_id == question.id,
            StructuredRubricVersion.status == VersionStatus.confirmed,
        )
        .order_by(
            StructuredRubricVersion.rubric_version.desc(),
            StructuredRubricVersion.id,
        )
    )
    answer = (
        db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
        if rubric is not None
        else None
    )
    if (
        answer is None
        or rubric is None
        or answer.status != VersionStatus.confirmed
        or answer.question_id != question.id
        or rubric.question_version != _current_question_version(question)
    ):
        _fail(
            "ACTIVE_CONFIRMED_FORMAL_REQUIRED",
            "Current confirmed reference answer and linked structured rubric are required",
        )
    criteria = list(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == rubric.id)
            .order_by(RubricCriterion.display_order, RubricCriterion.id)
        )
    )
    if not criteria:
        _fail("ACTIVE_CONFIRMED_FORMAL_REQUIRED", "Confirmed rubric criteria are required")
    return answer, rubric, criteria


def _current_evidence(
    db: Session,
    *,
    owner_id: uuid.UUID,
    submission: Submission,
    answer: StudentAnswer,
) -> tuple[QuestionRecognitionEvidence, list[dict[str, Any]]]:
    evidence = db.scalar(
        select(QuestionRecognitionEvidence)
        .where(
            QuestionRecognitionEvidence.student_answer_id == answer.id,
            QuestionRecognitionEvidence.status == "confirmed",
            QuestionRecognitionEvidence.stale_at.is_(None),
        )
        .order_by(
            QuestionRecognitionEvidence.recognition_version.desc(),
            QuestionRecognitionEvidence.id,
        )
    )
    if (
        evidence is None
        or evidence.owner_id != owner_id
        or evidence.submission_id != submission.id
        or evidence.confirmed_revision is None
        or evidence.confirmed_revision < 1
        or not evidence.block_sources
    ):
        _fail(
            "RECOGNITION_EVIDENCE_NOT_CONFIRMED",
            "Current confirmed recognition evidence is required",
        )

    all_regions = list(
        db.scalars(
            select(StudentAnswerRegion)
            .where(StudentAnswerRegion.student_answer_id == answer.id)
            .order_by(StudentAnswerRegion.id)
        )
    )
    pending_regions = [
        region
        for region in all_regions
        if region.status not in {"confirmed", "rejected", "stale", "superseded"}
    ]
    if pending_regions:
        _fail(
            "SEGMENTATION_CONFIRMATION_REQUIRED",
            "Every current answer region must be confirmed or explicitly retired",
        )
    regions = [region for region in all_regions if region.status == "confirmed"]
    if not regions:
        _fail(
            "RECOGNITION_EVIDENCE_NOT_CONFIRMED",
            "At least one confirmed answer region is required",
        )
    regions_by_id = {str(region.id): region for region in regions}
    seen_regions: set[str] = set()
    seen_blocks: set[uuid.UUID] = set()
    seen_sources: set[str] = set()
    result: list[dict[str, Any]] = []
    for source in evidence.block_sources:
        if not isinstance(source, dict):
            _fail("PROCESSING_INPUT_STALE", "Recognition block source is malformed")
        region = regions_by_id.get(str(source.get("region_id")))
        try:
            block_id = uuid.UUID(str(source.get("block_id")))
        except (TypeError, ValueError):
            _fail("PROCESSING_INPUT_STALE", "Recognition block identity is malformed")
        try:
            source_job_id = uuid.UUID(str(source.get("block_recognition_job_id")))
        except (TypeError, ValueError):
            _fail("PROCESSING_INPUT_STALE", "Recognition job identity is malformed")
        region_id = str(region.id) if region is not None else ""
        source_fingerprint = canonical_hash(source)
        if block_id in seen_blocks or source_fingerprint in seen_sources:
            _fail("PROCESSING_INPUT_STALE", "Recognition block sources must be unique")
        block = db.get(SubmissionRecognitionBlock, block_id)
        page = db.get(SubmissionPage, region.submission_page_id) if region else None
        image = (
            db.get(RegionEvidenceImage, block.region_evidence_image_id)
            if block is not None and block.region_evidence_image_id is not None
            else None
        )
        revision = (
            db.scalar(
                select(RecognitionRevision)
                .where(RecognitionRevision.recognition_block_id == block_id)
                .order_by(RecognitionRevision.revision.desc(), RecognitionRevision.id)
            )
            if block is not None
            else None
        )
        job = db.get(SubmissionRecognitionJob, evidence.recognition_job_id)
        current_job = db.scalar(
            select(SubmissionRecognitionJob)
            .where(
                SubmissionRecognitionJob.owner_id == owner_id,
                SubmissionRecognitionJob.submission_id == submission.id,
            )
            .order_by(
                SubmissionRecognitionJob.generation.desc(),
                SubmissionRecognitionJob.id,
            )
        )
        if (
            region is None
            or page is None
            or page.submission_id != submission.id
            or block is None
            or block.student_answer_region_id != region.id
            or block.submission_page_id != page.id
            or block.submission_recognition_job_id != evidence.recognition_job_id
            or block.submission_recognition_job_id != source_job_id
            or block.status != "confirmed"
            or block.stale_at is not None
            or block.input_hash is None
            or source.get("region_version") != region.region_version
            or source.get("block_recognition_version") != block.recognition_version
            or revision is None
            or not revision.confirmed
            or revision.stale_at is not None
            or revision.base_recognition_version != block.recognition_version
            or job is None
            or current_job is None
            or job.id != current_job.id
            or job.owner_id != owner_id
            or job.submission_id != submission.id
            or job.status != "completed"
            or image is None
            or image.owner_id != owner_id
            or image.submission_id != submission.id
            or image.submission_page_id != page.id
            or image.student_answer_region_id != region.id
            or image.status != "ready"
            or image.stale_at is not None
            or image.page_version != page.page_version
            or image.region_version != region.region_version
            or image.input_hash != block.input_hash
        ):
            _fail("PROCESSING_INPUT_STALE", "Recognition evidence no longer matches its source")
        seen_regions.add(region_id)
        seen_blocks.add(block_id)
        seen_sources.add(source_fingerprint)
        revision_content_hash = canonical_hash(
            {
                "id": revision.id,
                "revision": revision.revision,
                "base_recognition_version": revision.base_recognition_version,
                "source": revision.source,
                "raw_text": revision.raw_text,
                "normalized_text": revision.normalized_text,
                "latex": revision.latex,
                "warning_codes": revision.warning_codes,
            }
        )
        result.append(
            {
                "id": region_id,
                "region_version": region.region_version,
                "segmentation_version": region.segmentation_version,
                "page": {
                    "id": str(page.id),
                    "page_number": page.page_number,
                    "page_version": page.page_version,
                },
                "bbox": [region.x, region.y, region.width, region.height],
                "block": {
                    "id": str(block.id),
                    "input_hash": block.input_hash,
                    "output_hash": block.output_hash,
                    "recognition_version": block.recognition_version,
                    "reading_order": block.reading_order,
                    "provider": block.provider,
                    "provider_version": block.provider_version,
                    "recognition_job_id": str(block.submission_recognition_job_id),
                },
                "revision": {
                    "id": str(revision.id),
                    "revision": revision.revision,
                    "base_recognition_version": revision.base_recognition_version,
                    "source": revision.source,
                    "content_hash": revision_content_hash,
                },
                "evidence_image": {
                    "id": str(image.id),
                    "content_hash": image.content_hash,
                    "input_hash": image.input_hash,
                    "page_version": image.page_version,
                    "region_version": image.region_version,
                    "processing_config_version": image.processing_config_version,
                    "source_kind": image.source_kind,
                },
                "job": {
                    "id": str(job.id),
                    "generation": job.generation,
                    "provider": job.provider,
                    "provider_version": job.provider_version,
                    "provider_kind": job.provider_kind,
                    "config_version": job.config_version,
                    "input_hash": job.input_hash,
                    "output_hash": job.output_hash,
                },
            }
        )
    if seen_regions != set(regions_by_id):
        _fail("PROCESSING_INPUT_STALE", "Confirmed regions and evidence sources differ")
    result.sort(
        key=lambda item: (
            item["page"]["page_number"],
            item["block"]["reading_order"],
            item["id"],
            item["block"]["id"],
        )
    )
    return evidence, result


def build_processing_input_snapshot(
    db: Session,
    *,
    owner_id: uuid.UUID,
    grading_batch_id: uuid.UUID,
    submission_id: uuid.UUID,
    answer_id: uuid.UUID,
) -> ProcessingInputSnapshot:
    batch = db.get(GradingBatch, grading_batch_id)
    submission = db.get(Submission, submission_id)
    answer = db.get(StudentAnswer, answer_id)
    if (
        batch is None
        or submission is None
        or answer is None
        or batch.owner_id != owner_id
        or submission.owner_id != owner_id
        or submission.grading_batch_id != batch.id
        or submission.assignment_id != batch.assignment_id
        or submission.class_id != batch.class_id
        or answer.submission_id != submission.id
    ):
        _fail("SUBMISSION_SCOPE_MISMATCH", "Batch, submission, and answer scope must match")

    assignment = db.get(Assignment, batch.assignment_id)
    if (
        assignment is None
        or assignment.owner_id != owner_id
        or assignment.active_paper_version_id is None
        or assignment.status != AssignmentStatus.published
    ):
        _fail("SUBMISSION_SCOPE_MISMATCH", "Assignment is not current for this owner")
    paper = db.scalar(
        select(PaperVersion).where(
            PaperVersion.id == assignment.active_paper_version_id,
            PaperVersion.assignment_id == assignment.id,
            PaperVersion.status == VersionStatus.confirmed,
        )
    )
    question = db.scalar(
        select(Question).where(
            Question.id == answer.question_id,
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    )
    if paper is None or question is None:
        _fail("SUBMISSION_SCOPE_MISMATCH", "Answer does not use the active paper question")
    if answer.question_version_reference != str(paper.id):
        _fail("PROCESSING_INPUT_STALE", "Student answer question version is stale")

    reference, rubric, criteria = _latest_confirmed_formals(db, question)
    evidence, regions = _current_evidence(
        db, owner_id=owner_id, submission=submission, answer=answer
    )

    session = db.scalar(
        select(AssignmentReviewSession)
        .where(
            AssignmentReviewSession.assignment_id == assignment.id,
            AssignmentReviewSession.owner_id == owner_id,
            AssignmentReviewSession.paper_version_id == paper.id,
            AssignmentReviewSession.invalidated_at.is_(None),
            AssignmentReviewSession.status == "published",
        )
        .order_by(
            AssignmentReviewSession.review_version.desc(),
            AssignmentReviewSession.id,
        )
    )
    if session is None:
        _fail("LEGACY_PROJECTION_REQUIRED", "Current review session is required")
    projection = validate_current_projection_under_locks(
        db, session, lock=False, require_confirmed=True
    )
    if not projection.current or projection.binding is None:
        code = (
            "LEGACY_PROJECTION_STALE"
            if projection.binding is not None
            else "LEGACY_PROJECTION_REQUIRED"
        )
        _fail(code, projection.reason or "Current legacy projection is required")
    binding = projection.binding
    if (
        binding.owner_id != owner_id
        or binding.assignment_id != assignment.id
        or binding.paper_version_id != paper.id
        or session.legacy_rubric_version_id != binding.legacy_rubric_version_id
        or assignment.active_rubric_version_id != binding.legacy_rubric_version_id
    ):
        _fail("LEGACY_PROJECTION_STALE", "Legacy projection scope is stale")

    reference_recomputed_hash = semantic_hash(_answer_content_payload(reference))
    rubric_recomputed_hash = semantic_hash(_rubric_content_payload(db, rubric))
    criteria_hash = semantic_hash(
        [
            {
                "stable_key": item.stable_key,
                "display_order": item.display_order,
                "title": item.title,
                "description": item.description,
                "max_points": item.max_points,
                "criterion_type": item.criterion_type,
                "required": item.required,
                "dependencies": item.dependencies,
                "expected_evidence": item.expected_evidence,
                "validation_mode": item.validation_mode,
                "validation_rule": item.validation_rule,
                "manual_review_policy": item.manual_review_policy,
                "partial_credit_policy": item.partial_credit_policy,
                "error_category": item.error_category,
                "metadata": item.metadata_,
            }
            for item in criteria
        ]
    )
    effective_answer_hash = semantic_hash(
        {
            "text": answer.corrected_text
            if answer.corrected_text is not None
            else answer.recognized_text,
            "latex": answer.corrected_latex
            if answer.corrected_latex is not None
            else answer.recognized_latex,
            "is_blank": answer.is_blank,
        }
    )
    recognition_source_hash = canonical_hash(
        {
            "evidence": {
                "id": evidence.id,
                "input_hash": evidence.input_hash,
                "output_hash": evidence.output_hash,
                "recognition_version": evidence.recognition_version,
                "confirmed_revision": evidence.confirmed_revision,
                "recognition_job_id": evidence.recognition_job_id,
            },
            "regions": regions,
        }
    )
    payload: dict[str, Any] = {
        "schema": PROCESSING_INPUT_SCHEMA,
        "owner": {"id": str(owner_id)},
        "assignment": {"id": str(assignment.id)},
        "batch": {"id": str(batch.id)},
        "submission": {"id": str(submission.id)},
        "answer": {
            "id": str(answer.id),
            "question_version_reference": answer.question_version_reference,
            "effective_answer_hash": effective_answer_hash,
        },
        "question": {
            "id": str(question.id),
            "paper_version_id": str(paper.id),
            "paper_version": paper.version,
            "question_version": rubric.question_version,
            "content_hash": semantic_hash(
                {
                    "number": question.question_number,
                    "order": question.display_order,
                    "type": question.question_type,
                    "text": question.content_text,
                    "latex": question.content_latex,
                    "max_score": question.max_score,
                }
            ),
        },
        "recognition_evidence": {
            "id": str(evidence.id),
            "input_hash": evidence.input_hash,
            "recognition_version": evidence.recognition_version,
            "confirmed_revision": evidence.confirmed_revision,
            "recognition_source_hash": recognition_source_hash,
            "regions": regions,
        },
        "formal": {
            "reference_answer": {
                "id": str(reference.id),
                "version": reference.version,
                "stored_content_hash": reference.content_hash,
                "recomputed_content_hash": reference_recomputed_hash,
            },
            "structured_rubric": {
                "id": str(rubric.id),
                "version": rubric.rubric_version,
                "stored_content_hash": rubric.content_hash,
                "recomputed_content_hash": rubric_recomputed_hash,
                "criteria_hash": criteria_hash,
                "reference_answer_id": str(rubric.reference_answer_version_id),
            },
        },
        "legacy_projection": {
            "binding_id": str(binding.id),
            "legacy_rubric_version_id": str(binding.legacy_rubric_version_id),
            "target_hash": binding.target_legacy_hash,
            "profile": binding.projection_profile,
            "version": binding.projection_version,
        },
    }
    return ProcessingInputSnapshot(payload=payload, input_version=canonical_hash(payload))
