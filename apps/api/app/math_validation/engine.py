from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any

ENGINE_VERSION = "ahamark-safe-math-2"


@dataclass(frozen=True)
class Limits:
    timeout_ms: int = 500
    max_expression_length: int = 4096
    max_nodes: int = 1000
    max_matrix_size: int = 12
    max_polynomial_degree: int = 20
    max_variables: int = 8
    max_expansion_terms: int = 500
    random_seed: int = 1729


@dataclass(frozen=True)
class ValidationOutcome:
    result: str
    comparison_method: str
    reason: str | None
    evidence: dict[str, Any]
    diagnostics: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return asdict(self)


class InvalidMathInput(ValueError):
    pass


def _fraction(value: object, domain: str) -> Fraction:
    if domain not in {"integer", "rational", "real", "complex"}:
        raise InvalidMathInput("domain_mismatch")
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Fraction)):
        raise InvalidMathInput("invalid_scalar")
    text = str(value).strip()
    if len(text) > 128:
        raise InvalidMathInput("resource_limit")
    try:
        result = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise InvalidMathInput("invalid_scalar") from exc
    if domain == "integer" and result.denominator != 1:
        raise InvalidMathInput("domain_mismatch")
    return result


def _matrix(value: object, domain: str, limits: Limits) -> list[list[Fraction]]:
    if not isinstance(value, list) or not value or not all(isinstance(row, list) for row in value):
        raise InvalidMathInput("invalid_matrix")
    rows = value
    if len(rows) > limits.max_matrix_size or any(len(row) > limits.max_matrix_size for row in rows):
        raise InvalidMathInput("resource_limit")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise InvalidMathInput("dimension_mismatch")
    return [[_fraction(cell, domain) for cell in row] for row in rows]


def _rank(matrix: list[list[Fraction]]) -> int:
    a = [row[:] for row in matrix]
    rank = 0
    for col in range(len(a[0])):
        pivot = next((i for i in range(rank, len(a)) if a[i][col]), None)
        if pivot is None:
            continue
        a[rank], a[pivot] = a[pivot], a[rank]
        p = a[rank][col]
        a[rank] = [x / p for x in a[rank]]
        for i, row in enumerate(a):
            if i != rank and row[col]:
                factor = row[col]
                a[i] = [x - factor * y for x, y in zip(row, a[rank], strict=True)]
        rank += 1
        if rank == len(a):
            break
    return rank


def _determinant(matrix: list[list[Fraction]]) -> Fraction:
    if len(matrix) != len(matrix[0]):
        raise InvalidMathInput("dimension_mismatch")
    a = [row[:] for row in matrix]
    result = Fraction(1)
    for col in range(len(a)):
        pivot = next((i for i in range(col, len(a)) if a[i][col]), None)
        if pivot is None:
            return Fraction(0)
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            result *= -1
        p = a[col][col]
        result *= p
        for i in range(col + 1, len(a)):
            factor = a[i][col] / p
            for j in range(col + 1, len(a)):
                a[i][j] -= factor * a[col][j]
    return result


def _rref(matrix: list[list[Fraction]]) -> tuple[list[list[Fraction]], list[int]]:
    a = [row[:] for row in matrix]
    pivots: list[int] = []
    row = 0
    for col in range(len(a[0])):
        pivot = next((i for i in range(row, len(a)) if a[i][col]), None)
        if pivot is None:
            continue
        a[row], a[pivot] = a[pivot], a[row]
        value = a[row][col]
        a[row] = [cell / value for cell in a[row]]
        for index, current in enumerate(a):
            if index != row and current[col]:
                factor = current[col]
                a[index] = [
                    cell - factor * pivot_cell
                    for cell, pivot_cell in zip(current, a[row], strict=True)
                ]
        pivots.append(col)
        row += 1
        if row == len(a):
            break
    return a, pivots


def _transpose(matrix: list[list[Fraction]]) -> list[list[Fraction]]:
    return [list(column) for column in zip(*matrix, strict=True)]


def _multiply(left: list[list[Fraction]], right: list[list[Fraction]]) -> list[list[Fraction]]:
    if len(left[0]) != len(right):
        raise InvalidMathInput("dimension_mismatch")
    columns = _transpose(right)
    return [
        [
            sum(
                (a * b for a, b in zip(row, column, strict=True)),
                start=Fraction(0),
            )
            for column in columns
        ]
        for row in left
    ]


