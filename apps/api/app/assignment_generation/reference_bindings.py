"""Draft-only reference-answer source bindings keyed by stable question numbers."""

from __future__ import annotations

import uuid
from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assignment_generation.answer_rubric import question_version
from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AssignmentSourceFileAnalysis,
    PaperPage,
    Question,
    RecognitionBlock,
    RecognitionJob,
    RecognitionStatus,
    ReferenceAnswerSourceBinding,
    ReferenceAnswerSourceRegion,
)
from app.recognition.pipeline import QuestionAnchor, derive_question_regions
from app.recognition.question_numbers import normalize_question_number


def build_reference_answer_bindings(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision
) -> dict[str, Any]:
    """Create auditable suggestions without accepting answer content."""

    assignment = db.get(Assignment, job.assignment_id)
    if assignment is None or assignment.active_paper_version_id is None:
        return {"created": 0, "blocked": "ASSIGNMENT_VERSION_MISSING"}
    sources = list(
        db.scalars(
            select(AssignmentSourceFileAnalysis).where(
                AssignmentSourceFileAnalysis.draft_revision_id == revision.id,
                AssignmentSourceFileAnalysis.analysis_status == "confirmed",
                AssignmentSourceFileAnalysis.teacher_confirmed_role.in_(
                    ["reference_answer", "question_and_answer"]
                ),
            )
        )
    )
    if not sources:
        return {"created": 0, "blocked": "REFERENCE_ANSWER_ROLE_UNCONFIRMED"}

    questions = list(
        db.scalars(
            select(Question)
            .where(
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == "active",
            )
            .order_by(Question.display_order, Question.id)
        )
    )
    normalized_questions = [
        (question, normalize_question_number(question.question_number)) for question in questions
    ]
    question_counts = Counter(number for _question, number in normalized_questions if number)
    questions_by_number = {
        number: question
        for question, number in normalized_questions
        if number is not None and question_counts[number] == 1
    }

    old_rows = list(
        db.scalars(
            select(ReferenceAnswerSourceBinding)
            .where(ReferenceAnswerSourceBinding.draft_revision_id == revision.id)
            .with_for_update()
        )
    )
    for row in old_rows:
        if row.status == "suggested":
            row.status = "superseded"
    version = (
        int(
            db.scalar(
                select(
                    func.coalesce(func.max(ReferenceAnswerSourceBinding.binding_version), 0)
                ).where(ReferenceAnswerSourceBinding.draft_revision_id == revision.id)
            )
            or 0
        )
        + 1
    )

    source_pages: dict[uuid.UUID, list[PaperPage]] = {}
    all_blocks: list[tuple[AssignmentSourceFileAnalysis, RecognitionBlock, str]] = []
    for source in sources:
        pages = list(
            db.scalars(
                select(PaperPage)
                .where(
                    PaperPage.paper_version_id == assignment.active_paper_version_id,
                    PaperPage.stored_file_id == source.stored_file_id,
                    PaperPage.status != "excluded",
                )
                .order_by(PaperPage.source_page_number, PaperPage.page_number, PaperPage.id)
            )
        )
        source_pages[source.id] = pages
        if not pages:
            continue
        recognition = db.scalar(
            select(RecognitionJob)
            .where(
                RecognitionJob.paper_version_id == assignment.active_paper_version_id,
                RecognitionJob.status.in_(
                    [RecognitionStatus.completed, RecognitionStatus.partially_completed]
                ),
            )
            .order_by(RecognitionJob.created_at.desc(), RecognitionJob.id.desc())
        )
        if recognition is None:
            continue
        page_ids = {page.id for page in pages}
        for block in db.scalars(
            select(RecognitionBlock)
            .where(
                RecognitionBlock.recognition_job_id == recognition.id,
                RecognitionBlock.paper_page_id.in_(page_ids),
                RecognitionBlock.block_type == "question_number",
            )
            .order_by(RecognitionBlock.paper_page_id, RecognitionBlock.y, RecognitionBlock.id)
        ):
            number = normalize_question_number(block.text or "")
            if number is not None:
                all_blocks.append((source, block, number))

    anchor_counts = Counter(number for _source, _block, number in all_blocks)
    created = 0
    manual_required = 0
    for source in sources:
        pages = source_pages.get(source.id, [])
        blocks = [(block, number) for item, block, number in all_blocks if item.id == source.id]
        regions_by_block = derive_question_regions(
            [page.id for page in pages],
            [
                QuestionAnchor(block.id, block.paper_page_id, float(block.y))
                for block, _number in blocks
            ],
        )
        for block, number in blocks:
            warnings: list[str] = []
            question = questions_by_number.get(number)
            if question is None:
                warnings.append(
                    "QUESTION_NUMBER_CONFLICT"
                    if question_counts[number] > 1
                    else "QUESTION_NUMBER_UNKNOWN"
                )
            if anchor_counts[number] > 1:
                warnings.append("REFERENCE_NUMBER_DUPLICATE")
            confidence = Decimal(str(block.confidence or 0))
            if confidence < Decimal("0.80"):
                warnings.append("REFERENCE_ANCHOR_LOW_CONFIDENCE")
            manual = bool(warnings)
            binding = ReferenceAnswerSourceBinding(
                owner_id=job.owner_id,
                assignment_id=job.assignment_id,
                draft_revision_id=revision.id,
                paper_version_id=assignment.active_paper_version_id,
                source_file_analysis_id=source.id,
                source_recognition_block_id=block.id,
                question_id=question.id if question is not None else None,
                detected_number=number,
                binding_version=version,
                confidence=confidence,
                warning_codes=sorted(set(warnings)),
                source_snapshot_hash=job.source_snapshot_hash,
            )
            db.add(binding)
            db.flush()
            derived = regions_by_block.get(block.id, [])
            for display_order, region in enumerate(derived):
                db.add(
                    ReferenceAnswerSourceRegion(
                        binding_id=binding.id,
                        paper_page_id=region.paper_page_id,
                        display_order=display_order,
                        x=region.x,
                        y=region.y,
                        width=region.width,
                        height=region.height,
                        source="pdf_text_anchor",
                        confidence=confidence,
                        evidence={"recognition_block_id": str(block.id)},
                    )
                )
            created += 1
            manual_required += int(manual)
    return {
        "created": created,
        "manual_required": manual_required,
        "binding_version": version,
    }


