"""Bridge current page-processing/Recognition results into revisioned draft suggestions."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assignment_generation.question_extraction import materialize, prompt_injection
from app.models import (
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AssignmentPageAnalysis,
    AssignmentQuestionExtractionCandidate,
    AssignmentQuestionExtractionRegion,
    AssignmentSourceFileAnalysis,
    PageProcessingResult,
    PaperPage,
    PaperPageOrganizationSuggestion,
    QuestionCandidate,
    QuestionCandidateRegion,
    RecognitionBlock,
    RecognitionJob,
    RecognitionStatus,
)


def build_page_suggestions(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision
) -> int:
    sources = list(
        db.scalars(
            select(AssignmentSourceFileAnalysis).where(
                AssignmentSourceFileAnalysis.draft_revision_id == revision.id,
                (
                    (AssignmentSourceFileAnalysis.analysis_status == "confirmed")
                    & (AssignmentSourceFileAnalysis.teacher_confirmed_role == "question_paper")
                )
                | (
                    (AssignmentSourceFileAnalysis.analysis_status == "suggested")
                    & (AssignmentSourceFileAnalysis.suggested_role == "question_paper")
                    & (AssignmentSourceFileAnalysis.role_confidence >= 0.7)
                ),
            )
        ).all()
    )
    file_ids = {x.stored_file_id for x in sources}
    pages = (
        list(
            db.scalars(
                select(PaperPage)
                .where(PaperPage.stored_file_id.in_(file_ids))
                .order_by(PaperPage.paper_version_id, PaperPage.page_number)
            ).all()
        )
        if file_ids
        else []
    )
    created = 0
    for page in pages:
        old = db.scalar(
            select(PaperPageOrganizationSuggestion)
            .where(
                PaperPageOrganizationSuggestion.draft_revision_id == revision.id,
                PaperPageOrganizationSuggestion.paper_page_id == page.id,
                PaperPageOrganizationSuggestion.status == "suggested",
            )
            .order_by(PaperPageOrganizationSuggestion.suggestion_version.desc())
        )
        version = (old.suggestion_version + 1) if old else 1
        if old:
            old.status = "superseded"
        analysis = db.scalar(
            select(AssignmentPageAnalysis).where(
                AssignmentPageAnalysis.draft_revision_id == revision.id,
                AssignmentPageAnalysis.paper_page_id == page.id,
            )
        )
        processing = db.scalar(
            select(PageProcessingResult)
            .where(
                PageProcessingResult.paper_page_id == page.id,
                PageProcessingResult.status.in_(["completed", "ready"]),
            )
            .order_by(PageProcessingResult.created_at.desc())
        )
        reasons = []
        suggested_status = page.status
        confidence = 0.95
        if analysis:
            reasons = list(analysis.warning_codes)
            if analysis.blank_probability is not None and float(analysis.blank_probability) >= 0.99:
                suggested_status = "excluded"
                reasons.append("PAGE_EXCLUSION_REVIEW_REQUIRED")
            if (
                analysis.duplicate_probability is not None
                and float(analysis.duplicate_probability) >= 0.99
            ):
                suggested_status = "excluded"
                reasons.append("DUPLICATE_PAGE_REVIEW_REQUIRED")
            if analysis.low_quality:
                confidence = min(confidence, 0.6)
        rotation = processing.detected_rotation if processing else page.rotation
        if rotation != page.rotation:
            reasons.append("PAGE_ROTATION_REVIEW_REQUIRED")
        db.add(
            PaperPageOrganizationSuggestion(
                owner_id=job.owner_id,
                assignment_id=job.assignment_id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                paper_version_id=page.paper_version_id,
                paper_page_id=page.id,
                suggestion_version=version,
                suggested_page_number=page.page_number,
                suggested_rotation=rotation,
                suggested_status=suggested_status,
                duplicate_of_page_id=analysis.duplicate_of_page_id if analysis else None,
                variant_label=analysis.variant_label if analysis else None,
                confidence=confidence,
                reason_codes=sorted(set(reasons)),
                evidence=(analysis.evidence if analysis else [])
                + [{"type": "original_pdf_order", "source_page_number": page.source_page_number}],
                source_snapshot_hash=job.source_snapshot_hash,
            )
        )
        created += 1
    return created


def build_fake_candidates(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision
) -> dict[str, Any]:
    sources = list(
        db.scalars(
            select(AssignmentSourceFileAnalysis).where(
                AssignmentSourceFileAnalysis.draft_revision_id == revision.id
            )
        ).all()
    )
    questions = {
        x.stored_file_id
        for x in sources
        if (x.analysis_status == "confirmed" and x.teacher_confirmed_role == "question_paper")
        or (
            x.analysis_status == "suggested"
            and x.suggested_role == "question_paper"
            and float(x.role_confidence or 0) >= 0.7
        )
    }
    if not questions:
        return {"created": 0, "blocked": "QUESTION_PAPER_ROLE_UNCONFIRMED"}
    pages = list(
        db.scalars(
            select(PaperPage).where(
                PaperPage.stored_file_id.in_(questions), PaperPage.status != "excluded"
            )
        ).all()
    )
    if not pages:
        return {"created": 0, "blocked": "PAGE_PROCESSING_INCOMPLETE"}
    processed_page_ids = set(
        db.scalars(
            select(PageProcessingResult.paper_page_id).where(
                PageProcessingResult.paper_page_id.in_({page.id for page in pages}),
                PageProcessingResult.status.in_(["completed", "ready"]),
            )
        ).all()
    )
    if {page.id for page in pages if page.status != "ready" and page.id not in processed_page_ids}:
        return {"created": 0, "blocked": "PAGE_PROCESSING_INCOMPLETE"}
    version_ids = {x.paper_version_id for x in pages}
    recognition = db.scalar(
        select(RecognitionJob)
        .where(
            RecognitionJob.paper_version_id.in_(version_ids),
            RecognitionJob.status.in_(
                [RecognitionStatus.completed, RecognitionStatus.partially_completed]
            ),
        )
        .order_by(RecognitionJob.created_at.desc())
    )
    if recognition is None:
        return {"created": 0, "blocked": "OCR_UNAVAILABLE"}
    page_ids = {x.id for x in pages}
    candidates = list(
        db.scalars(
            select(QuestionCandidate)
            .where(
                QuestionCandidate.recognition_job_id == recognition.id,
                QuestionCandidate.paper_version_id.in_(version_ids),
            )
            .order_by(QuestionCandidate.created_at, QuestionCandidate.temporary_number)
        ).all()
    )
    for old in db.scalars(
        select(AssignmentQuestionExtractionCandidate).where(
            AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
            AssignmentQuestionExtractionCandidate.status == "suggested",
        )
    ).all():
        old.status = "superseded"
    version = (
        int(
            db.scalar(
                select(
                    func.coalesce(
                        func.max(AssignmentQuestionExtractionCandidate.candidate_version), 0
                    )
                ).where(AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id)
            )
            or 0
        )
        + 1
    )
    created = 0
    injection = False
    number_counts = Counter((item.temporary_number or "").strip() for item in candidates)
    for source in candidates:
        source_regions = list(
            db.scalars(
                select(QuestionCandidateRegion).where(
                    QuestionCandidateRegion.question_candidate_id == source.id
                )
            ).all()
        )
        source_regions = [r for r in source_regions if r.paper_page_id in page_ids]
        if not source_regions:
            continue
        block_ids: list[str] = []
        blocks_by_region: dict[uuid.UUID, list[str]] = {}
        for region in source_regions:
            region_blocks = [
                str(x)
                for x in db.scalars(
                    select(RecognitionBlock.id).where(
                        RecognitionBlock.recognition_job_id == recognition.id,
                        RecognitionBlock.paper_page_id == region.paper_page_id,
                        RecognitionBlock.x >= region.x,
                        RecognitionBlock.y >= region.y,
                        RecognitionBlock.x + RecognitionBlock.width <= region.x + region.width,
                        RecognitionBlock.y + RecognitionBlock.height <= region.y + region.height,
                    )
                ).all()
            ]
            blocks_by_region[region.id] = region_blocks
            block_ids.extend(region_blocks)
        warnings = []
        manual = False
        latex = source.content_latex
        if not (source.temporary_number or "").strip():
            warnings.append("QUESTION_NUMBER_MISSING")
        elif number_counts[(source.temporary_number or "").strip()] > 1:
            warnings.append("QUESTION_NUMBER_CONFLICT")
            manual = True
        if source.suggested_score is None:
            warnings.append("QUESTION_SCORE_MISSING")
        if source.question_type == "proof":
            warnings.append("PROOF_MANUAL_REVIEW")
            manual = True
        referenced_blocks = [
            block
            for value in block_ids
            if (block := db.get(RecognitionBlock, uuid.UUID(value))) is not None
        ]
        if any(block.block_type in {"formula", "figure", "table"} for block in referenced_blocks):
            kinds = {block.block_type for block in referenced_blocks}
            for kind in kinds & {"formula", "figure", "table"}:
                warnings.append(f"{kind.upper()}_REVIEW_REQUIRED")
                manual = True
            if "formula" in kinds:
                latex = None
        if len({r.paper_page_id for r in source_regions}) > 1:
            warnings.append("CROSS_PAGE_REVIEW_REQUIRED")
            manual = True
        if len(source_regions) > 1:
            warnings.append("MULTI_REGION_REVIEW_REQUIRED")
        if prompt_injection(source.content_text):
            warnings.append("PROMPT_INJECTION_CONTENT_DETECTED")
            injection = True
        confidence = float(source.confidence or 0)
        field_conf = {
            k: confidence
            for k in (
                "question_number",
                "parent_relation",
                "question_type",
                "content_text",
                "content_latex",
                "max_score",
                "difficulty",
                "knowledge_points",
                "regions",
            )
        }
        row = AssignmentQuestionExtractionCandidate(
            owner_id=job.owner_id,
            assignment_id=job.assignment_id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            paper_version_id=source.paper_version_id,
            source_recognition_job_id=recognition.id,
            source_question_candidate_id=source.id,
            candidate_version=version,
            question_number=source.temporary_number,
            question_type=source.question_type,
            content_text=source.content_text,
            content_latex=latex,
            max_score=source.suggested_score,
            difficulty=None,
            knowledge_point_suggestions=[],
            field_confidences=field_conf,
            overall_confidence=confidence,
            extraction_method="recognition_bridge_fake",
            evidence={
                "untrusted_document_content": True,
                "source_candidate_id": str(source.id),
                "recognition_block_ids": block_ids,
            },
            warning_codes=warnings,
            status="suggested",
            manual_required=manual,
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(row)
        db.flush()
        display_order = 0
        for region in source_regions:
            db.add(
                AssignmentQuestionExtractionRegion(
                    candidate_id=row.id,
                    paper_page_id=region.paper_page_id,
                    display_order=display_order,
                    region_type="stem",
                    x=region.x,
                    y=region.y,
                    width=region.width,
                    height=region.height,
                    confidence=region.confidence or confidence,
                    evidence={"source": "question_candidate_region"},
                    source_block_ids=blocks_by_region[region.id],
                    cross_page_group=str(row.id)
                    if len({r.paper_page_id for r in source_regions}) > 1
                    else None,
                )
            )
            display_order += 1
        for block in referenced_blocks:
            if block.block_type not in {"formula", "figure", "table"}:
                continue
            db.add(
                AssignmentQuestionExtractionRegion(
                    candidate_id=row.id,
                    paper_page_id=block.paper_page_id,
                    display_order=display_order,
                    region_type=block.block_type,
                    x=block.x,
                    y=block.y,
                    width=block.width,
                    height=block.height,
                    confidence=block.confidence or confidence,
                    evidence={"source": "recognition_block", "untrusted_document_content": True},
                    source_block_ids=[str(block.id)],
                    cross_page_group=str(row.id)
                    if len({r.paper_page_id for r in source_regions}) > 1
                    else None,
                )
            )
            display_order += 1
        created += 1
    return {
        "created": created,
        "recognition_job_id": str(recognition.id),
        "prompt_injection_detected": injection,
        "question_number_conflict": any(value > 1 for key, value in number_counts.items() if key),
    }


def materialize_draft_questions(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision
) -> int:
    """Create editable AI-draft questions without recording teacher approval."""
    rows = list(
        db.scalars(
            select(AssignmentQuestionExtractionCandidate)
            .where(
                AssignmentQuestionExtractionCandidate.generation_job_id == job.id,
                AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
                AssignmentQuestionExtractionCandidate.status == "suggested",
            )
            .order_by(
                AssignmentQuestionExtractionCandidate.parent_candidate_id,
                AssignmentQuestionExtractionCandidate.question_number,
                AssignmentQuestionExtractionCandidate.id,
            )
        ).all()
    )
    created = 0
    pending = list(rows)
    while pending:
        progressed = False
        for row in list(pending):
            parent = (
                db.get(AssignmentQuestionExtractionCandidate, row.parent_candidate_id)
                if row.parent_candidate_id
                else None
            )
            if parent is not None and parent.materialized_question_id is None:
                continue
            regions = list(
                db.scalars(
                    select(AssignmentQuestionExtractionRegion)
                    .where(AssignmentQuestionExtractionRegion.candidate_id == row.id)
                    .order_by(AssignmentQuestionExtractionRegion.display_order)
                ).all()
            )
            question = materialize(db, row, regions)
            if parent is not None:
                question.parent_question_id = parent.materialized_question_id
            created += 1
            pending.remove(row)
            progressed = True
        if not progressed:
            break
    return created
