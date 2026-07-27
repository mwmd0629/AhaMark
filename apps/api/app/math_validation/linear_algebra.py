"""Explicit linear-algebra answer types and a safe validation bridge.

The provider/worker layer should use this module instead of passing arbitrary
``answer_type`` strings to the math engine.  The bridge deliberately returns a
non-scoring result for unsupported or malformed inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.math_validation.engine import Limits, ValidationOutcome, validate


@dataclass(frozen=True)
class ValidationRefs:
    answer_id: str
    criterion_id: str
    rubric_version_id: str
    reference_answer_version_id: str
    generation: int

    def matches(self, other: ValidationRefs | None) -> bool:
        return other is not None and self == other


@dataclass(frozen=True)
class LinearAlgebraResult:
    status: str
    answer_type: str
    reason: str | None
    error_code: str | None
    comparison_method: str
    evidence: dict[str, Any]
    diagnostics: dict[str, Any]
    refs: ValidationRefs | None = None
    engine_result: str | None = None

    def json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "answer_type": self.answer_type,
            "reason": self.reason,
            "error_code": self.error_code,
            "comparison_method": self.comparison_method,
            "evidence": self.evidence,
            "diagnostics": self.diagnostics,
            "refs": (
                {
                    "answer_id": self.refs.answer_id,
                    "criterion_id": self.refs.criterion_id,
                    "rubric_version_id": self.refs.rubric_version_id,
                    "reference_answer_version_id": self.refs.reference_answer_version_id,
                    "generation": self.refs.generation,
                }
                if self.refs
                else None
            ),
        }


# Stable public names.  Values are the deliberately smaller safe-engine
# vocabulary; no arbitrary rule can enter through this mapping.
ANSWER_TYPE_TO_ENGINE: dict[str, str] = {
    "matrix_addition": "matrix",
    "matrix_subtraction": "matrix",
    "matrix_multiplication": "matrix",
    "matrix_transpose": "matrix",
    "determinant": "determinant",
    "rank": "rank",
    "linear_system_solution": "linear_system_candidate",
    "linear_independence": "linear_independence",
    "span_basis": "subspace_basis",
    "eigenvalues": "eigenvalue_multiset",
    "eigenvectors": "eigenvector",
    "eigenspace": "eigenspace_basis",
    "diagonalization": "diagonalization",
}

MANUAL_TYPES = frozenset(
    {"proof", "proof_step", "manual_only", "jordan_form", "smith_normal_form", "open_derivation"}
)


def supported_answer_types() -> frozenset[str]:
    return frozenset(ANSWER_TYPE_TO_ENGINE)


def _result(
    status: str,
    answer_type: str,
    *,
    reason: str | None = None,
    error_code: str | None = None,
    comparison_method: str = "linear_algebra_registry",
    evidence: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    refs: ValidationRefs | None = None,
    engine_result: str | None = None,
) -> LinearAlgebraResult:
    return LinearAlgebraResult(
        status,
        answer_type,
        reason,
        error_code,
        comparison_method,
        evidence or {},
        diagnostics or {},
        refs,
        engine_result,
    )


def validate_linear_algebra(
    answer_type: str,
    rule: dict[str, Any],
    student: object,
    expected: object,
    *,
    refs: ValidationRefs | None = None,
    current_refs: ValidationRefs | None = None,
    limits: Limits | None = None,
) -> LinearAlgebraResult:
    """Validate one finite-check linear-algebra answer.

    ``refs`` are carried through unchanged so a caller can persist them with a
    CriterionValidationResult and the server guards can reject stale links.
    """
    if refs is not None and current_refs is not None and not refs.matches(current_refs):
        return _result(
            "stale",
            answer_type,
            reason="validation_reference_mismatch",
            error_code="VALIDATION_STALE",
            refs=refs,
            engine_result="stale",
        )
    if answer_type in MANUAL_TYPES:
        return _result(
            "manual",
            answer_type,
            reason="manual_review_required",
            error_code="MANUAL_ONLY",
            refs=refs,
            engine_result="manual_required",
        )
    engine_type = ANSWER_TYPE_TO_ENGINE.get(answer_type)
    if engine_type is None:
        return _result(
            "unsupported",
            answer_type,
            reason="unsupported_answer_type",
            error_code="QUESTION_TYPE_UNSUPPORTED",
            refs=refs,
            engine_result="indeterminate",
        )
    if not isinstance(rule, dict) or not isinstance(rule.get("domain"), str):
        return _result(
            "indeterminate",
            answer_type,
            reason="missing_explicit_domain",
            error_code="INVALID_VALIDATION_RULE",
            refs=refs,
            engine_result="invalid_input",
        )
    engine_rule = dict(rule)
    engine_rule["answer_type"] = engine_type
    # These types compare a supplied result against the expected operation
    # input.  The engine itself enforces all matrix dimensions and domains.
    outcome: ValidationOutcome = validate(engine_rule, student, expected, limits)
    if outcome.result == "verified_pass":
        status = "verified"
        error_code = None
    elif outcome.result == "verified_fail":
        status = "conflict"
        error_code = "VALIDATION_CONFLICT"
    elif outcome.result == "manual_required":
        status = "manual"
        error_code = "MANUAL_ONLY"
    elif outcome.result == "indeterminate":
        status = "indeterminate"
        error_code = "VALIDATION_INDETERMINATE"
    elif outcome.result == "timeout":
        status = "indeterminate"
        error_code = "VALIDATION_TIMEOUT"
    else:
        status = "indeterminate"
        error_code = "INVALID_MATH_INPUT"
    return _result(
        status,
        answer_type,
        reason=outcome.reason,
        error_code=error_code,
        comparison_method=outcome.comparison_method,
        evidence=outcome.evidence,
        diagnostics=outcome.diagnostics,
        refs=refs,
        engine_result=outcome.result,
    )