def _vector(value: object, domain: str, limits: Limits) -> list[Fraction]:
    return _matrix([value], domain, limits)[0]


def _basis(value: object, domain: str, limits: Limits) -> list[list[Fraction]]:
    if not isinstance(value, list) or not value:
        raise InvalidMathInput("invalid_basis")
    vectors = [_vector(item, domain, limits) for item in value]
    if any(len(item) != len(vectors[0]) for item in vectors):
        raise InvalidMathInput("dimension_mismatch")
    return vectors


def _same_span(left: list[list[Fraction]], right: list[list[Fraction]]) -> bool:
    if len(left[0]) != len(right[0]):
        return False
    return _rank(left) == _rank(right) == _rank(left + right)


def _nullspace(matrix: list[list[Fraction]]) -> list[list[Fraction]]:
    reduced, pivots = _rref(matrix)
    free = [col for col in range(len(matrix[0])) if col not in pivots]
    basis: list[list[Fraction]] = []
    for free_col in free:
        vector = [Fraction(0) for _ in range(len(matrix[0]))]
        vector[free_col] = Fraction(1)
        for row, pivot_col in enumerate(pivots):
            vector[pivot_col] = -reduced[row][free_col]
        basis.append(vector)
    return basis


def _solution_kind(matrix: list[list[Fraction]], target: list[Fraction]) -> str:
    if len(matrix) != len(target):
        raise InvalidMathInput("dimension_mismatch")
    rank = _rank(matrix)
    augmented_rank = _rank([row + [value] for row, value in zip(matrix, target, strict=True)])
    if augmented_rank > rank:
        return "no_solution"
    return "unique" if rank == len(matrix[0]) else "infinite"


def _satisfies(
    matrix: list[list[Fraction]], vector: list[Fraction], target: list[Fraction]
) -> bool:
    if len(matrix[0]) != len(vector) or len(matrix) != len(target):
        raise InvalidMathInput("dimension_mismatch")
    return [
        sum(cell * value for cell, value in zip(row, vector, strict=True)) for row in matrix
    ] == target


def _polynomial_coefficients(value: object, domain: str, limits: Limits) -> list[Fraction]:
    if isinstance(value, dict) and "factors" in value:
        factors = value.get("factors")
        scalar = _fraction(value.get("scalar", 1), domain)
        if not isinstance(factors, list):
            raise InvalidMathInput("invalid_polynomial")
        result = [scalar]
        for factor in factors:
            factor_coefficients = _polynomial_coefficients(factor, domain, limits)
            expanded = [Fraction(0)] * (len(result) + len(factor_coefficients) - 1)
            for i, left in enumerate(result):
                for j, right in enumerate(factor_coefficients):
                    expanded[i + j] += left * right
            if len(expanded) - 1 > limits.max_polynomial_degree:
                raise InvalidMathInput("resource_limit")
            result = expanded
        return _trim_polynomial(result)
    raw_coefficients = value.get("coefficients") if isinstance(value, dict) else value
    if not isinstance(raw_coefficients, list) or not raw_coefficients:
        raise InvalidMathInput("invalid_polynomial")
    if len(raw_coefficients) - 1 > limits.max_polynomial_degree:
        raise InvalidMathInput("resource_limit")
    return _trim_polynomial([_fraction(item, domain) for item in raw_coefficients])


def _trim_polynomial(coefficients: list[Fraction]) -> list[Fraction]:
    result = coefficients[:]
    while len(result) > 1 and result[-1] == 0:
        result.pop()
    return result


def _matrix_polynomial(
    coefficients: list[Fraction], matrix: list[list[Fraction]]
) -> list[list[Fraction]]:
    if len(matrix) != len(matrix[0]):
        raise InvalidMathInput("dimension_mismatch")
    size = len(matrix)
    result = [[Fraction(0) for _ in range(size)] for _ in range(size)]
    identity = [[Fraction(int(row == col)) for col in range(size)] for row in range(size)]
    power = identity
    for coefficient in coefficients:
        for row in range(size):
            for col in range(size):
                result[row][col] += coefficient * power[row][col]
        power = _multiply(power, matrix)
    return result


