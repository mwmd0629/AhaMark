"""Run the deterministic, local linear-algebra safety evaluation.

This runner imports the production validation bridge. It never opens a
network connection and consumes only synthetic JSON supplied by the repo.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ruff: noqa: E402
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
sys.path.insert(0, str(REPO_ROOT))

from app.math_validation.engine import Limits
from app.math_validation.linear_algebra import (
    ValidationRefs,
    validate_linear_algebra,
)

PROVIDER_MODE = "unavailable_or_test_fake_only"
PROVIDER_GATE = {
    "production_ready": False,
    "false_verified_max": 0,
    "reference_interception_min": 1.0,
    "manual_unsupported_adherence_min": 1.0,
    "status_accuracy_min": 0.95,
    "human_review_rate_min": 0.90,
    "human_review_required": True,
    "privacy_cost_latency_evidence_required": True,
}


def _refs(raw: dict[str, Any] | None, default_generation: int = 1) -> ValidationRefs:
    raw = raw or {}
    return ValidationRefs(
        str(raw.get("answer_id", "synthetic-answer")),
        str(raw.get("criterion_id", "synthetic-criterion")),
        str(raw.get("rubric_version_id", "synthetic-rubric")),
        str(raw.get("reference_answer_version_id", "synthetic-reference")),
        int(raw.get("generation", default_generation)),
    )


def evaluate(dataset: dict[str, Any]) -> dict[str, Any]:
    cases = dataset.get("cases")
    if dataset.get("schema_version") != "linear-algebra-eval-v1" or not isinstance(cases, list):
        raise ValueError("invalid evaluation dataset schema")
    results: list[dict[str, Any]] = []
    for case in cases:
        refs = _refs(case.get("refs"))
        current_refs = _refs(case.get("current_refs"), refs.generation)
        limits = Limits(**case["limits"]) if case.get("limits") else None
        result = validate_linear_algebra(
            str(case["answer_type"]),
            case.get("rule", {}),
            case.get("student"),
            case.get("expected"),
            refs=refs,
            current_refs=current_refs,
            limits=limits,
        )
        results.append(
            {
                "id": str(case["id"]),
                "answer_type": str(case["answer_type"]),
                "expected_status": str(case["expected_status"]),
                "actual_status": result.status,
                "reason": result.reason,
                "error_code": result.error_code,
                "verified": result.status == "verified",
            }
        )
    total = len(results)
    expected = Counter(row["expected_status"] for row in results)
    actual = Counter(row["actual_status"] for row in results)
    correct = sum(row["expected_status"] == row["actual_status"] for row in results)
    non_verified = [row for row in results if row["expected_status"] != "verified"]
    false_verified = sum(row["actual_status"] == "verified" for row in non_verified)
    ref_cases = [row for row in results if row["expected_status"] == "stale"]
    ref_intercepted = sum(row["actual_status"] == "stale" for row in ref_cases)
    manual_cases = [row for row in results if row["expected_status"] in {"manual", "unsupported"}]
    manual_adhered = sum(row["actual_status"] in {"manual", "unsupported"} for row in manual_cases)
    review_cases = [row for row in results if row["expected_status"] != "verified"]
    review_rate = (
        sum(row["actual_status"] != "verified" for row in review_cases) / len(review_cases)
        if review_cases
        else 1.0
    )
    metrics = {
        "total_cases": total,
        "expected_status_counts": dict(sorted(expected.items())),
        "actual_status_counts": dict(sorted(actual.items())),
        "status_accuracy": correct / total if total else 0.0,
        "false_verified": false_verified,
        "false_verified_rate": false_verified / len(non_verified) if non_verified else 0.0,
        "safe_human_review_rate": review_rate,
        "manual_unsupported_adherence": manual_adhered / len(manual_cases) if manual_cases else 1.0,
        "reference_interception_rate": ref_intercepted / len(ref_cases) if ref_cases else 1.0,
        "deterministic": True,
        "provider_mode": PROVIDER_MODE,
    }
    by_type: dict[str, dict[str, Any]] = {}
    for kind in sorted({row["answer_type"] for row in results}):
        subset = [row for row in results if row["answer_type"] == kind]
        by_type[kind] = {
            "cases": len(subset),
            "status_accuracy": sum(x["expected_status"] == x["actual_status"] for x in subset)
            / len(subset),
        }
    return {
        "schema_version": "linear-algebra-eval-report-v1",
        "dataset_schema_version": dataset["schema_version"],
        "provider_gate": PROVIDER_GATE,
        "metrics": metrics,
        "by_answer_type": by_type,
        "failures": [row for row in results if row["expected_status"] != row["actual_status"]],
    }


def gate_passed(report: dict[str, Any]) -> bool:
    metrics = report["metrics"]
    return (
        metrics["false_verified"] <= PROVIDER_GATE["false_verified_max"]
        and metrics["reference_interception_rate"] >= PROVIDER_GATE["reference_interception_min"]
        and metrics["manual_unsupported_adherence"]
        >= PROVIDER_GATE["manual_unsupported_adherence_min"]
        and metrics["status_accuracy"] >= PROVIDER_GATE["status_accuracy_min"]
        and metrics["safe_human_review_rate"] >= PROVIDER_GATE["human_review_rate_min"]
        and not report["provider_gate"]["production_ready"]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(json.loads(args.dataset.read_text(encoding="utf-8")))
    report["gate_passed_for_current_safe_mode"] = gate_passed(report)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not report["gate_passed_for_current_safe_mode"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
