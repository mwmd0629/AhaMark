"""Transactional semantic validation and draft-only Provider DTO materialization."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assignment_generation.answer_rubric import (
    AnswerRubricProviderOutput,
    question_version,
    route_scoring_mode,
    validate_candidate_structure,
)
from app.assignment_generation.question_extraction import ExtractionOutput
from app.assignment_generation.schemas import FileAnalysisOutput, MetadataProviderOutput
from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentDraftRevision,
    AssignmentFieldSuggestion,
    AssignmentGenerationJob,
    AssignmentPageAnalysis,
    AssignmentQuestionExtractionCandidate,
    AssignmentQuestionExtractionRegion,
    AssignmentRubricCriterionDraft,
    AssignmentRubricDraftCandidate,
    AssignmentSourceFileAnalysis,
    PaperPage,
    PaperPageOrganizationSuggestion,
    PaperVersion,
    Question,
    QuestionCandidate,
    RecognitionBlock,
    RecognitionJob,
    StoredFile,
    now_utc,
)


class ProviderSemanticError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _uuid(value: str | uuid.UUID, code: str = "PROVIDER_EVIDENCE_INVALID") -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise ProviderSemanticError(code) from exc


def _paper_version(db: Session, job: AssignmentGenerationJob) -> PaperVersion:
    assignment = db.get(Assignment, job.assignment_id)
    if (
        assignment is None
        or assignment.owner_id != job.owner_id
        or getattr(assignment.status, "value", assignment.status) == "published"
        or assignment.active_paper_version_id is None
    ):
        raise ProviderSemanticError("PROVIDER_OUTPUT_DISCARDED")
    paper = db.get(PaperVersion, assignment.active_paper_version_id)
    if paper is None or paper.assignment_id != assignment.id:
        raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")
    return paper


def _known_entities(db: Session, job: AssignmentGenerationJob) -> dict[str, set[str]]:
    paper = _paper_version(db, job)
    pages = list(db.scalars(select(PaperPage).where(PaperPage.paper_version_id == paper.id)))
    file_ids = {page.stored_file_id for page in pages}
    blocks = list(
        db.scalars(
            select(RecognitionBlock).where(
                RecognitionBlock.paper_page_id.in_({page.id for page in pages})
            )
        )
    )
    return {
        "file": {str(value) for value in file_ids},
        "page": {str(page.id) for page in pages},
        "block": {str(block.id) for block in blocks},
        "ocr_region": {str(block.id) for block in blocks},
        "region": {str(block.id) for block in blocks},
        "question": set(),
    }


def _validate_evidence(
    refs: Iterable[Any], known: dict[str, set[str]], *, question_id: uuid.UUID | None = None
) -> None:
    if question_id is not None:
        known = {**known, "question": {str(question_id)}}
    for ref in refs:
        kind = str(ref.kind)
        identifier = str(ref.reference_id)
        if kind in {"derived", "assignment_field", "file_name"}:
            continue
        if identifier not in known.get(kind, set()):
            raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")


def materialize_metadata(
    db: Session,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision,
    output: MetadataProviderOutput,
) -> int:
    known = _known_entities(db, job)
    created = 0
    for candidate in output.suggestions:
        _validate_evidence(candidate.evidence, known)
        previous = list(
            db.scalars(
                select(AssignmentFieldSuggestion)
                .where(
                    AssignmentFieldSuggestion.draft_revision_id == revision.id,
                    AssignmentFieldSuggestion.field_name == candidate.field_name,
                )
                .with_for_update()
            )
        )
        for old in previous:
            if old.status == "suggested":
                old.status = "superseded"
        version = max((old.suggestion_version for old in previous), default=0) + 1
        db.add(
            AssignmentFieldSuggestion(
                owner_id=job.owner_id,
                assignment_id=job.assignment_id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                field_name=candidate.field_name,
                suggested_value=candidate.suggested_value,
                normalized_value=candidate.normalized_value,
                confidence=candidate.confidence,
                evidence=[item.model_dump(mode="json") for item in candidate.evidence],
                source_type="provider",
                source_stage="analyzing",
                suggestion_version=version,
            )
        )
        created += 1
    return created


def materialize_file_analysis(
    db: Session,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision,
    output: FileAnalysisOutput,
) -> dict[str, int]:
    paper = _paper_version(db, job)
    pages = list(db.scalars(select(PaperPage).where(PaperPage.paper_version_id == paper.id)))
    pages_by_id = {page.id: page for page in pages}
    file_ids = {page.stored_file_id for page in pages}
    files = {
        item.id: item
        for item in db.scalars(select(StoredFile).where(StoredFile.id.in_(file_ids))).all()
    }
    known = _known_entities(db, job)
    analyses: dict[uuid.UUID, AssignmentSourceFileAnalysis] = {}
    for file_candidate in output.files:
        file_id = _uuid(file_candidate.stored_file_id)
        stored = files.get(file_id)
        if (
            stored is None
            or stored.owner_id != job.owner_id
            or stored.checksum != file_candidate.checksum
        ):
            raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")
        duplicate_id = (
            _uuid(file_candidate.duplicate_of_file_id)
            if file_candidate.duplicate_of_file_id
            else None
        )
        if duplicate_id is not None and duplicate_id not in files:
            raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")
        _validate_evidence(file_candidate.evidence, known)
        old_file_rows = list(
            db.scalars(
                select(AssignmentSourceFileAnalysis)
                .where(
                    AssignmentSourceFileAnalysis.draft_revision_id == revision.id,
                    AssignmentSourceFileAnalysis.stored_file_id == file_id,
                )
                .with_for_update()
            )
        )
        for old_file_analysis in old_file_rows:
            if old_file_analysis.analysis_status == "suggested":
                old_file_analysis.analysis_status = "superseded"
        answer_source = file_candidate.suggested_answer_source
        warnings = list(file_candidate.warning_codes)
        if answer_source in {"teacher_official", "publisher_official"}:
            answer_source = "unknown"
            warnings.append("ANSWER_SOURCE_CONFIRMATION_REQUIRED")
        row = AssignmentSourceFileAnalysis(
            owner_id=job.owner_id,
            assignment_id=job.assignment_id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            stored_file_id=file_id,
            source_snapshot_hash=job.source_snapshot_hash,
            detected_mime_type=file_candidate.detected_mime_type,
            checksum=file_candidate.checksum,
            page_count=file_candidate.page_count,
            suggested_role=file_candidate.suggested_role,
            role_confidence=file_candidate.role_confidence,
            suggested_answer_source=answer_source,
            answer_source_confidence=file_candidate.answer_source_confidence,
            duplicate_of_file_id=duplicate_id,
            evidence=[item.model_dump(mode="json") for item in file_candidate.evidence],
            warning_codes=sorted(set(warnings)),
        )
        db.add(row)
        db.flush()
        analyses[file_id] = row
    page_count = 0
    suggestion_count = 0
    for page_candidate in output.pages:
        page_id = _uuid(page_candidate.paper_page_id)
        file_id = _uuid(page_candidate.stored_file_id)
        page = pages_by_id.get(page_id)
        analysis = analyses.get(file_id)
        if page is None or page.stored_file_id != file_id or analysis is None:
            raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")
        duplicate_page_id = (
            _uuid(page_candidate.duplicate_of_page_id)
            if page_candidate.duplicate_of_page_id
            else None
        )
        if duplicate_page_id is not None and duplicate_page_id not in pages_by_id:
            raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")
        _validate_evidence(page_candidate.evidence, known)
        db.add(
            AssignmentPageAnalysis(
                owner_id=job.owner_id,
                assignment_id=job.assignment_id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                paper_page_id=page_id,
                source_file_analysis_id=analysis.id,
                source_snapshot_hash=job.source_snapshot_hash,
                status=page_candidate.status,
                quality_score=page_candidate.quality_score,
                blank_probability=page_candidate.blank_probability,
                duplicate_probability=page_candidate.duplicate_probability,
                duplicate_of_page_id=duplicate_page_id,
                missing_page_suspected=page_candidate.missing_page_suspected,
                low_quality=page_candidate.low_quality,
                corrupted=page_candidate.corrupted,
                mixed_document_suspected=page_candidate.mixed_document_suspected,
                variant_label=page_candidate.variant_label,
                metrics=page_candidate.metrics,
                evidence=[item.model_dump(mode="json") for item in page_candidate.evidence],
                warning_codes=page_candidate.warning_codes,
            )
        )
        previous_page_suggestions = list(
            db.scalars(
                select(PaperPageOrganizationSuggestion)
                .where(
                    PaperPageOrganizationSuggestion.draft_revision_id == revision.id,
                    PaperPageOrganizationSuggestion.paper_page_id == page_id,
                )
                .with_for_update()
            )
        )
        for old_page_suggestion in previous_page_suggestions:
            if old_page_suggestion.status == "suggested":
                old_page_suggestion.status = "superseded"
        version = (
            max((item.suggestion_version for item in previous_page_suggestions), default=0) + 1
        )
        suggested_status = "excluded" if page_candidate.status == "blank" else page.status
        db.add(
            PaperPageOrganizationSuggestion(
                owner_id=job.owner_id,
                assignment_id=job.assignment_id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                paper_version_id=paper.id,
                paper_page_id=page.id,
                suggestion_version=version,
                suggested_page_number=page.page_number,
                suggested_rotation=page.rotation,
                suggested_status=suggested_status,
                duplicate_of_page_id=duplicate_page_id,
                variant_label=page_candidate.variant_label,
                confidence=page_candidate.quality_score
                if page_candidate.quality_score is not None
                else 0.5,
                reason_codes=page_candidate.warning_codes,
                evidence=[item.model_dump(mode="json") for item in page_candidate.evidence],
                source_snapshot_hash=job.source_snapshot_hash,
            )
        )
        page_count += 1
        suggestion_count += 1
    return {"files": len(analyses), "pages": page_count, "page_suggestions": suggestion_count}


def materialize_questions(
    db: Session,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision,
    output: ExtractionOutput,
) -> dict[str, int]:
    paper = _paper_version(db, job)
    pages = {
        page.id: page
        for page in db.scalars(select(PaperPage).where(PaperPage.paper_version_id == paper.id))
    }
    recognition = db.scalar(
        select(RecognitionJob)
        .where(RecognitionJob.paper_version_id == paper.id)
        .order_by(RecognitionJob.created_at.desc())
    )
    known_blocks = set(
        db.scalars(
            select(RecognitionBlock.id).where(RecognitionBlock.paper_page_id.in_(set(pages)))
        )
    )
    for candidate in output.candidates:
        for region in candidate.regions:
            if region.page_id not in pages or not set(region.block_ids) <= known_blocks:
                raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")
    old_rows = list(
        db.scalars(
            select(AssignmentQuestionExtractionCandidate)
            .where(AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id)
            .with_for_update()
        )
    )
    for old in old_rows:
        if old.status == "suggested":
            old.status = "superseded"
    version = max((old.candidate_version for old in old_rows), default=0) + 1
    number_counts = Counter(
        item.question_number for item in output.candidates if item.question_number
    )
    by_ref: dict[str, AssignmentQuestionExtractionCandidate] = {}
    manual_count = 0
    for candidate in output.candidates:
        kinds = {region.region_type for region in candidate.regions}
        warnings = set(candidate.warning_codes)
        if candidate.question_number and number_counts[candidate.question_number] > 1:
            warnings.add("QUESTION_NUMBER_CONFLICT")
        if candidate.max_score is None:
            warnings.add("QUESTION_SCORE_MISSING")
        if candidate.question_type == "proof":
            warnings.add("PROOF_MANUAL_REVIEW")
        for kind in kinds & {"formula", "figure", "table"}:
            warnings.add(f"{kind.upper()}_REVIEW_REQUIRED")
        if len({region.page_id for region in candidate.regions}) > 1:
            warnings.add("CROSS_PAGE_REVIEW_REQUIRED")
        if len(candidate.regions) > 1:
            warnings.add("MULTI_REGION_REVIEW_REQUIRED")
        manual = (
            candidate.manual_required
            or bool(kinds & {"formula", "figure", "table"})
            or candidate.question_type == "proof"
        )
        source_id = candidate.source_candidate_id
        if source_id is not None:
            source = db.get(QuestionCandidate, source_id)
            if source is None or source.paper_version_id != paper.id:
                raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")
        row = AssignmentQuestionExtractionCandidate(
            owner_id=job.owner_id,
            assignment_id=job.assignment_id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            paper_version_id=paper.id,
            source_recognition_job_id=recognition.id if recognition else None,
            source_question_candidate_id=source_id,
            candidate_version=version,
            question_number=candidate.question_number,
            question_type=candidate.question_type,
            content_text=candidate.content_text,
            content_latex=None if "formula" in kinds else candidate.content_latex,
            max_score=candidate.max_score,
            difficulty=candidate.difficulty,
            knowledge_point_suggestions=candidate.knowledge_points,
            field_confidences={
                key: str(value) for key, value in candidate.field_confidences.items()
            },
            overall_confidence=candidate.overall_confidence,
            extraction_method="openai_compatible",
            evidence=candidate.evidence,
            warning_codes=sorted(warnings),
            manual_required=manual,
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(row)
        db.flush()
        by_ref[candidate.ref] = row
        manual_count += int(manual)
        for region in candidate.regions:
            db.add(
                AssignmentQuestionExtractionRegion(
                    candidate_id=row.id,
                    paper_page_id=region.page_id,
                    display_order=region.display_order,
                    region_type=region.region_type,
                    x=region.x,
                    y=region.y,
                    width=region.width,
                    height=region.height,
                    confidence=region.confidence,
                    evidence=region.evidence,
                    source_block_ids=[str(value) for value in region.block_ids],
                    cross_page_group=region.cross_page_group,
                )
            )
    for candidate in output.candidates:
        if candidate.parent_ref:
            by_ref[candidate.ref].parent_candidate_id = by_ref[candidate.parent_ref].id
    return {"created": len(by_ref), "manual_required": manual_count}


def materialize_answer(
    db: Session,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision,
    question: Question,
    output: AnswerRubricProviderOutput,
    audit: dict[str, Any],
) -> AssignmentAnswerDraftCandidate:
    paper = _paper_version(db, job)
    if (
        question.paper_version_id != paper.id
        or getattr(question.status, "value", question.status) != "active"
    ):
        raise ProviderSemanticError("PROVIDER_EVIDENCE_INVALID")
    known = _known_entities(db, job)
    _validate_evidence(output.evidence, known, question_id=question.id)
    old_rows = list(
        db.scalars(
            select(AssignmentAnswerDraftCandidate)
            .where(
                AssignmentAnswerDraftCandidate.draft_revision_id == revision.id,
                AssignmentAnswerDraftCandidate.question_id == question.id,
            )
            .with_for_update()
        )
    )
    for old in old_rows:
        if old.status == "suggested":
            old.status = "superseded"
    version = max((old.candidate_version for old in old_rows), default=0) + 1
    _mode, routed_manual, route_warnings = route_scoring_mode(question, output)
    manual = routed_manual or output.raw_content is None
    row = AssignmentAnswerDraftCandidate(
        owner_id=job.owner_id,
        assignment_id=job.assignment_id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        question_id=question.id,
        question_version=question_version(question),
        candidate_version=version,
        source_type="ai_generated",
        raw_content=output.raw_content,
        normalized_content=output.normalized_content,
        structured_content=output.structured_content,
        alternative_answers=[item.model_dump(mode="json") for item in output.alternative_answers],
        provenance={
            "source_type": "ai_generated",
            "generation_job_id": str(job.id),
            "revision_id": str(revision.id),
            "question_id": str(question.id),
            "provider": "openai_compatible",
            "provider_config_version": job.provider_config_version,
            "prompt_version": job.prompt_version,
            "schema_version": job.schema_version,
            **audit,
            "created_at": now_utc().isoformat(),
        },
        confidence=output.confidence,
        evidence=[item.model_dump(mode="json") for item in output.evidence],
        warning_codes=sorted(set(output.warning_codes + route_warnings)),
        status="manual_required" if manual else "suggested",
        manual_required=manual,
        source_snapshot_hash=job.source_snapshot_hash,
    )
    db.add(row)
    db.flush()
    return row


def materialize_rubric(
    db: Session,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision,
    question: Question,
    answer: AssignmentAnswerDraftCandidate,
    output: AnswerRubricProviderOutput,
) -> AssignmentRubricDraftCandidate:
    known = _known_entities(db, job)
    _validate_evidence(output.evidence, known, question_id=question.id)
    for criterion in output.criteria:
        _validate_evidence(criterion.evidence, known, question_id=question.id)
    mode, routed_manual, route_warnings = route_scoring_mode(question, output)
    structural = validate_candidate_structure(output.total_points, mode, output.criteria)
    if not structural.valid:
        raise ProviderSemanticError("PROVIDER_SCHEMA_INVALID")
    old_rows = list(
        db.scalars(
            select(AssignmentRubricDraftCandidate)
            .where(
                AssignmentRubricDraftCandidate.draft_revision_id == revision.id,
                AssignmentRubricDraftCandidate.question_id == question.id,
            )
            .with_for_update()
        )
    )
    for old in old_rows:
        if old.status == "suggested":
            old.status = "superseded"
    version = max((old.candidate_version for old in old_rows), default=0) + 1
    rubric = AssignmentRubricDraftCandidate(
        owner_id=job.owner_id,
        assignment_id=job.assignment_id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        question_id=question.id,
        question_version=question_version(question),
        answer_candidate_id=answer.id,
        candidate_version=version,
        title=output.title,
        scoring_mode=mode,
        total_points=output.total_points,
        allow_partial_credit=output.allow_partial_credit,
        domain_requirements=output.domain_requirements,
        validation_config=output.validation_config,
        common_error_types=output.common_error_types,
        feedback_templates=output.feedback_templates,
        confidence=output.confidence,
        evidence=[item.model_dump(mode="json") for item in output.evidence],
        warning_codes=sorted(set(output.warning_codes + route_warnings)),
        status="manual_required" if routed_manual else "suggested",
        manual_required=routed_manual,
        source_snapshot_hash=job.source_snapshot_hash,
    )
    db.add(rubric)
    db.flush()
    for order, criterion in enumerate(output.criteria):
        db.add(
            AssignmentRubricCriterionDraft(
                rubric_candidate_id=rubric.id,
                display_order=order,
                **criterion.model_dump(exclude={"evidence"}),
                evidence=[item.model_dump(mode="json") for item in criterion.evidence],
            )
        )
    return rubric