def _characteristic_polynomial(matrix: list[list[Fraction]]) -> list[Fraction]:
    if len(matrix) != len(matrix[0]):
        raise InvalidMathInput("dimension_mismatch")
    size = len(matrix)
    identity = [[Fraction(int(row == col)) for col in range(size)] for row in range(size)]
    coefficients_desc = [Fraction(1)]
    current = identity
    for k in range(1, size + 1):
        current = _multiply(matrix, current)
        coefficient = -sum((current[i][i] for i in range(size)), start=Fraction(0)) / k
        coefficients_desc.append(coefficient)
        for i in range(size):
            current[i][i] += coefficient
    return list(reversed(coefficients_desc))


def _minimal_polynomial(matrix: list[list[Fraction]], limits: Limits) -> list[Fraction]:
    size = len(matrix)
    identity = [[Fraction(int(row == col)) for col in range(size)] for row in range(size)]
    powers = [identity]
    for degree in range(1, min(limits.max_polynomial_degree, size * size) + 1):
        powers.append(_multiply(powers[-1], matrix))
        columns = [
            [power[row][col] for row in range(size) for col in range(size)] for power in powers
        ]
        coefficient_matrix = _transpose(columns[:-1])
        target = [-value for value in columns[-1]]
        if _solution_kind(coefficient_matrix, target) == "no_solution":
            continue
        augmented = [row + [value] for row, value in zip(coefficient_matrix, target, strict=True)]
        reduced, pivots = _rref(augmented)
        coefficients = [Fraction(0) for _ in range(degree)]
        for row, pivot in enumerate(pivots):
            if pivot < degree:
                coefficients[pivot] = reduced[row][-1]
        return coefficients + [Fraction(1)]
    raise InvalidMathInput("resource_limit")


def _outcome(
    passed: bool,
    method: str,
    reason: str,
    domain: str,
    started: float,
    limits: Limits,
    evidence: dict[str, Any] | None = None,
) -> ValidationOutcome:
    elapsed = int((time.monotonic() - started) * 1000)
    if elapsed > limits.timeout_ms:
        return ValidationOutcome(
            "timeout", "bounded_runtime", "timeout", {}, {"duration_ms": elapsed}
        )
    return ValidationOutcome(
        "verified_pass" if passed else "verified_fail",
        method,
        None if passed else reason,
        {"domain": domain, **(evidence or {})},
        {"duration_ms": elapsed, "engine_version": ENGINE_VERSION},
    )


