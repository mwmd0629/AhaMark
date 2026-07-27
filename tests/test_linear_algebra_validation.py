from app.math_validation.engine import Limits
from app.math_validation.linear_algebra import (
    ANSWER_TYPE_TO_ENGINE,
    ValidationRefs,
    supported_answer_types,
    validate_linear_algebra,
)


def _refs(generation: int = 3) -> ValidationRefs:
    return ValidationRefs("answer", "criterion", "rubric", "reference", generation)


def test_registry_is_explicit_and_complete() -> None:
    assert supported_answer_types() == frozenset(ANSWER_TYPE_TO_ENGINE)
    assert {
        "matrix_addition",
        "matrix_subtraction",
        "matrix_multiplication",
        "matrix_transpose",
        "determinant",
        "rank",
        "linear_system_solution",
        "linear_independence",
        "span_basis",
        "eigenvalues",
        "eigenvectors",
        "eigenspace",
        "diagonalization",
    } <= supported_answer_types()
    outcome = validate_linear_algebra("unknown", {"domain": "rational"}, 1, 1)
    assert (outcome.status, outcome.error_code) == (
        "unsupported",
        "QUESTION_TYPE_UNSUPPORTED",
    )


def test_matrix_result_types_compare_safely_and_preserve_refs() -> None:
    rule = {"domain": "rational"}
    for answer_type in {
        "matrix_addition",
        "matrix_subtraction",
        "matrix_multiplication",
        "matrix_transpose",
    }:
        outcome = validate_linear_algebra(
            answer_type, rule, [[1, 2], [3, 4]], [[1, 2], [3, 4]], refs=_refs()
        )
        assert outcome.status == "verified"
        assert outcome.refs == _refs()
    mismatch = validate_linear_algebra("matrix_multiplication", rule, [[1, 2]], [[1], [2]])
    assert (mismatch.status, mismatch.reason) == ("conflict", "dimension_mismatch")


def test_domains_tolerance_and_invalid_input_never_verify() -> None:
    exact = validate_linear_algebra("determinant", {"domain": "rational"}, "1/3", "2/6")
    approximate = validate_linear_algebra(
        "determinant", {"domain": "real", "tolerance": 0.001}, 1.0005, 1
    )
    wrong_domain = validate_linear_algebra("determinant", {"domain": "integer"}, "1/2", 1)
    too_large = validate_linear_algebra(
        "matrix_addition",
        {"domain": "integer"},
        [[1, 2]],
        [[1, 2]],
        limits=Limits(max_matrix_size=1),
    )
    assert exact.status == approximate.status == "verified"
    assert (wrong_domain.status, wrong_domain.reason) == ("indeterminate", "domain_mismatch")
    assert (too_large.status, too_large.reason) == ("indeterminate", "resource_limit")


def test_linear_system_and_span_boundaries() -> None:
    system = {"matrix": [[1, 1], [1, -1]], "target": [3, 1]}
    assert (
        validate_linear_algebra(
            "linear_system_solution", {"domain": "rational"}, [2, 1], system
        ).status
        == "verified"
    )
    conflict = validate_linear_algebra(
        "linear_system_solution", {"domain": "rational"}, [1, 2], system
    )
    assert (conflict.status, conflict.reason) == ("conflict", "equation_not_satisfied")
    malformed = validate_linear_algebra(
        "linear_system_solution",
        {"domain": "rational"},
        [1],
        {"matrix": [[1, 0], [0, 1]], "target": [1]},
    )
    assert malformed.status == "indeterminate"
    assert malformed.reason == "dimension_mismatch"
    span = validate_linear_algebra(
        "span_basis",
        {"domain": "rational"},
        [[1, 0], [0, 1]],
        [[1, 1], [1, -1]],
    )
    dependent = validate_linear_algebra(
        "span_basis",
        {"domain": "rational"},
        [[1, 0], [2, 0]],
        [[1, 0], [0, 1]],
    )
    assert span.status == "verified"
    assert (dependent.status, dependent.reason) == ("conflict", "not_linearly_independent")


def test_eigen_and_diagonalization_finite_checks() -> None:
    matrix = [[2, 0], [0, 3]]
    assert (
        validate_linear_algebra("eigenvalues", {"domain": "rational"}, [3, 2], matrix).status
        == "verified"
    )
    assert (
        validate_linear_algebra(
            "eigenvectors",
            {"domain": "rational"},
            [1, 0],
            {"matrix": matrix, "eigenvalue": 2},
        ).status
        == "verified"
    )
    assert (
        validate_linear_algebra(
            "eigenspace",
            {"domain": "rational"},
            [[1, 0]],
            {"matrix": matrix, "eigenvalue": 2},
        ).status
        == "verified"
    )
    diagonal = validate_linear_algebra(
        "diagonalization",
        {"domain": "rational"},
        {"P": [[1, 0], [0, 1]], "D": matrix},
        matrix,
    )
    singular = validate_linear_algebra(
        "diagonalization",
        {"domain": "rational"},
        {"P": [[1, 0], [0, 0]], "D": matrix},
        matrix,
    )
    assert diagonal.status == "verified"
    assert (singular.status, singular.reason) == ("conflict", "matrix_not_invertible")


def test_manual_types_and_missing_domain_downgrade() -> None:
    for answer_type in {"proof", "jordan_form", "smith_normal_form", "open_derivation"}:
        outcome = validate_linear_algebra(answer_type, {"domain": "rational"}, {}, {})
        assert (outcome.status, outcome.error_code) == ("manual", "MANUAL_ONLY")
    missing = validate_linear_algebra("rank", {}, [[1]], 1)
    assert (missing.status, missing.error_code) == (
        "indeterminate",
        "INVALID_VALIDATION_RULE",
    )


def test_refs_are_immutable_and_generation_specific() -> None:
    current = validate_linear_algebra(
        "rank", {"domain": "rational"}, [[1, 0], [0, 1]], 2, refs=_refs(4)
    )
    stale = _refs(3)
    assert current.refs is not None and current.refs.generation == 4
    assert current.refs != stale
    assert current.json()["refs"]["rubric_version_id"] == "rubric"
    stale_result = validate_linear_algebra(
        "rank",
        {"domain": "rational"},
        [[1, 0], [0, 1]],
        2,
        refs=_refs(3),
        current_refs=_refs(4),
    )
    assert (stale_result.status, stale_result.error_code) == ("stale", "VALIDATION_STALE")
