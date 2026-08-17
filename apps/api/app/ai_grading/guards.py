"""Server-side safety gates for subjective AI grading.

These guards deliberately do not trust provider confidence or provider-declared
status.  They are usable as pure functions in tests and by API/worker code.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.api.domain import ApiProblem


class ErrorCodes:
    MANUAL_ONLY = "MANUAL_ONLY"
    ANSWER_NOT_CONFIRMED = "ANSWER_NOT_CONFIRMED"
    EVIDENCE_UNREADABLE = "EVIDENCE_UNREADABLE"
    EVIDENCE_UNKNOWN = "EVIDENCE_UNKNOWN"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    VALIDATION_MISSING = "VALIDATION_MISSING"
    VALIDATION_STALE = "VALIDATION_STALE"
    VALIDATION_CONFLICT = "VALIDATION_CONFLICT"
    RUBRIC_VERSION_STALE = "RUBRIC_VERSION_STALE"
    ANSWER_VERSION_STALE = "ANSWER_VERSION_STALE"
    QUESTION_TYPE_UNSUPPORTED = "QUESTION_TYPE_UNSUPPORTED"
    SCORE_OUT_OF_RANGE = "SCORE_OUT_OF_RANGE"
    SCORE_STEP_INVALID = "SCORE_STEP_INVALID"
    CRITERION_SET_INVALID = "CRITERION_SET_INVALID"
    CRITERION_TOTAL_INVALID = "CRITERION_TOTAL_INVALID"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    FINALIZED_SUBMISSION = "FINALIZED_SUBMISSION"
    VOIDED_SUBMISSION = "VOIDED_SUBMISSION"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    CSRF_REQUIRED = "CSRF_REQUIRED"


PUBLIC_STATUS = ("scored", "abstain", "manual", "conflict", "insufficient", "failed", "stale")
NON_SCORING_MODES = {
    "manual_only": "manual",
    "unsupported": "abstain",
    "indeterminate": "insufficient",
    "timeout": "failed",
    "provider_unavailable": "failed",
}


@dataclass(frozen=True)
class GuardViolation(ValueError):
    code: str
    message: str
    status: str = "manual"

    def __str__(self) -> str:
        return self.message

    def problem(self, http_status: int = 422) -> ApiProblem:
        return ApiProblem(http_status, self.code, self.message)


def public_status(
    internal: str | None,
    *,
    points: Decimal | None = None,
    stale: bool = False,
    error_code: str | None = None,
) -> str:
    if stale or error_code in {ErrorCodes.EVIDENCE_STALE, ErrorCodes.VALIDATION_STALE,
                                ErrorCodes.RUBRIC_VERSION_STALE, ErrorCodes.ANSWER_VERSION_STALE}:
        return "stale"
    if error_code == ErrorCodes.VALIDATION_CONFLICT or internal in {
        "deterministic_conflict",
        "conflict",
    }:
        return "conflict"
    if internal in {"manual_required", "manual", "manual_review"}:
        return "manual"
    if internal in {"insufficient_evidence", "insufficient"}:
        return "insufficient"
    if internal in {"failed", "error"}:
        return "failed"
    if internal in {"suggested_pass", "suggested_partial", "suggested_fail", "scored"}:
        return "scored" if points is not None else "insufficient"
    return "abstain"


def require_owner(actual_owner: Any, expected_owner: Any) -> None:
    if actual_owner != expected_owner:
        raise GuardViolation(ErrorCodes.OWNER_MISMATCH, "Resource owner mismatch")


def require_submission_mutable(submission: Any) -> None:
    if submission is None:
        raise GuardViolation("SUBMISSION_NOT_FOUND", "Submission not found")
    status = getattr(submission, "status", None)
    if getattr(submission, "finalized_at", None) is not None or status == "finalized":
        raise GuardViolation(
            ErrorCodes.FINALIZED_SUBMISSION,
            "Finalized submissions are read-only",
            status="stale",
        )
    if status == "voided":
        raise GuardViolation(
            ErrorCodes.VOIDED_SUBMISSION,
            "Voided submissions are read-only",
            status="stale",
        )


def require_answer_relation(answer: Any, submission: Any, question: Any, owner_id: Any) -> None:
    if answer is None or submission is None or question is None:
        raise GuardViolation(
            "AI_GRADING_INPUT_NOT_FOUND", "Answer, submission or question not found"
        )
    require_owner(getattr(submission, "owner_id", None), owner_id)
    if getattr(answer, "submission_id", None) != getattr(submission, "id", None):
        raise GuardViolation("ANSWER_SUBMISSION_MISMATCH", "Answer does not belong to submission")
    if getattr(answer, "question_id", None) != getattr(question, "id", None):
        raise GuardViolation("ANSWER_QUESTION_MISMATCH", "Answer does not belong to question")


def require_confirmed_answer(answer: Any) -> None:
    if getattr(answer, "status", None) != "confirmed":
        raise GuardViolation(ErrorCodes.ANSWER_NOT_CONFIRMED, "Confirmed answer required")


def validate_evidence_refs(
    refs: Iterable[str],
    known_ids: set[str],
    *,
    stale_ids: set[str] | None = None,
    unreadable_ids: set[str] | None = None,
) -> None:
    values = list(refs)
    if len(values) != len(set(values)):
        raise GuardViolation(ErrorCodes.EVIDENCE_UNKNOWN, "Duplicate evidence reference")
    stale_ids = stale_ids or set()
    unreadable_ids = unreadable_ids or set()
    if any(x in stale_ids for x in values):
        raise GuardViolation(ErrorCodes.EVIDENCE_STALE, "Stale evidence cannot be used", "stale")
    if any(x in unreadable_ids for x in values):
        raise GuardViolation(
            ErrorCodes.EVIDENCE_UNREADABLE, "Unreadable evidence cannot be used", "insufficient"
        )
    if not set(values) <= known_ids:
        raise GuardViolation(ErrorCodes.EVIDENCE_UNKNOWN, "Unknown evidence reference")


def validate_score(score: Decimal | None, maximum: Decimal, step: Decimal | None = None) -> None:
    if score is None:
        return
    if score < 0 or score > maximum:
        raise GuardViolation(ErrorCodes.SCORE_OUT_OF_RANGE, "Score out of range")
    if step and step > 0 and score % step != 0:
        raise GuardViolation(ErrorCodes.SCORE_STEP_INVALID, "Score violates criterion step")


def validate_criterion_set(
    keys: Iterable[str], rubric_keys: Iterable[str], *, complete: bool = True
) -> None:
    actual = list(keys)
    allowed = set(rubric_keys)
    if (
        len(actual) != len(set(actual))
        or not set(actual) <= allowed
        or (complete and set(actual) != allowed)
    ):
        raise GuardViolation(
            ErrorCodes.CRITERION_SET_INVALID, "Criterion set is duplicate, unknown or incomplete"
        )


def validate_total(
    scores: dict[str, Decimal | None],
    maxima: dict[str, Decimal],
    total: Decimal | None,
) -> None:
    if total is None:
        return
    if set(scores) != set(maxima) or any(value is None for value in scores.values()):
        raise GuardViolation(
            ErrorCodes.CRITERION_TOTAL_INVALID, "Total requires all criteria scored"
        )
    expected = sum((value for value in scores.values() if value is not None), Decimal("0"))
    if total != expected:
        raise GuardViolation(ErrorCodes.CRITERION_TOTAL_INVALID, "Criterion total mismatch")


def require_non_scoring_mode(mode: str | None) -> str | None:
    if mode in NON_SCORING_MODES:
        return NON_SCORING_MODES[mode]
    return None


def validate_validation_link(
    validation_job: Any,
    result: Any,
    *,
    answer_id: Any,
    rubric_id: Any,
    reference_id: Any,
    criterion_id: Any,
) -> None:
    if validation_job is None or result is None:
        raise GuardViolation(ErrorCodes.VALIDATION_MISSING, "Current validation result required")
    if getattr(validation_job, "stale_at", None) or getattr(result, "stale_at", None):
        raise GuardViolation(ErrorCodes.VALIDATION_STALE, "Validation result is stale", "stale")
    if (
        getattr(validation_job, "student_answer_id", None) != answer_id
        or getattr(validation_job, "rubric_version_id", None) != rubric_id
        or getattr(validation_job, "reference_answer_version_id", None) != reference_id
        or getattr(result, "criterion_id", None) != criterion_id
        or getattr(result, "generation", None) != getattr(validation_job, "generation", None)
    ):
        raise GuardViolation(ErrorCodes.VALIDATION_STALE, "Validation version mismatch", "stale")
    if getattr(result, "result", None) in {"conflict", "indeterminate", "timeout", "error"}:
        raise GuardViolation(
            ErrorCodes.VALIDATION_CONFLICT,
            "Validation does not permit a scored suggestion",
            "conflict",
        )
