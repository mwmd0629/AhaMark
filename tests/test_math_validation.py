from app.math_validation.engine import Limits, validate


def test_scalar_exact_and_tolerance() -> None:
    assert (
        validate({"answer_type": "exact_scalar", "domain": "rational"}, "1/2", "2/4").result
        == "verified_pass"
    )
    assert (
        validate(
            {"answer_type": "approximate_scalar", "domain": "real", "tolerance": 0.01},
            3.14,
            3.14159,
        ).result
        == "verified_pass"
    )


def test_vector_proportional_and_dimension_failure() -> None:
    rule = {"answer_type": "vector", "domain": "rational", "allow_proportional": True}
    assert validate(rule, [2, 4], [1, 2]).result == "verified_pass"
    outcome = validate(rule, [1], [1, 2])
    assert (outcome.result, outcome.reason) == ("verified_fail", "dimension_mismatch")


def test_matrix_rank_determinant_and_resource_limit() -> None:
    assert (
        validate({"answer_type": "rank", "domain": "rational"}, [[1, 2], [2, 4]], 1).result
        == "verified_pass"
    )
    assert (
        validate(
            {"answer_type": "matrix_determinant", "domain": "integer"}, [[1, 2], [3, 4]], -2
        ).result
        == "verified_pass"
    )
    outcome = validate(
        {"answer_type": "matrix", "domain": "integer"},
        [[1, 2]],
        [[1, 2]],
        Limits(max_matrix_size=1),
    )
    assert (outcome.result, outcome.reason) == ("invalid_input", "resource_limit")


def test_explicit_domain_and_manual_boundaries() -> None:
    assert validate({"answer_type": "exact_scalar"}, 1, 1).result == "invalid_input"
    assert (
        validate({"answer_type": "manual_only", "domain": "rational"}, "proof", "proof").result
        == "manual_required"
    )
    assert (
        validate({"answer_type": "jordan_form", "domain": "complex"}, [], []).result
        == "manual_required"
    )


def test_input_is_data_not_code() -> None:
    outcome = validate(
        {"answer_type": "exact_scalar", "domain": "rational"},
        "__import__('os').system('echo unsafe')",
        1,
    )
    assert outcome.result == "invalid_input"
