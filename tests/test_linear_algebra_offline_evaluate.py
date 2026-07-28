import json
from pathlib import Path

from scripts.linear_algebra_offline_evaluate import evaluate, gate_passed

DATASET = Path(__file__).parents[1] / "data" / "linear_algebra_evaluation_v1.json"


def test_dataset_covers_every_registry_type_and_is_synthetic() -> None:
    from app.math_validation.linear_algebra import supported_answer_types

    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    covered = {row["answer_type"] for row in payload["cases"]}
    assert supported_answer_types() <= covered
    assert payload["source"] == "synthetic-local-codex"
    assert all("student" in row and "expected" in row for row in payload["cases"])


def test_runner_metrics_and_gate_are_deterministic() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    first = evaluate(payload)
    second = evaluate(payload)
    assert first == second
    assert first["metrics"]["false_verified"] == 0
    assert first["metrics"]["reference_interception_rate"] == 1.0
    assert first["metrics"]["manual_unsupported_adherence"] == 1.0
    assert gate_passed(first)
    assert first["provider_gate"]["production_ready"] is False


def test_gate_rejects_false_verified_regression() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    report = evaluate(payload)
    report["metrics"]["false_verified"] = 1
    assert not gate_passed(report)


def test_report_contains_no_student_or_secret_fields() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    report = json.dumps(evaluate(payload), ensure_ascii=False)
    for forbidden in ("email", "password", "token", "student_name", "api_key"):
        assert forbidden not in report.lower()
