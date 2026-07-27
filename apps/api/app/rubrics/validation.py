from __future__ import annotations

from decimal import Decimal
from typing import Any

SUPPORTED_TYPES = {
    "final_answer",
    "intermediate_result",
    "method",
    "justification",
    "proof_step",
    "computation",
    "presentation",
}
SUPPORTED_MODES = {"deterministic", "manual_only"}
SUPPORTED_ANSWERS = {
    "exact_scalar",
    "approximate_scalar",
    "vector",
    "matrix",
    "polynomial",
    "unordered_set",
    "ordered_sequence",
    "linear_system",
    "subspace_basis",
    "row_space",
    "column_space",
    "eigenvalue_multiset",
    "characteristic_polynomial",
    "minimal_polynomial",
    "determinant",
    "symbolic_expression",
    "manual_only",
}


def validate_rubric(total_points: Decimal, criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    keys = [str(item.get("stable_key", "")) for item in criteria]
    if len(keys) != len(set(keys)) or any(not key for key in keys):
        errors.append({"code": "STABLE_KEY_INVALID"})
    points = Decimal("0")
    graph: dict[str, list[str]] = {}
    for item in criteria:
        key = str(item.get("stable_key", ""))
        try:
            value = Decimal(str(item.get("max_points")))
        except Exception:
            value = Decimal("-1")
        if value < 0 or value > total_points:
            errors.append({"code": "POINTS_INVALID", "criterion": key})
        points += max(value, Decimal("0"))
        mode = item.get("validation_mode")
        rule = item.get("validation_rule") or {}
        if item.get("criterion_type") not in SUPPORTED_TYPES:
            errors.append({"code": "CRITERION_TYPE_INVALID", "criterion": key})
        if mode not in SUPPORTED_MODES:
            errors.append({"code": "VALIDATION_MODE_UNSUPPORTED", "criterion": key})
        if mode == "deterministic" and (
            rule.get("answer_type") not in SUPPORTED_ANSWERS
            or not rule.get("domain")
            or "limits" not in rule
        ):
            errors.append({"code": "DETERMINISTIC_CONFIG_INCOMPLETE", "criterion": key})
        if mode == "manual_only" and rule.get("answer_type") not in {None, "manual_only"}:
            errors.append({"code": "MANUAL_ONLY_AUTOMATION_FORBIDDEN", "criterion": key})
        deps = [str(dep) for dep in item.get("dependencies", [])]
        if any(dep not in keys for dep in deps):
            errors.append({"code": "DEPENDENCY_OUTSIDE_RUBRIC", "criterion": key})
        graph[key] = deps
    if points != total_points:
        errors.append(
            {"code": "POINTS_TOTAL_MISMATCH", "expected": str(total_points), "actual": str(points)}
        )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        cycle = any(visit(dep) for dep in graph.get(node, []))
        visiting.remove(node)
        visited.add(node)
        return cycle

    if any(visit(key) for key in graph):
        errors.append({"code": "DEPENDENCY_CYCLE"})
    return errors
