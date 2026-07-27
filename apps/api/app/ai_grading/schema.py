from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai_grading.guards import (
    GuardViolation,
    validate_evidence_refs,
    validate_score,
    validate_total,
)

InternalStatus = Literal[
    "suggested_pass",
    "suggested_partial",
    "suggested_fail",
    "abstain",
    "insufficient_evidence",
    "deterministic_conflict",
    "manual_required",
    "abstain",
]
Status = Literal["scored", "abstain", "manual", "conflict", "insufficient", "failed", "stale"]
Error = Literal[
    "conceptual_error",
    "arithmetic_error",
    "algebraic_manipulation_error",
    "sign_error",
    "dimension_error",
    "notation_error",
    "missing_justification",
    "incomplete_proof",
    "invalid_inference",
    "irrelevant_step",
    "correct_method_wrong_result",
    "correct_result_missing_process",
    "unreadable_evidence",
    "formula_recognition_uncertain",
    "rubric_not_applicable",
    "other",
]


class CriterionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_stable_key: str = Field(min_length=1, max_length=80)
    status: InternalStatus
    suggested_points: Decimal | None
    max_points: Decimal = Field(gt=0)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    decision: str = Field(min_length=1, max_length=80)
    evidence_refs: list[str]
    matched_steps: list[str] = Field(default_factory=list)
    missing_steps: list[str] = Field(default_factory=list)
    detected_errors: list[Error] = Field(default_factory=list)
    reasoning_summary: str = Field(max_length=2000)
    manual_review_reason: str | None = Field(default=None, max_length=1000)
    student_feedback: str = Field(default="", max_length=2000)
    teacher_note: str = Field(default="", max_length=2000)
    abstained: bool = False

    @model_validator(mode="after")
    def coherent(self) -> Self:
        abstain = self.status in {"abstain", "insufficient_evidence", "manual_required"}
        if abstain and self.suggested_points is not None:
            raise ValueError("abstention/manual status cannot carry points")
        if self.suggested_points is not None and not 0 <= self.suggested_points <= self.max_points:
            raise ValueError("suggested points out of range")
        if self.status == "deterministic_conflict" and not self.manual_review_reason:
            raise ValueError("deterministic conflict requires a review reason")
        if not self.evidence_refs and self.status not in {"abstain", "insufficient_evidence"}:
            raise ValueError("scored suggestion requires evidence")
        return self

    def public_status(self, *, stale: bool = False) -> Status:
        """Map legacy/provider statuses to the stable API vocabulary."""
        if stale:
            return "stale"
        if self.status in {"suggested_pass", "suggested_partial", "suggested_fail"}:
            return "scored" if self.suggested_points is not None else "insufficient"
        if self.status == "deterministic_conflict":
            return "conflict"
        if self.status == "manual_required":
            return "manual"
        if self.status == "insufficient_evidence":
            return "insufficient"
        return "abstain"


class AIGradingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["criterion-suggestion-v1"]
    criteria: list[CriterionSuggestion]
    total_suggested_points: Decimal | None
    student_feedback: str = Field(default="", max_length=4000)
    teacher_summary: str = Field(default="", max_length=4000)
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class ValidationContext(BaseModel):
    criterion_maxima: dict[str, Decimal]
    evidence_ids: set[str]
    manual_only: set[str] = Field(default_factory=set)
    deterministic: dict[str, str] = Field(default_factory=dict)
    step_sizes: dict[str, Decimal] = Field(default_factory=dict)
    question_max_points: Decimal | None = None
    criterion_keys: set[str] | None = None
    validation_refs: dict[str, str] = Field(default_factory=dict)
    unsupported: set[str] = Field(default_factory=set)


def validate_output(raw: object, context: ValidationContext) -> AIGradingOutput:
    out = AIGradingOutput.model_validate(raw)
    seen: set[str] = set()
    total = Decimal("0")
    has_abstain = False
    for item in out.criteria:
        key = item.criterion_stable_key
        if key in seen or key not in context.criterion_maxima:
            raise ValueError("duplicate or unknown criterion")
        seen.add(key)
        if item.max_points != context.criterion_maxima[key]:
            raise ValueError("criterion maximum mismatch")
        step = context.step_sizes.get(key)
        try:
            validate_score(item.suggested_points, context.criterion_maxima[key], step)
            validate_evidence_refs(item.evidence_refs, context.evidence_ids)
        except GuardViolation as exc:
            raise ValueError(str(exc)) from exc
        if key in context.manual_only and item.status != "manual_required":
            raise ValueError("manual-only criterion must remain manual")
        if key in context.unsupported and item.suggested_points is not None:
            raise ValueError("unsupported criterion cannot be scored")
        expected = context.deterministic.get(key)
        if expected and item.status not in {expected, "deterministic_conflict", "manual_required"}:
            raise ValueError("AI result conflicts with deterministic fact without conflict flag")
        if item.suggested_points is None:
            has_abstain = True
        else:
            total += item.suggested_points
        if (item.confidence or Decimal("0")) > Decimal(".8") and not item.evidence_refs:
            raise ValueError("high confidence requires evidence")
    if context.criterion_keys is not None and seen != context.criterion_keys:
        raise ValueError("criterion set is incomplete or invalid")
    if has_abstain and out.total_suggested_points is not None:
        raise ValueError("partial abstention cannot claim a total")
    try:
        validate_total(
            {x.criterion_stable_key: x.suggested_points for x in out.criteria},
            context.criterion_maxima,
            out.total_suggested_points,
        )
    except GuardViolation as exc:
        raise ValueError("criterion sum mismatch") from exc
    if not has_abstain and out.total_suggested_points != total:
        raise ValueError("criterion sum mismatch")
    if (
        out.total_suggested_points is not None
        and context.question_max_points is not None
        and out.total_suggested_points > context.question_max_points
    ):
        raise ValueError("suggested total exceeds question maximum")
    return out