class ReferenceTextExtractionError(RuntimeError):
    pass


def extract_reference_answer_candidate(
    db: Session, binding: ReferenceAnswerSourceBinding
) -> AssignmentAnswerDraftCandidate:
    """Create one editable suggestion from confirmed pdf_text evidence only."""

    existing = db.scalar(
        select(AssignmentAnswerDraftCandidate).where(
            AssignmentAnswerDraftCandidate.source_reference_binding_id == binding.id
        )
    )
    if existing is not None:
        return existing
    if binding.status != "confirmed" or binding.question_id is None:
        raise ReferenceTextExtractionError("REFERENCE_BINDING_NOT_CONFIRMED")
    question = db.get(Question, binding.question_id)
    source = db.get(AssignmentSourceFileAnalysis, binding.source_file_analysis_id)
    revision = db.get(AssignmentDraftRevision, binding.draft_revision_id)
    anchor = db.get(RecognitionBlock, binding.source_recognition_block_id)
    if question is None or source is None or revision is None or anchor is None:
        raise ReferenceTextExtractionError("REFERENCE_BINDING_SOURCE_MISSING")
    regions = list(
        db.scalars(
            select(ReferenceAnswerSourceRegion)
            .where(ReferenceAnswerSourceRegion.binding_id == binding.id)
            .order_by(ReferenceAnswerSourceRegion.display_order)
        )
    )
    if not regions:
        raise ReferenceTextExtractionError("REFERENCE_BINDING_REGION_REQUIRED")
    block_rows: list[tuple[int, RecognitionBlock]] = []
    seen: set[uuid.UUID] = set()
    for region in regions:
        page = db.get(PaperPage, region.paper_page_id)
        if page is None:
            continue
        for block in db.scalars(
            select(RecognitionBlock)
            .where(
                RecognitionBlock.paper_page_id == region.paper_page_id,
                RecognitionBlock.recognition_job_id == anchor.recognition_job_id,
                RecognitionBlock.id != binding.source_recognition_block_id,
                RecognitionBlock.status == "recognized",
                RecognitionBlock.source.like("pdf_text:%"),
                RecognitionBlock.text.is_not(None),
                RecognitionBlock.x >= region.x,
                RecognitionBlock.y >= region.y,
                RecognitionBlock.x + RecognitionBlock.width <= region.x + region.width,
                RecognitionBlock.y + RecognitionBlock.height <= region.y + region.height,
            )
            .order_by(RecognitionBlock.display_order, RecognitionBlock.y, RecognitionBlock.x)
        ):
            if block.id not in seen and (block.text or "").strip():
                seen.add(block.id)
                block_rows.append((page.page_number, block))
    block_rows.sort(key=lambda item: (item[0], item[1].display_order, item[1].y, item[1].x))
    lines = [(block.text or "").strip() for _page_number, block in block_rows]
    if not lines:
        raise ReferenceTextExtractionError("REFERENCE_PDF_TEXT_MISSING")
    raw = "\n".join(lines)
    normalized = "\n".join(" ".join(line.split()) for line in lines)
    for old in db.scalars(
        select(AssignmentAnswerDraftCandidate)
        .where(
            AssignmentAnswerDraftCandidate.draft_revision_id == binding.draft_revision_id,
            AssignmentAnswerDraftCandidate.question_id == question.id,
            AssignmentAnswerDraftCandidate.status.in_(["suggested", "manual_required"]),
        )
        .with_for_update()
    ):
        old.status = "superseded"
    version = (
        int(
            db.scalar(
                select(
                    func.coalesce(func.max(AssignmentAnswerDraftCandidate.candidate_version), 0)
                ).where(
                    AssignmentAnswerDraftCandidate.draft_revision_id == binding.draft_revision_id,
                    AssignmentAnswerDraftCandidate.question_id == question.id,
                )
            )
            or 0
        )
        + 1
    )
    confidence = min(
        [
            Decimal(str(binding.confidence)),
            *[Decimal(str(block.confidence or 0)) for _, block in block_rows],
        ]
    )
    candidate = AssignmentAnswerDraftCandidate(
        owner_id=binding.owner_id,
        assignment_id=binding.assignment_id,
        generation_job_id=revision.generation_job_id,
        draft_revision_id=binding.draft_revision_id,
        question_id=question.id,
        question_version=question_version(question),
        candidate_version=version,
        source_type=source.teacher_confirmed_answer_source or "unknown",
        source_file_analysis_id=source.id,
        source_page_id=regions[0].paper_page_id,
        source_reference_binding_id=binding.id,
        source_region={
            "binding_id": str(binding.id),
            "regions": [
                {
                    "paper_page_id": str(region.paper_page_id),
                    "x": str(region.x),
                    "y": str(region.y),
                    "width": str(region.width),
                    "height": str(region.height),
                }
                for region in regions
            ],
        },
        raw_content=raw,
        normalized_content=normalized,
        structured_content={"format": "plain_text", "source": "pdf_text"},
        alternative_answers=[],
        provenance={
            "reference_answer_source_binding_id": str(binding.id),
            "binding_version": binding.binding_version,
            "source_snapshot_hash": binding.source_snapshot_hash,
            "extraction": "deterministic_pdf_text_regions",
        },
        confidence=confidence,
        evidence=[
            {"kind": "recognition_block", "reference_id": str(block.id)}
            for _page_number, block in block_rows
        ],
        warning_codes=["REFERENCE_TEXT_REQUIRES_TEACHER_CONFIRMATION"],
        status="suggested",
        manual_required=True,
        source_snapshot_hash=binding.source_snapshot_hash,
    )
    db.add(candidate)
    db.flush()
    return candidate
