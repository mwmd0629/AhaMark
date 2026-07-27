from app.math_validation.engine import Limits, validate

LIMITS = {
    "timeout_ms": 500,
    "max_matrix_size": 12,
    "max_polynomial_degree": 20,
}


def rule(answer_type: str, **extra: object) -> dict[str, object]:
    return {
        "answer_type": answer_type,
        "domain": "rational",
        "limits": LIMITS,
        **extra,
    }


def test_linear_system_candidate_basis_and_classification() -> None:
    system = {"matrix": [[1, 1, 1], [2, 2, 2]], "target": [0, 0]}
    assert validate(rule("linear_system_candidate"), [1, -1, 0], system).result == "verified_pass"
    assert validate(rule("linear_system_candidate"), [1, 0, 0], system).reason == (
        "equation_not_satisfied"
    )
    basis = [[-1, 1, 0], [-1, 0, 1]]
    assert (
        validate(rule("linear_system_basis"), basis, {"matrix": system["matrix"]}).result
        == "verified_pass"
    )
    invalid = validate(
        rule("linear_system_basis"), [basis[0], [2, -2, 0]], {"matrix": system["matrix"]}
    )
    assert (invalid.result, invalid.reason) == (
        "verified_fail",
        "invalid_fundamental_solution_set",
    )
    assert (
        validate(rule("linear_system_classification"), "infinite", system).result == "verified_pass"
    )
    assert (
        validate(
            rule("linear_system_classification"),
            "no_solution",
            {"matrix": [[1], [1]], "target": [0, 1]},
        ).result
        == "verified_pass"
    )
    assert (
        validate(
            rule("linear_system_classification"),
            "unique",
            {"matrix": [[1, 0], [0, 1]], "target": [3, 4]},
        ).result
        == "verified_pass"
    )


def test_parametric_solution_sets_are_affinely_equivalent() -> None:
    first = {"particular": [1, 0, 0], "basis": [[0, 1, 0], [0, 0, 1]]}
    equivalent = {"particular": [1, 2, 3], "basis": [[0, 0, 2], [0, -3, 0]]}
    different = {"particular": [2, 0, 0], "basis": [[0, 1, 0], [0, 0, 1]]}
    assert validate(rule("parametric_solution_set"), equivalent, first).result == "verified_pass"
    outcome = validate(rule("parametric_solution_set"), different, first)
    assert (outcome.result, outcome.reason) == ("verified_fail", "solution_set_mismatch")


def test_subspace_membership_basis_equivalence_and_invalid_basis() -> None:
    target = [[1, 0, 1], [0, 1, 1]]
    assert validate(rule("subspace_membership"), [2, 3, 5], target).result == "verified_pass"
    assert (
        validate(rule("subspace_basis"), [[0, 2, 2], [-3, 0, -3]], target).result == "verified_pass"
    )
    invalid = validate(rule("subspace_basis"), [[1, 0, 1], [2, 0, 2]], target)
    assert (invalid.result, invalid.reason) == ("verified_fail", "not_linearly_independent")
    assert validate(rule("subspace_dimension"), 2, target).result == "verified_pass"


def test_polynomial_factor_form_characteristic_and_resource_limit() -> None:
    expanded = {"coefficients": [-1, 0, 1]}
    factored = {"factors": [[-1, 1], [1, 1]]}
    polynomial_rule = rule("polynomial", variable="x")
    assert validate(polynomial_rule, factored, expanded).result == "verified_pass"
    matrix = {"matrix": [[1, 0], [0, 2]]}
    assert (
        validate(
            rule("characteristic_polynomial", variable="t"),
            {"factors": [[-1, 1], [-2, 1]]},
            matrix,
        ).result
        == "verified_pass"
    )
    limited = validate(
        rule("polynomial", variable="x"),
        {"coefficients": [1, 2, 3]},
        {"coefficients": [1, 2, 3]},
        Limits(max_polynomial_degree=1),
    )
    assert (limited.result, limited.reason) == ("invalid_input", "resource_limit")


def test_minimal_polynomial_verified_and_indeterminate() -> None:
    matrix = {"matrix": [[2, 0], [0, 2]]}
    candidate = {"coefficients": [-2, 1]}
    assert (
        validate(
            rule("minimal_polynomial", variable="x", prove_minimal=True),
            candidate,
            matrix,
        ).result
        == "verified_pass"
    )
    outcome = validate(
        rule("minimal_polynomial", variable="x", prove_minimal=False),
        {"coefficients": [4, -4, 1]},
        matrix,
    )
    assert (outcome.result, outcome.reason) == ("indeterminate", "minimality_not_proven")
    failed = validate(
        rule("minimal_polynomial", variable="x", prove_minimal=True),
        {"coefficients": [-3, 1]},
        matrix,
    )
    assert failed.reason == "polynomial_does_not_annihilate_matrix"


def test_eigenvalue_multiset_eigenvector_and_eigenspace() -> None:
    matrix = [[2, 0, 0], [0, 2, 0], [0, 0, 3]]
    assert validate(rule("eigenvalue_multiset"), [3, 2, 2], matrix).result == "verified_pass"
    multiplicity = validate(rule("eigenvalue_multiset"), [2, 3], matrix)
    assert multiplicity.reason == "algebraic_multiplicity_mismatch"
    eigenpair = {"matrix": matrix, "eigenvalue": 2}
    assert validate(rule("eigenvector"), [1, 4, 0], eigenpair).result == "verified_pass"
    assert validate(rule("eigenvector"), [0, 0, 0], eigenpair).reason == "zero_eigenvector"
    assert (
        validate(rule("eigenspace_basis"), [[0, 2, 0], [3, 0, 0]], eigenpair).result
        == "verified_pass"
    )


def test_diagonalization_correct_and_wrong() -> None:
    matrix = [[1, 1], [0, 2]]
    correct = {"P": [[1, 1], [0, 1]], "D": [[1, 0], [0, 2]]}
    wrong = {"P": [[1, 0], [0, 1]], "D": [[1, 0], [0, 2]]}
    assert validate(rule("diagonalization"), correct, matrix).result == "verified_pass"
    outcome = validate(rule("diagonalization"), wrong, matrix)
    assert (outcome.result, outcome.reason) == ("verified_fail", "decomposition_invalid")


def test_domain_dimension_timeout_and_manual_boundaries() -> None:
    assert (
        validate(
            {"answer_type": "subspace_basis", "domain": "unsupported"},
            [[1, 0]],
            [[1, 0]],
        ).reason
        == "domain_mismatch"
    )
    assert (
        validate(rule("eigenvector"), [1], {"matrix": [[1, 0], [0, 1]], "eigenvalue": 1}).reason
        == "dimension_mismatch"
    )
    timeout = validate(
        rule("diagonalization"),
        {"P": [[1, 0], [0, 1]], "D": [[1, 0], [0, 1]]},
        [[1, 0], [0, 1]],
        Limits(timeout_ms=-1),
    )
    assert timeout.result == "timeout"
    for answer_type in ("jordan_form", "smith_normal_form", "proof_step"):
        assert validate(rule(answer_type), {}, {}).result == "manual_required"
