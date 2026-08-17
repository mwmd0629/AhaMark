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
from app.recognition.text_integrity import text_quality_statistics

_MATH_STRUCTURE_RISK_CODES = {
    "FORMULA_REVIEW_REQUIRED",
    "MATH_LAYOUT_REVIEW_REQUIRED",
    "READING_ORDER_CONFLICT",
}


def _regions_overlap(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> bool:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return min(first_x + first_width, second_x + second_width) > max(first_x, second_x) and min(
        first_y + first_height, second_y + second_height
    ) > max(first_y, second_y)


def _completed_recognitions_by_version(
    db: Session, version_ids: set[uuid.UUID]
) -> dict[uuid.UUID, RecognitionJob]:
    rows = list(
        db.scalars(
            select(RecognitionJob)
            .where(
                RecognitionJob.paper_version_id.in_(version_ids),
                RecognitionJob.status == RecognitionStatus.completed,
            )
            .order_by(RecognitionJob.created_at.desc())
        ).all()
    )
    selected: dict[uuid.UUID, RecognitionJob] = {}
    for row in rows:
        selected.setdefault(row.paper_version_id, row)
    return selected


def build_page_suggestions(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision
) -> int:
    sources = list(
        db.scalars(
            select(AssignmentSourceFileAnalysis).where(
                AssignmentSourceFileAnalysis.draft_revision_id == revision.id,
                AssignmentSourceFileAnalysis.analysis_status == "confirmed",
                AssignmentSourceFileAnalysis.teacher_confirmed_role.in_(
                    ["question_paper", "question_and_answer"]
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
    recognitions = _completed_recognitions_by_version(db, {page.paper_version_id for page in pages})
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
        recognition = recognitions.get(page.paper_version_id)
        processing = (
            db.scalar(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == recognition.id,
                    PageProcessingResult.paper_page_id == page.id,
                    PageProcessingResult.status.in_(["completed", "ready"]),
                )
            )
            if recognition is not None
            else None
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


def build_local_candidates(
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
        if x.analysis_status == "confirmed"
        and x.teacher_confirmed_role in {"question_paper", "question_and_answer"}
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
    version_ids = {x.paper_version_id for x in pages}
    recognitions = _completed_recognitions_by_version(db, version_ids)
    if set(recognitions) != version_ids:
        return {"created": 0, "blocked": "OCR_UNAVAILABLE"}
    recognition_ids = {item.id for item in recognitions.values()}
    page_versions = {page.id: page.paper_version_id for page in pages}
    processed_rows = list(
        db.scalars(
            select(PageProcessingResult).where(
                PageProcessingResult.recognition_job_id.in_(recognition_ids),
                PageProcessingResult.paper_page_id.in_(page_versions),
                PageProcessingResult.status.in_(["completed", "ready"]),
            )
        ).all()
    )
    processed_page_ids = {
        row.paper_page_id
        for row in processed_rows
        if row.recognition_job_id == recognitions[page_versions[row.paper_page_id]].id
    }
    if {page.id for page in pages if page.status != "ready" and page.id not in processed_page_ids}:
        return {"created": 0, "blocked": "PAGE_PROCESSING_INCOMPLETE"}
    page_ids = {x.id for x in pages}
    page_quality: dict[uuid.UUID, tuple[str, list[str]]] = {}
    page_math_risks: dict[
        uuid.UUID, list[tuple[str, set[int], tuple[float, float, float, float]]]
    ] = {}
    page_math_symbol_conflicts: dict[uuid.UUID, int] = {}
    public_quality_issues = {
        "low_resolution",
        "blur",
        "low_contrast",
        "shadow",
        "skew",
        "crop_risk",
    }
    for analysis in db.scalars(
        select(AssignmentPageAnalysis).where(
            AssignmentPageAnalysis.draft_revision_id == revision.id,
            AssignmentPageAnalysis.paper_page_id.in_(page_ids),
        )
    ):
        raw = (analysis.metrics or {}).get("page_quality")
        if isinstance(raw, dict):
            level = raw.get("level")
            issues = raw.get("issues")
            if level in {"review_required", "rescan_required"}:
                page_quality[analysis.paper_page_id] = (
                    str(level),
                    sorted(str(issue) for issue in issues if str(issue) in public_quality_issues)
                    if isinstance(issues, list)
                    else [],
                )
        raw_math = (analysis.metrics or {}).get("math_structure")
        risks = []
        if isinstance(raw_math, dict):
            codes = raw_math.get("risk_codes")
            evidence = raw_math.get("evidence")
            if isinstance(codes, (list, tuple)) and isinstance(evidence, (list, tuple)):
                for code, item in zip(codes, evidence, strict=False):
                    if code not in _MATH_STRUCTURE_RISK_CODES or not isinstance(item, dict):
                        continue
                    raw_indexes = item.get("block_indexes")
                    raw_region = item.get("region")
                    if not (
                        isinstance(raw_indexes, (list, tuple))
                        and isinstance(raw_region, (list, tuple))
                        and len(raw_region) == 4
                        and all(
                            isinstance(value, (int, float)) and not isinstance(value, bool)
                            for value in raw_region
                        )
                    ):
                        continue
                    risks.append(
                        (
                            str(code),
                            {
                                index
                                for index in raw_indexes
                                if isinstance(index, int)
                                and not isinstance(index, bool)
                                and index >= 0
                            },
                            (
                                float(raw_region[0]),
                                float(raw_region[1]),
                                float(raw_region[2]),
                                float(raw_region[3]),
                            ),
                        )
                    )
        if risks:
            page_math_risks[analysis.paper_page_id] = risks
        raw_conflicts = (analysis.metrics or {}).get("source_conflicts")
        if isinstance(raw_conflicts, dict):
            math_count = raw_conflicts.get("math_symbol_count")
            if isinstance(math_count, int) and not isinstance(math_count, bool) and math_count > 0:
                page_math_symbol_conflicts[analysis.paper_page_id] = math_count
    candidates = list(
        db.scalars(
            select(QuestionCandidate)
            .where(
                QuestionCandidate.recognition_job_id.in_(recognition_ids),
                QuestionCandidate.paper_version_id.in_(version_ids),
            )
            .order_by(QuestionCandidate.created_at, QuestionCandidate.temporary_number)
        ).all()
    )
    trusted_candidates = [
        item
        for item in candidates
        if item.source.startswith("pdf_text:")
        or item.source.startswith("rapidocr:")
        or item.source.startswith("tesseract:")
        or item.source.startswith("mixed:")
    ]
    if not trusted_candidates:
        return {"created": 0, "blocked": "TRUSTED_TEXT_SOURCE_UNAVAILABLE"}
    existing_rows = list(
        db.scalars(
            select(AssignmentQuestionExtractionCandidate).where(
                AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id
            )
        ).all()
    )
    protected_source_ids = {
        row.source_question_candidate_id
        for row in existing_rows
        if row.source_question_candidate_id is not None
        and (row.materialized_question_id is not None or row.status in {"accepted", "modified"})
    }
    candidates = [item for item in trusted_candidates if item.id not in protected_source_ids]
    for old in (
        row
        for row in existing_rows
        if row.status == "suggested" and row.materialized_question_id is None
    ):
        old.status = "superseded"
    if not candidates:
        return {
            "created": 0,
            "preserved": len(protected_source_ids),
            "recognition_job_ids": sorted(str(value) for value in recognition_ids),
        }
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
        source_recognition_id = source.recognition_job_id
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
        conflict_block_ids: list[str] = []
        blocks_by_region: dict[uuid.UUID, list[str]] = {}
        for region in source_regions:
            region_blocks = [
                str(x)
                for x in db.scalars(
                    select(RecognitionBlock.id).where(
                        RecognitionBlock.recognition_job_id == source_recognition_id,
                        RecognitionBlock.paper_page_id == region.paper_page_id,
                        RecognitionBlock.x + RecognitionBlock.width / 2 >= region.x,
                        RecognitionBlock.y + RecognitionBlock.height / 2 >= region.y,
                        RecognitionBlock.x + RecognitionBlock.width / 2 <= region.x + region.width,
                        RecognitionBlock.y + RecognitionBlock.height / 2
                        <= region.y + region.height,
                        RecognitionBlock.status.in_(
                            ["adopted", "manual_required", "recognized", "low_confidence"]
                        ),
                    )
                ).all()
            ]
            conflict_block_ids.extend(
                str(x)
                for x in db.scalars(
                    select(RecognitionBlock.id).where(
                        RecognitionBlock.recognition_job_id == source_recognition_id,
                        RecognitionBlock.paper_page_id == region.paper_page_id,
                        RecognitionBlock.x + RecognitionBlock.width / 2 >= region.x,
                        RecognitionBlock.y + RecognitionBlock.height / 2 >= region.y,
                        RecognitionBlock.x + RecognitionBlock.width / 2 <= region.x + region.width,
                        RecognitionBlock.y + RecognitionBlock.height / 2
                        <= region.y + region.height,
                        RecognitionBlock.status == "source_conflict",
                    )
                ).all()
            )
            blocks_by_region[region.id] = region_blocks
            block_ids.extend(region_blocks)
        warnings = []
        manual = False
        region_quality = {
            region.paper_page_id: page_quality[region.paper_page_id]
            for region in source_regions
            if region.paper_page_id in page_quality
        }
        quality_levels = {level for level, _issues in region_quality.values()}
        if "rescan_required" in quality_levels:
            warnings.append("PAGE_QUALITY_RESCAN_REQUIRED")
            manual = True
        elif "review_required" in quality_levels:
            warnings.append("PAGE_QUALITY_REVIEW_REQUIRED")
            manual = True
        latex = source.content_latex
        if not (source.temporary_number or "").strip():
            warnings.append("QUESTION_NUMBER_MISSING")
        elif "[重复 " in source.temporary_number:
            warnings.append("QUESTION_NUMBER_CONFLICT")
            manual = True
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
        if any(block.status == "manual_required" for block in referenced_blocks):
            manual = True
        if any(
            not block.source.startswith("pdf_text:")
            and block.confidence is not None
            and float(block.confidence) < 0.7
            for block in referenced_blocks
        ):
            warnings.append("OCR_TEXT_LOW_CONFIDENCE_REVIEW_REQUIRED")
            manual = True
        referenced_orders_by_page: dict[uuid.UUID, set[int]] = {}
        for block in referenced_blocks:
            referenced_orders_by_page.setdefault(block.paper_page_id, set()).add(
                block.display_order - 1
            )
        candidate_math_risks: set[str] = set()
        for region in source_regions:
            candidate_region = (
                float(region.x),
                float(region.y),
                float(region.width),
                float(region.height),
            )
            referenced_orders = referenced_orders_by_page.get(region.paper_page_id, set())
            for code, block_indexes, risk_region in page_math_risks.get(region.paper_page_id, []):
                if block_indexes & referenced_orders or _regions_overlap(
                    candidate_region, risk_region
                ):
                    candidate_math_risks.add(code)
        if candidate_math_risks:
            warnings.extend(sorted(candidate_math_risks))
            manual = True
        if conflict_block_ids:
            warnings.append("SOURCE_TEXT_CONFLICT_REVIEW_REQUIRED")
            manual = True
        if source.source.startswith("mixed:"):
            warnings.append("MIXED_TEXT_SOURCE_REVIEW_REQUIRED")
            manual = True
        if any(
            page_math_symbol_conflicts.get(region.paper_page_id, 0) > 0 for region in source_regions
        ):
            warnings.append("MATH_SYMBOL_SOURCE_CONFLICT")
            manual = True
        quality_stats = text_quality_statistics(
            [source.content_text, source.content_latex],
            sources=[source.source],
            confidences=[
                float(block.confidence) if block.confidence is not None else None
                for block in referenced_blocks
            ],
            block_types=[block.block_type for block in referenced_blocks],
        )
        if quality_stats["suspicious_character_count"]:
            warnings.append("CHARACTER_ENCODING_CORRUPTION_DETECTED")
            manual = True
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
            source_recognition_job_id=source_recognition_id,
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
            extraction_method=(
                "pdf_text_anchor"
                if source.source.startswith("pdf_text:")
                else (
                    "mixed_text_anchor"
                    if source.source.startswith("mixed:")
                    else "printed_text_ocr_anchor"
                )
            ),
            evidence={
                "untrusted_document_content": True,
                "source_candidate_id": str(source.id),
                "recognition_block_ids": block_ids,
                "source_conflict_block_ids": conflict_block_ids,
                "page_quality": {
                    str(page_id): {"level": level, "issues": issues}
                    for page_id, (level, issues) in region_quality.items()
                },
                "quality_stats": quality_stats,
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
        "recognition_job_ids": sorted(str(value) for value in recognition_ids),
        "prompt_injection_detected": injection,
        "question_number_conflict": any(value > 1 for key, value in number_counts.items() if key),
    }


def build_fake_candidates(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision
) -> dict[str, Any]:
    """Compatibility wrapper; fake recognition output is intentionally rejected."""
    return build_local_candidates(db, job, revision)


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
