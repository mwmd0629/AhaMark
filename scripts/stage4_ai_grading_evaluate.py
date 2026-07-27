"""Offline, provider-neutral Stage 4 criterion evaluation.

Input is JSONL. Each row contains source_group, gold.criteria and prediction.criteria.
Rows marked stage4_e2e are excluded from business statistics by design.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact = within_one = evidence_valid = 0
    score_errors = 0.0
    gold_abstain = predicted_abstain = true_abstain = 0
    gold_manual = recalled_manual = gold_conflict = recalled_conflict = 0
    criterion_count = 0
    total_errors: list[float] = []
    latencies: list[float] = []
    tokens = cost = 0.0
    error_tp = error_pred = error_gold = high_confidence_errors = 0
    teacher_adopted = teacher_reviewed = 0
    teacher_deltas: list[float] = []
    source_groups: Counter[str] = Counter()
    for row in rows:
        source_groups[str(row["source_group"])] += 1
        gold = {x["criterion_stable_key"]: x for x in row["gold"]["criteria"]}
        pred = {x["criterion_stable_key"]: x for x in row["prediction"]["criteria"]}
        gold_total = predicted_total = 0.0
        for key, expected in gold.items():
            actual = pred.get(key, {})
            criterion_count += 1
            expected_score = expected.get("points")
            actual_score = actual.get("suggested_points")
            if expected_score is not None:
                gold_total += float(expected_score)
            if actual_score is not None:
                predicted_total += float(actual_score)
            if actual_score == expected_score:
                exact += 1
            elif actual_score is not None and expected_score is not None:
                delta = abs(float(actual_score) - float(expected_score))
                score_errors += delta
                within_one += delta <= 1
                high_confidence_errors += float(actual.get("confidence") or 0) >= 0.8
            expected_refs = set(expected.get("allowed_evidence_refs", []))
            actual_refs = set(actual.get("evidence_refs", []))
            evidence_valid += bool(actual_refs) and actual_refs <= expected_refs
            expected_errors = set(expected.get("detected_errors", []))
            actual_errors = set(actual.get("detected_errors", []))
            error_tp += len(expected_errors & actual_errors)
            error_gold += len(expected_errors)
            error_pred += len(actual_errors)
            expected_abstain = bool(expected.get("abstain"))
            actual_abstain = actual.get("suggested_points") is None
            gold_abstain += expected_abstain
            predicted_abstain += actual_abstain
            true_abstain += expected_abstain and actual_abstain
            gold_manual += bool(expected.get("manual_required"))
            recalled_manual += bool(expected.get("manual_required")) and actual.get("status") in {
                "manual_required",
                "abstain",
                "insufficient_evidence",
            }
            gold_conflict += bool(expected.get("deterministic_conflict"))
            recalled_conflict += bool(expected.get("deterministic_conflict")) and (
                actual.get("status") == "deterministic_conflict"
            )
        total_errors.append(abs(predicted_total - gold_total))
        latencies.append(float(row.get("latency_ms", 0)))
        tokens += float(row.get("input_tokens", 0)) + float(row.get("output_tokens", 0))
        cost += float(row.get("estimated_cost", 0))
        for decision in row.get("teacher_decisions", []):
            teacher_reviewed += 1
            teacher_adopted += decision.get("action") == "accepted"
            if decision.get("ai_points") is not None and decision.get("teacher_points") is not None:
                teacher_deltas.append(
                    abs(float(decision["teacher_points"]) - float(decision["ai_points"]))
                )
    return {
        "rows": len(rows),
        "source_groups": dict(source_groups),
        "criterion_exact_agreement": safe_ratio(exact, criterion_count),
        "criterion_score_mae": safe_ratio(int(score_errors * 1000), criterion_count * 1000),
        "total_score_mae": statistics.fmean(total_errors) if total_errors else None,
        "within_one_point_rate": safe_ratio(within_one + exact, criterion_count),
        "error_type_precision": safe_ratio(error_tp, error_pred),
        "error_type_recall": safe_ratio(error_tp, error_gold),
        "evidence_reference_valid_rate": safe_ratio(evidence_valid, criterion_count),
        "high_confidence_error_rate": safe_ratio(high_confidence_errors, criterion_count),
        "abstain_precision": safe_ratio(true_abstain, predicted_abstain),
        "abstain_recall": safe_ratio(true_abstain, gold_abstain),
        "manual_required_recall": safe_ratio(recalled_manual, gold_manual),
        "deterministic_conflict_recall": safe_ratio(recalled_conflict, gold_conflict),
        "mean_latency_ms": statistics.fmean(latencies) if latencies else None,
        "total_tokens": tokens,
        "estimated_cost": cost,
        "teacher_adoption_rate": safe_ratio(teacher_adopted, teacher_reviewed),
        "teacher_mean_absolute_score_change": (
            statistics.fmean(teacher_deltas) if teacher_deltas else None
        ),
        "quality_claim": (
            "insufficient_teacher_gold"
            if len(rows) < 30
            else "evaluate_against_configured_thresholds"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--thresholds",
        type=Path,
        help="JSON mapping metric names to {min: number} or {max: number}",
    )
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # Leakage guard: one source group may appear in only one split.
    memberships: dict[str, set[str]] = {}
    for row in rows:
        memberships.setdefault(str(row["source_group"]), set()).add(str(row["split"]))
    leaked = sorted(key for key, splits in memberships.items() if len(splits) > 1)
    if leaked:
        raise SystemExit(f"source-group split leakage: {leaked}")
    result = evaluate(rows)
    if args.thresholds:
        thresholds = json.loads(args.thresholds.read_text(encoding="utf-8"))
        checks: dict[str, bool] = {}
        for metric, rule in thresholds.items():
            value = result.get(metric)
            checks[metric] = value is not None and (
                ("min" not in rule or value >= rule["min"])
                and ("max" not in rule or value <= rule["max"])
            )
        result["threshold_checks"] = checks
        result["thresholds_passed"] = all(checks.values())
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
