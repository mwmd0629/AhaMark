from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.assignment_generation_evaluate import (
    DATASET_VERSION,
    DATASET_VERSION_V2,
    boundary_iou,
    character_error_rate,
    evaluate,
    load_dataset,
    threshold_checks,
    write_run,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "tests/fixtures/assignment_generation_evaluation_v1.jsonl"
THRESHOLDS = ROOT / "scripts/assignment-generation-evaluation-thresholds-v1.json"
DATASET_V2 = ROOT / "tests/fixtures/assignment_generation_evaluation_v2.jsonl"
THRESHOLDS_V2 = ROOT / "scripts/assignment-generation-evaluation-thresholds-v2.json"


def test_frozen_synthetic_dataset_schema_and_unique_ids() -> None:
    rows = load_dataset(DATASET)
    assert len(rows) == 16
    assert {row["dataset_version"] for row in rows} == {DATASET_VERSION}
    assert len({row["case_id"] for row in rows}) == len(rows)
    assert all(row["synthetic"] and "publish" in row["forbidden_actions"] for row in rows)


def test_frozen_v2_dataset_has_thirty_distinct_synthetic_cases() -> None:
    rows = load_dataset(DATASET_V2, DATASET_VERSION_V2)
    thresholds = json.loads(THRESHOLDS_V2.read_text(encoding="utf-8"))
    assert len(rows) == 32
    assert len({row["case_id"] for row in rows}) == len(rows)
    assert {row["dataset_version"] for row in rows} == {DATASET_VERSION_V2}
    assert all(row["synthetic"] and "publish" in row["forbidden_actions"] for row in rows)
    assert thresholds["minimum_real_provider_cases"] == 30
    tags = {tag for row in rows for tag in row.get("coverage_tags", [])}
    assert {"prompt injection", "dependency cycle", "double request publish"} <= tags


def test_iou_and_cer_are_normalized_and_deterministic() -> None:
    assert boundary_iou([0, 0, 1, 1], [0.5, 0, 1, 1]) == pytest.approx(0.5)
    assert character_error_rate("A B C", "abc") == 0
    assert character_error_rate("abc", "axc") == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        boundary_iou([-1, 0, 1, 1], [0, 0, 1, 1])


def test_denominators_nulls_and_rubric_metrics() -> None:
    rows = load_dataset(DATASET)
    metrics = evaluate(rows, "fake")
    assert metrics["question_count_accuracy"] == 1
    assert metrics["rubric_points_consistency"] == 1
    assert metrics["dependency_validity"] == 1
    assert metrics["teacher_modification_rate"] is None
    assert metrics["provider_input_tokens"] is None
    assert metrics["estimated_cost"] is None


def test_high_confidence_error_and_unknown_evidence_are_counted() -> None:
    row = deepcopy(load_dataset(DATASET)[0])
    row["prediction"]["questions"][0]["text"] = "wrong"
    row["prediction"]["confidence"] = 0.99
    row["prediction"]["rubric"]["evidence_refs"] = ["unknown"]
    metrics = evaluate([row], "fake")
    assert metrics["high_confidence_error_rate"] == 1
    assert metrics["evidence_unknown_reference_count"] == 1


def test_fake_and_unavailable_never_pass_real_provider_gate() -> None:
    rows = load_dataset(DATASET)
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    for provider in ("fake", "unavailable"):
        result = threshold_checks(evaluate(rows, provider), thresholds, provider)
        assert result["real_provider_run"] is False
        assert result["real_provider_thresholds_passed"] is False


def test_failed_or_existing_run_is_never_merged_or_overwritten(tmp_path: Path) -> None:
    rows = load_dataset(DATASET)
    run_id = "assignment-generation-v1-test-isolation"
    run_dir = write_run(rows, DATASET, THRESHOLDS, tmp_path, "unavailable", None, run_id)
    result = json.loads((run_dir / "evaluation-results.json").read_text(encoding="utf-8"))
    assert result["real_provider_quality"] == "pending"
    assert result["threshold_checks"]["real_provider_thresholds_passed"] is False
    with pytest.raises(FileExistsError):
        write_run(rows, DATASET, THRESHOLDS, tmp_path, "unavailable", None, run_id)


def test_dataset_rejects_duplicate_id_and_extra_fields(tmp_path: Path) -> None:
    row = load_dataset(DATASET)[0]
    row["unexpected"] = True
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="extra fields|duplicate"):
        load_dataset(path)
