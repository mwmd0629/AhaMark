"""Strict, draft-only question extraction and materialization helpers."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentQuestionExtractionCandidate,
    AssignmentQuestionExtractionRegion,
    PaperPage,
    Question,
    QuestionRegion,
    RecognitionBlock,
)
from app.recognition.text_integrity import ensure_text_fields_integrity

QUESTION_TYPES = {
    "single_choice",
    "multiple_choice",
    "true_false",
    "fill_blank",
    "calculation",
    "short_answer",
    "proof",
    "other",
}
FIELD_KEYS = {
    "question_number",
    "parent_relation",
    "question_type",
    "content_text",
    "content_latex",
    "max_score",
    "difficulty",
    "knowledge_points",
    "regions",
}
BLOCKING_WARNINGS = {
    "QUESTION_NUMBER_CONFLICT",
    "PARENT_CHILD_CONFLICT",
    "READING_ORDER_CONFLICT",
    "QUESTION_BOUNDARY_LOW_CONFIDENCE",
}
NON_ELIGIBLE_WARNINGS = BLOCKING_WARNINGS | {
    "CROSS_PAGE_REVIEW_REQUIRED",
    "MULTI_REGION_REVIEW_REQUIRED",
    "FORMULA_REVIEW_REQUIRED",
    "FIGURE_REVIEW_REQUIRED",
    "TABLE_REVIEW_REQUIRED",
    "PROOF_MANUAL_REVIEW",
    "QUESTION_SCORE_MISSING",
    "QUESTION_SCORE_CONFLICT",
}
INJECTION_RE = re.compile(
    r"(ignore (all |the )?previous|忽略.{0,8}(此前|以上|之前)|自动发布|选择班级|"
    r"system prompt|developer message)",
    re.I,
)


class RegionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: uuid.UUID
    display_order: int = Field(ge=0, le=999)
    region_type: Literal[
        "question_number",
        "stem",
        "subquestion",
        "score",
        "formula",
        "figure",
        "table",
        "instructions",
        "other",
    ]
    x: Decimal = Field(ge=0, le=1)
    y: Decimal = Field(ge=0, le=1)
    width: Decimal = Field(gt=0, le=1)
    height: Decimal = Field(gt=0, le=1)
    confidence: Decimal = Field(ge=0, le=1)
    block_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    evidence: dict[str, Any] = Field(default_factory=dict)
    cross_page_group: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def bounds(self) -> RegionOutput:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("region outside normalized page")
        return self


class CandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1, max_length=80)
    parent_ref: str | None = Field(None, max_length=80)
    source_candidate_id: uuid.UUID | None = None
    question_number: str | None = Field(None, max_length=80)
    question_type: Literal[
        "single_choice",
        "multiple_choice",
        "true_false",
        "fill_blank",
        "calculation",
        "short_answer",
        "proof",
        "other",
    ]
    content_text: str | None = Field(None, max_length=20000)
    content_latex: str | None = Field(None, max_length=20000)
    max_score: Decimal | None = Field(None, gt=0, le=100000)
    difficulty: str | None = Field(None, max_length=20)
    knowledge_points: list[str] = Field(default_factory=list, max_length=20)
    field_confidences: dict[str, Decimal]
    overall_confidence: Decimal = Field(ge=0, le=1)
    evidence: dict[str, Any] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list, max_length=50)
    manual_required: bool = False
    regions: list[RegionOutput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def safe_fields(self) -> CandidateOutput:
        if set(self.field_confidences) != FIELD_KEYS:
            raise ValueError("all field confidences are required")
        if any(v < 0 or v > 1 for v in self.field_confidences.values()):
            raise ValueError("invalid field confidence")
        if self.question_type == "proof":
            self.manual_required = True
        if self.content_latex is not None and "FORMULA_REVIEW_REQUIRED" in self.warning_codes:
            raise ValueError("unverified formula latex must be null")
        return self


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[CandidateOutput] = Field(max_length=500)

    @model_validator(mode="after")
    def refs(self) -> ExtractionOutput:
        refs = [x.ref for x in self.candidates]
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate candidate ref")
        known = set(refs)
        for item in self.candidates:
            if item.parent_ref == item.ref or (item.parent_ref and item.parent_ref not in known):
                raise ValueError("invalid parent ref")
        parents = {x.ref: x.parent_ref for x in self.candidates}
        for ref in refs:
            seen = set()
            cur = ref
            while parents.get(cur):
                if cur in seen:
                    raise ValueError("parent cycle")
                seen.add(cur)
                cur = parents[cur] or ""
        ensure_extraction_text_integrity(self)
        return self


def ensure_extraction_text_integrity(output: ExtractionOutput) -> None:
    """Validate the complete batch before callers perform any database writes."""

    fields: list[tuple[str, str | None]] = []
    for index, candidate in enumerate(output.candidates):
        prefix = f"candidates[{index}]"
        fields.extend(
            [
                (f"{prefix}.question_number", candidate.question_number),
                (f"{prefix}.content_text", candidate.content_text),
                (f"{prefix}.content_latex", candidate.content_latex),
                (f"{prefix}.difficulty", candidate.difficulty),
            ]
        )
        fields.extend(
            (f"{prefix}.knowledge_points[{point_index}]", point)
            for point_index, point in enumerate(candidate.knowledge_points)
        )
    ensure_text_fields_integrity(fields)


def validate_references(
    db: Session,
    output: ExtractionOutput,
    paper_version_id: uuid.UUID,
    recognition_job_id: uuid.UUID,
) -> None:
    page_ids = set(
        db.scalars(select(PaperPage.id).where(PaperPage.paper_version_id == paper_version_id)).all()
    )
    block_ids = set(
        db.scalars(
            select(RecognitionBlock.id).where(
                RecognitionBlock.recognition_job_id == recognition_job_id
            )
        ).all()
    )
    for candidate in output.candidates:
        for region in candidate.regions:
            if region.page_id not in page_ids:
                raise ValueError("region page outside paper version")
            if not set(region.block_ids) <= block_ids:
                raise ValueError("unknown recognition block reference")


def eligible(
    candidate: AssignmentQuestionExtractionCandidate,
    regions: list[AssignmentQuestionExtractionRegion],
    *,
    variant_unresolved: bool = False,
    threshold: float = 0.85,
) -> bool:
    if (
        variant_unresolved
        or candidate.status != "suggested"
        or candidate.manual_required
        or candidate.materialized_question_id
    ):
        return False
    if (
        float(candidate.overall_confidence) < threshold
        or not candidate.question_number
        or not candidate.content_text
        or candidate.max_score is None
    ):
        return False
    if candidate.question_type == "proof" or set(candidate.warning_codes) & NON_ELIGIBLE_WARNINGS:
        return False
    if not regions or any(float(r.confidence) < threshold for r in regions):
        return False
    if len({r.paper_page_id for r in regions}) > 1:
        return False
    return True


def materialize(
    db: Session,
    candidate: AssignmentQuestionExtractionCandidate,
    regions: list[AssignmentQuestionExtractionRegion],
    *,
    modified: bool = False,
) -> Question:
    if candidate.materialized_question_id:
        existing = db.get(Question, candidate.materialized_question_id)
        if existing:
            return existing
    values = candidate.teacher_value or {}
    next_display_order = (
        db.scalar(
            select(func.max(Question.display_order)).where(
                Question.paper_version_id == candidate.paper_version_id
            )
        )
        or 0
    ) + 1
    question = Question(
        paper_version_id=candidate.paper_version_id,
        parent_question_id=None,
        question_number=values.get("question_number", candidate.question_number) or "",
        display_order=next_display_order,
        question_type=values.get("question_type", candidate.question_type),
        content_text=values.get("content_text", candidate.content_text),
        content_latex=values.get("content_latex", candidate.content_latex),
        max_score=values.get("max_score", candidate.max_score),
        difficulty=values.get("difficulty", candidate.difficulty),
        source="teacher_ai_edit" if modified else "ai_draft",
    )
    db.add(question)
    db.flush()
    for region in regions:
        db.add(
            QuestionRegion(
                question_id=question.id,
                paper_page_id=region.paper_page_id,
                region_type=region.region_type,
                x=region.x,
                y=region.y,
                width=region.width,
                height=region.height,
                source="teacher_ai_edit" if modified else "ai_draft",
                confidence=region.confidence,
            )
        )
    candidate.materialized_question_id = question.id
    return question


def prompt_injection(text: str | None) -> bool:
    return bool(text and INJECTION_RE.search(text))