def validate(
    rule: dict[str, Any], student: object, expected: object, limits: Limits | None = None
) -> ValidationOutcome:
    limits = limits or Limits()
    started = time.monotonic()
    kind = rule.get("answer_type")
    domain = rule.get("domain")
    if not isinstance(kind, str) or not isinstance(domain, str):
        return ValidationOutcome(
            "invalid_input", "schema", "missing_explicit_domain_or_type", {}, {}
        )
    try:
        if kind in {"exact_scalar", "approximate_scalar", "determinant"}:
            actual = _fraction(student, domain)
            target = _fraction(expected, domain)
            tolerance = float(rule.get("tolerance", 0))
            passed = math.isclose(
                float(actual), float(target), abs_tol=tolerance, rel_tol=tolerance
            )
            method = "exact_fraction" if tolerance == 0 else "bounded_tolerance"
        elif kind in {"vector", "matrix"}:
            actual_matrix = _matrix([student] if kind == "vector" else student, domain, limits)
            target_matrix = _matrix([expected] if kind == "vector" else expected, domain, limits)
            if (len(actual_matrix), len(actual_matrix[0])) != (
                len(target_matrix),
                len(target_matrix[0]),
            ):
                return ValidationOutcome(
                    "verified_fail", "component_comparison", "dimension_mismatch", {}, {}
                )
            if rule.get("allow_proportional") and kind == "vector":
                pairs = list(zip(actual_matrix[0], target_matrix[0], strict=True))
                ratios = {a / b for a, b in pairs if b}
                passed = all((a == 0) == (b == 0) for a, b in pairs) and len(ratios) == 1
                method = "proportional_vector"
            else:
                passed = actual_matrix == target_matrix
                method = "component_comparison"
        elif kind in {"rank", "invertible"}:
            matrix = _matrix(student, domain, limits)
            actual_value = _rank(matrix)
            if kind == "rank":
                if isinstance(expected, bool) or not isinstance(expected, (int, str)):
                    raise InvalidMathInput("invalid_scalar")
                target_value: int | bool = int(expected)
            else:
                if not isinstance(expected, bool):
                    raise InvalidMathInput("invalid_scalar")
                target_value = expected
            passed = (
                actual_value == target_value
                if kind == "rank"
                else (len(matrix) == len(matrix[0]) and actual_value == len(matrix)) == target_value
            )
            method = "exact_gaussian_elimination"
        elif kind == "matrix_determinant":
            matrix = _matrix(student, domain, limits)
            passed = _determinant(matrix) == _fraction(expected, domain)
            method = "exact_fraction_elimination"
        elif kind == "linear_system_candidate":
            if not isinstance(expected, dict):
                raise InvalidMathInput("invalid_linear_system")
            matrix = _matrix(expected.get("matrix"), domain, limits)
            system_target = _vector(expected.get("target"), domain, limits)
            candidate_vector = _vector(student, domain, limits)
            return _outcome(
                _satisfies(matrix, candidate_vector, system_target),
                "exact_substitution",
                "equation_not_satisfied",
                domain,
                started,
                limits,
            )
        elif kind == "linear_system_basis":
            if not isinstance(expected, dict):
                raise InvalidMathInput("invalid_linear_system")
            matrix = _matrix(expected.get("matrix"), domain, limits)
            candidate_basis = _basis(student, domain, limits)
            target_basis = _nullspace(matrix)
            passed = (
                all(
                    _satisfies(matrix, vector, [Fraction(0)] * len(matrix))
                    for vector in candidate_basis
                )
                and _rank(candidate_basis) == len(candidate_basis)
                and _same_span(candidate_basis, target_basis)
            )
            return _outcome(
                passed,
                "nullspace_basis",
                "invalid_fundamental_solution_set",
                domain,
                started,
                limits,
                {"nullity": len(target_basis)},
            )
        elif kind in {"affine_solution", "parametric_solution_set"}:
            if not isinstance(student, dict) or not isinstance(expected, dict):
                raise InvalidMathInput("invalid_linear_system")
            if kind == "affine_solution" and "matrix" in expected:
                matrix = _matrix(expected.get("matrix"), domain, limits)
                affine_target = _vector(expected.get("target"), domain, limits)
                target_particular = _vector(student.get("particular"), domain, limits)
                student_basis = _basis(student.get("basis"), domain, limits)
                target_basis = _nullspace(matrix)
                passed = (
                    _satisfies(matrix, target_particular, affine_target)
                    and _same_span(student_basis, target_basis)
                    and _rank(student_basis) == len(student_basis)
                )
            else:
                student_particular = _vector(student.get("particular"), domain, limits)
                expected_particular = _vector(expected.get("particular"), domain, limits)
                student_basis = _basis(student.get("basis"), domain, limits)
                expected_basis = _basis(expected.get("basis"), domain, limits)
                difference = [
                    left - right
                    for left, right in zip(student_particular, expected_particular, strict=True)
                ]
                passed = _same_span(student_basis, expected_basis) and _same_span(
                    student_basis, student_basis + [difference]
                )
            return _outcome(
                passed,
                "affine_subspace_equivalence",
                "solution_set_mismatch",
                domain,
                started,
                limits,
            )
        elif kind == "linear_system_classification":
            if not isinstance(expected, dict) or not isinstance(student, str):
                raise InvalidMathInput("invalid_linear_system")
            matrix = _matrix(expected.get("matrix"), domain, limits)
            classification_target = _vector(expected.get("target"), domain, limits)
            actual_kind = _solution_kind(matrix, classification_target)
            return _outcome(
                student == actual_kind,
                "rank_classification",
                "solution_classification_mismatch",
                domain,
                started,
                limits,
                {"actual_kind": actual_kind},
            )
        elif kind == "subspace_membership":
            target_basis = _basis(expected, domain, limits)
            vector = _vector(student, domain, limits)
            passed = _rank(target_basis) == _rank(target_basis + [vector])
            return _outcome(
                passed,
                "span_membership",
                "not_in_target_subspace",
                domain,
                started,
                limits,
            )
        elif kind in {"subspace_basis", "row_space", "column_space"}:
            student_basis = _basis(student, domain, limits)
            expected_basis = _basis(expected, domain, limits)
            independent = _rank(student_basis) == len(student_basis)
            passed = independent and _same_span(student_basis, expected_basis)
            return _outcome(
                passed,
                "exact_span_equivalence",
                "not_linearly_independent" if not independent else "does_not_span_target",
                domain,
                started,
                limits,
                {"dimension": _rank(expected_basis)},
            )
        elif kind == "linear_independence":
            vectors = _basis(student, domain, limits)
            expected_value = expected
            if not isinstance(expected_value, bool):
                raise InvalidMathInput("invalid_scalar")
            passed = (_rank(vectors) == len(vectors)) == expected_value
            return _outcome(
                passed,
                "exact_rank",
                "linear_independence_mismatch",
                domain,
                started,
                limits,
            )
        elif kind == "subspace_dimension":
            basis = _basis(expected, domain, limits)
            if isinstance(student, bool) or not isinstance(student, (int, str)):
                raise InvalidMathInput("invalid_scalar")
            return _outcome(
                int(student) == _rank(basis),
                "exact_rank",
                "dimension_mismatch",
                domain,
                started,
                limits,
            )
        elif kind == "polynomial":
            if rule.get("variable") is None:
                raise InvalidMathInput("missing_explicit_variable")
            actual_polynomial = _polynomial_coefficients(student, domain, limits)
            expected_polynomial = _polynomial_coefficients(expected, domain, limits)
            return _outcome(
                actual_polynomial == expected_polynomial,
                "bounded_polynomial_expansion",
                "polynomial_mismatch",
                domain,
                started,
                limits,
            )
        elif kind == "characteristic_polynomial":
            if not isinstance(expected, dict) or rule.get("variable") is None:
                raise InvalidMathInput("invalid_polynomial")
            matrix = _matrix(expected.get("matrix"), domain, limits)
            actual_polynomial = _polynomial_coefficients(student, domain, limits)
            target_polynomial = _characteristic_polynomial(matrix)
            return _outcome(
                actual_polynomial == target_polynomial,
                "faddeev_leverrier",
                "polynomial_mismatch",
                domain,
                started,
                limits,
                {"degree": len(target_polynomial) - 1},
            )
        elif kind == "minimal_polynomial":
            if not isinstance(expected, dict) or rule.get("variable") is None:
                raise InvalidMathInput("invalid_polynomial")
            matrix = _matrix(expected.get("matrix"), domain, limits)
            candidate = _polynomial_coefficients(student, domain, limits)
            if any(any(cell for cell in row) for row in _matrix_polynomial(candidate, matrix)):
                return _outcome(
                    False,
                    "matrix_polynomial_substitution",
                    "polynomial_does_not_annihilate_matrix",
                    domain,
                    started,
                    limits,
                )
            if not rule.get("prove_minimal", False):
                return ValidationOutcome(
                    "indeterminate",
                    "annihilation_only",
                    "minimality_not_proven",
                    {"domain": domain, "annihilates": True},
                    {"engine_version": ENGINE_VERSION},
                )
            minimal = _minimal_polynomial(matrix, limits)
            return _outcome(
                candidate == minimal,
                "bounded_krylov_minimal_polynomial",
                "not_minimal_polynomial",
                domain,
                started,
                limits,
                {"degree": len(minimal) - 1},
            )
        elif kind == "eigenvalue_multiset":
            matrix = _matrix(expected, domain, limits)
            if not isinstance(student, list) or len(student) != len(matrix):
                return ValidationOutcome(
                    "verified_fail",
                    "characteristic_polynomial_roots",
                    "algebraic_multiplicity_mismatch",
                    {"domain": domain},
                    {},
                )
            roots_polynomial: list[Fraction] = [Fraction(1)]
            for root_value in student:
                root = _fraction(root_value, domain)
                roots_polynomial = _polynomial_coefficients(
                    {"factors": [roots_polynomial, [-root, 1]]}, domain, limits
                )
            return _outcome(
                roots_polynomial == _characteristic_polynomial(matrix),
                "characteristic_polynomial_roots",
                "eigenvalue_multiset_mismatch",
                domain,
                started,
                limits,
            )
        elif kind == "eigenvector":
            if not isinstance(expected, dict):
                raise InvalidMathInput("invalid_eigenpair")
            matrix = _matrix(expected.get("matrix"), domain, limits)
            eigenvalue = _fraction(expected.get("eigenvalue"), domain)
            vector = _vector(student, domain, limits)
            if all(value == 0 for value in vector):
                return _outcome(
                    False,
                    "exact_eigenpair",
                    "zero_eigenvector",
                    domain,
                    started,
                    limits,
                )
            left = _multiply(matrix, [[value] for value in vector])
            right = [[eigenvalue * value] for value in vector]
            return _outcome(
                left == right,
                "exact_eigenpair",
                "equation_not_satisfied",
                domain,
                started,
                limits,
            )
        elif kind == "eigenspace_basis":
            if not isinstance(expected, dict):
                raise InvalidMathInput("invalid_eigenpair")
            matrix = _matrix(expected.get("matrix"), domain, limits)
            eigenvalue = _fraction(expected.get("eigenvalue"), domain)
            shifted = [
                [value - (eigenvalue if row == col else 0) for col, value in enumerate(values)]
                for row, values in enumerate(matrix)
            ]
            student_basis = _basis(student, domain, limits)
            target_basis = _nullspace(shifted)
            independent = _rank(student_basis) == len(student_basis)
            return _outcome(
                independent and _same_span(student_basis, target_basis),
                "eigenspace_nullspace",
                "not_linearly_independent" if not independent else "does_not_span_target",
                domain,
                started,
                limits,
            )
        elif kind == "diagonalization":
            if not isinstance(student, dict):
                raise InvalidMathInput("invalid_decomposition")
            matrix = _matrix(expected, domain, limits)
            eigenvectors = _matrix(student.get("P"), domain, limits)
            diagonal = _matrix(student.get("D"), domain, limits)
            size = len(matrix)
            shapes_ok = (
                len(matrix[0]) == size
                and len(eigenvectors) == len(eigenvectors[0]) == size
                and len(diagonal) == len(diagonal[0]) == size
            )
            diagonal_ok = shapes_ok and all(
                diagonal[row][col] == 0 for row in range(size) for col in range(size) if row != col
            )
            invertible = shapes_ok and _rank(eigenvectors) == size
            relation = shapes_ok and _multiply(matrix, eigenvectors) == _multiply(
                eigenvectors, diagonal
            )
            reason = (
                "dimension_mismatch"
                if not shapes_ok
                else (
                    "decomposition_invalid"
                    if not diagonal_ok or not relation
                    else "matrix_not_invertible"
                )
            )
            return _outcome(
                shapes_ok and diagonal_ok and invertible and relation,
                "exact_AP_equals_PD",
                reason,
                domain,
                started,
                limits,
                {"p_invertible": invertible, "ap_equals_pd": relation},
            )
        elif kind in {"manual_only", "proof_step", "jordan_form", "smith_normal_form"}:
            return ValidationOutcome("manual_required", "manual_policy", None, {}, {})
        else:
            return ValidationOutcome("indeterminate", "unsupported_safe_operation", None, {}, {})
        elapsed = int((time.monotonic() - started) * 1000)
        if elapsed > limits.timeout_ms:
            return ValidationOutcome(
                "timeout", "bounded_runtime", "timeout", {}, {"duration_ms": elapsed}
            )
        return ValidationOutcome(
            "verified_pass" if passed else "verified_fail",
            method,
            None if passed else "value_mismatch",
            {"domain": domain, "exact": rule.get("tolerance", 0) == 0},
            {"duration_ms": elapsed, "engine_version": ENGINE_VERSION},
        )
    except InvalidMathInput as exc:
        return ValidationOutcome("invalid_input", "bounded_parser", str(exc), {}, {})
    except (ArithmeticError, TypeError, ValueError) as exc:
        return ValidationOutcome("invalid_input", "bounded_parser", type(exc).__name__, {}, {})
