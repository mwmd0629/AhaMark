from decimal import Decimal

from app.rubrics.validation import validate_rubric


def criterion(key: str, points: str, dependencies: list[str] | None = None) -> dict[str, object]:
    return {
        "stable_key": key,
        "title": key,
        "max_points": points,
        "criterion_type": "computation",
        "validation_mode": "deterministic",
        "dependencies": dependencies or [],
        "validation_rule": {
            "answer_type": "matrix",
            "domain": "rational",
            "limits": {"timeout_ms": 500, "max_matrix_size": 12},
        },
    }


def test_points_total_and_dependency_cycle() -> None:
    assert validate_rubric(Decimal("5"), [criterion("a", "2"), criterion("b", "3", ["a"])]) == []
    errors = validate_rubric(Decimal("5"), [criterion("a", "2", ["b"]), criterion("b", "2", ["a"])])
    assert {error["code"] for error in errors} == {"POINTS_TOTAL_MISMATCH", "DEPENDENCY_CYCLE"}


def test_deterministic_configuration_and_manual_boundary() -> None:
    item = criterion("a", "5")
    item["validation_rule"] = {}
    assert validate_rubric(Decimal("5"), [item])[0]["code"] == "DETERMINISTIC_CONFIG_INCOMPLETE"
    item["validation_mode"] = "manual_only"
    item["validation_rule"] = {"answer_type": "matrix"}
    assert validate_rubric(Decimal("5"), [item])[0]["code"] == "MANUAL_ONLY_AUTOMATION_FORBIDDEN"


def test_ai_suggestion_allows_a_descriptive_criterion_without_deterministic_rule() -> None:
    item = criterion("reasoning", "5")
    item["validation_mode"] = "ai_suggestion"
    item["validation_rule"] = {}
    assert validate_rubric(Decimal("5"), [item]) == []
