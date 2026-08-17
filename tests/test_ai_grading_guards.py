from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.ai_grading.guards import (
    ErrorCodes,
    GuardViolation,
    public_status,
    require_submission_mutable,
    validate_evidence_refs,
    validate_score,
    validate_total,
    validate_validation_link,
)


def test_public_status_never_exposes_legacy_meanings() -> None:
    assert public_status("suggested_partial", points=Decimal("1")) == "scored"
    assert public_status("manual_required") == "manual"
    assert public_status("deterministic_conflict") == "conflict"
    assert public_status("suggested_pass", stale=True, points=Decimal("1")) == "stale"


def test_evidence_duplicate_unknown_and_stale_fail_closed() -> None:
    with pytest.raises(GuardViolation) as duplicate:
        validate_evidence_refs(["a", "a"], {"a"})
    assert duplicate.value.code == ErrorCodes.EVIDENCE_UNKNOWN
    with pytest.raises(GuardViolation) as unknown:
        validate_evidence_refs(["x"], {"a"})
    assert unknown.value.code == ErrorCodes.EVIDENCE_UNKNOWN
    with pytest.raises(GuardViolation) as stale:
        validate_evidence_refs(["a"], {"a"}, stale_ids={"a"})
    assert stale.value.code == ErrorCodes.EVIDENCE_STALE


def test_score_step_and_total_require_complete_scored_criteria() -> None:
    with pytest.raises(GuardViolation) as out:
        validate_score(Decimal("4"), Decimal("3"))
    assert out.value.code == ErrorCodes.SCORE_OUT_OF_RANGE
    with pytest.raises(GuardViolation) as step:
        validate_score(Decimal("1.5"), Decimal("3"), Decimal("1"))
    assert step.value.code == ErrorCodes.SCORE_STEP_INVALID
    with pytest.raises(GuardViolation) as partial:
        validate_total(
            {"a": Decimal("1"), "b": None},
            {"a": Decimal("1"), "b": Decimal("1")},
            Decimal("1"),
        )
    assert partial.value.code == ErrorCodes.CRITERION_TOTAL_INVALID


def test_finalized_submission_is_read_only() -> None:
    with pytest.raises(GuardViolation) as exc:
        require_submission_mutable(SimpleNamespace(finalized_at=object()))
    assert exc.value.code == ErrorCodes.FINALIZED_SUBMISSION


def test_voided_submission_is_read_only() -> None:
    with pytest.raises(GuardViolation) as exc:
        require_submission_mutable(SimpleNamespace(finalized_at=None, status="voided"))
    assert exc.value.code == ErrorCodes.VOIDED_SUBMISSION


def test_validation_link_rejects_stale_and_conflict() -> None:
    job = SimpleNamespace(
        student_answer_id="answer",
        rubric_version_id="rubric",
        reference_answer_version_id="reference",
        generation=2,
        stale_at=None,
    )
    stale = SimpleNamespace(
        criterion_id="criterion", generation=1, stale_at=None, result="verified"
    )
    with pytest.raises(GuardViolation) as stale_exc:
        validate_validation_link(
            job,
            stale,
            answer_id="answer",
            rubric_id="rubric",
            reference_id="reference",
            criterion_id="criterion",
        )
    assert stale_exc.value.code == ErrorCodes.VALIDATION_STALE
    conflict = SimpleNamespace(
        criterion_id="criterion", generation=2, stale_at=None, result="indeterminate"
    )
    with pytest.raises(GuardViolation) as conflict_exc:
        validate_validation_link(
            job,
            conflict,
            answer_id="answer",
            rubric_id="rubric",
            reference_id="reference",
            criterion_id="criterion",
        )
    assert conflict_exc.value.code == ErrorCodes.VALIDATION_CONFLICT
