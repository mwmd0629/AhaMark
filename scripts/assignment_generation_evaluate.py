"""Offline evaluation and evidence writer for assignment-generation v1.

The evaluator never calls a provider and never writes the application database.  A
fake/unavailable run may validate deterministic routing and safety, but it can
never satisfy the real-provider quality gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATASET_VERSION = "assignment-generation-evaluation-v1"
DATASET_VERSION_V2 = "assignment-generation-evaluation-v2"
SUPPORTED_DATASET_VERSIONS = {DATASET_VERSION, DATASET_VERSION_V2}
SCHEMA_VERSION = "assignment-generation-evaluation-schema-v1"
PROMPT_VERSION = "assignment-generation-prompt-v1"
REQUIRED_FIELDS = {
    "case_id",
    "dataset_version",
    "category",
    "source_type",
    "synthetic",
    "input_files",
    "page_ground_truth",
    "file_role_ground_truth",
    "answer_source_ground_truth",
    "question_ground_truth",
    "answer_ground_truth",
    "rubric_ground_truth",
    "expected_issues",
    "expected_manual_review",
    "forbidden_actions",
    "notes",
}
SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|password|cookie|csrf|authorization|database_url|signed[_-]?url)"
    r"\s*[:=]\s*[^\s,}\]]+"
)
REAL_PROVIDERS = {"openai", "openai_compatible"}
SAFETY_ZERO_METRICS = {
    "auto_publish_attempt_count",
    "unauthorized_publish_success_count",
    "stale_overwrite_count",
    "teacher_edit_overwrite_count",
    "official_source_escalation_count",
    "cross_owner_data_leak_count",
    "prompt_injection_control_success_count",
    "evidence_unknown_reference_count",
}


def safe_ratio(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "")).casefold()


def levenshtein(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for index, char in enumerate(left, 1):
        current = [index]
        for other_index, other in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[other_index] + 1,
                    previous[other_index - 1] + (char != other),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(expected: str | None, actual: str | None) -> float | None:
    gold = normalize_text(expected)
    predicted = normalize_text(actual)
    if not gold:
        return 0.0 if not predicted else None
    return levenshtein(gold, predicted) / len(gold)


def boundary_iou(left: list[float], right: list[float]) -> float:
    if len(left) != 4 or len(right) != 4:
        raise ValueError("boundaries must contain [x0,y0,x1,y1]")
    if any(not math.isfinite(float(value)) for value in [*left, *right]):
        raise ValueError("boundary coordinates must be finite")
    if any(float(value) < 0 or float(value) > 1 for value in [*left, *right]):
        raise ValueError("boundary coordinates must be normalized")
    lx0, ly0, lx1, ly1 = map(float, left)
    rx0, ry0, rx1, ry1 = map(float, right)
    if lx1 < lx0 or ly1 < ly0 or rx1 < rx0 or ry1 < ry0:
        raise ValueError("boundary coordinates are inverted")
    intersection = max(0.0, min(lx1, rx1) - max(lx0, rx0)) * max(0.0, min(ly1, ry1) - max(ly0, ry0))
    union = (lx1 - lx0) * (ly1 - ly0) + (rx1 - rx0) * (ry1 - ry0) - intersection
    return intersection / union if union else 1.0


def load_dataset(path: Path, expected_version: str | None = None) -> list[dict[str, Any]]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    versions = {str(row.get("dataset_version")) for row in rows}
    if expected_version is None:
        expected_version = next(iter(versions)) if len(versions) == 1 else DATASET_VERSION
    if expected_version not in SUPPORTED_DATASET_VERSIONS:
        raise ValueError("unsupported evaluation dataset version")
    errors: list[str] = []
    ids: set[str] = set()
    for index, row in enumerate(rows, 1):
        missing = sorted(REQUIRED_FIELDS - row.keys())
        extra = sorted(row.keys() - (REQUIRED_FIELDS | {"prediction", "coverage_tags"}))
        if missing:
            errors.append(f"line {index}: missing {missing}")
        if extra:
            errors.append(f"line {index}: extra fields {extra}")
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,79}", case_id):
            errors.append(f"line {index}: invalid case_id")
        elif case_id in ids:
            errors.append(f"line {index}: duplicate case_id {case_id}")
        ids.add(str(case_id))
        if row.get("dataset_version") != expected_version:
            errors.append(f"line {index}: dataset_version does not match frozen version")
        if row.get("synthetic") is not True:
            errors.append(f"line {index}: Git fixture must be synthetic")
        if not isinstance(row.get("forbidden_actions"), list) or "publish" not in row.get(
            "forbidden_actions", []
        ):
            errors.append(f"line {index}: publish must be forbidden")
    if errors:
        raise ValueError("invalid evaluation dataset:\n" + "\n".join(errors))
    return rows


def _binary_counts(gold: set[Any], predicted: set[Any]) -> tuple[int, int, int]:
    return len(gold & predicted), len(predicted - gold), len(gold - predicted)


def evaluate(rows: list[dict[str, Any]], provider: str) -> dict[str, Any]:
    exact_roles = exact_sources = page_count = page_order = 0
    question_number = question_count = question_type = question_text = score = missing_score = 0
    cross_page = multi_region = parent_child = 0
    boundaries: list[float] = []
    cer_values: list[float] = []
    blank = [0, 0, 0]
    duplicate = [0, 0, 0]
    low_quality_gold = low_quality_hit = missing_issue_gold = missing_issue_hit = 0
    variant_gold = variant_hit = blocking_gold = blocking_hit = false_blocking = 0
    answer_source_preservation = answer_structure = alternative_answer = 0
    rubric_schema = rubric_points = dependency = alternative_path = partial_credit = 0
    deterministic_routing = manual_routing = validation_evidence = evidence_total = 0
    high_confidence_errors = teacher_modified = teacher_reviewed = 0
    safety = Counter({name: 0 for name in SAFETY_ZERO_METRICS})
    latency: list[float] = []
    stage_latency: dict[str, list[float]] = {}
    input_tokens = output_tokens = image_count = image_bytes = retries = manual_reviews = 0
    costs: list[float] = []

    for row in rows:
        gold_pages = row["page_ground_truth"]
        gold_questions = row["question_ground_truth"]
        gold_answer = row["answer_ground_truth"]
        gold_rubric = row["rubric_ground_truth"]
        prediction = row.get("prediction") or {}
        pred_pages = prediction.get("pages", {})
        pred_questions = prediction.get("questions", [])
        pred_answer = prediction.get("answer", {})
        pred_rubric = prediction.get("rubric", {})
        case_text_error = False
        issues = set(prediction.get("issues", []))
        expected_issues = set(row["expected_issues"])

        exact_roles += prediction.get("file_roles") == row["file_role_ground_truth"]
        exact_sources += prediction.get("answer_sources") == row["answer_source_ground_truth"]
        page_count += pred_pages.get("count") == gold_pages.get("count")
        page_order += pred_pages.get("order") == gold_pages.get("order")
        for key, accumulator in (("blank", blank), ("duplicate", duplicate)):
            counts = _binary_counts(set(gold_pages.get(key, [])), set(pred_pages.get(key, [])))
            for index, value in enumerate(counts):
                accumulator[index] += value
        low = set(gold_pages.get("low_quality", []))
        low_quality_gold += len(low)
        low_quality_hit += len(low & set(pred_pages.get("low_quality", [])))
        missing_expected = "MISSING_PAGE_SUSPECTED" in expected_issues
        variant_expected = any(code.startswith("VARIANT_") for code in expected_issues)
        missing_issue_gold += missing_expected
        missing_issue_hit += missing_expected and "MISSING_PAGE_SUSPECTED" in issues
        variant_gold += variant_expected
        variant_hit += variant_expected and any(code.startswith("VARIANT_") for code in issues)

        question_count += len(pred_questions) == len(gold_questions)
        by_id = {str(question.get("id")): question for question in pred_questions}
        for question in gold_questions:
            predicted = by_id.get(str(question.get("id")), {})
            question_number += predicted.get("number") == question.get("number")
            question_type += predicted.get("type") == question.get("type")
            text_equal = normalize_text(predicted.get("text")) == normalize_text(
                question.get("text")
            )
            case_text_error = case_text_error or not text_equal
            question_text += text_equal
            cer = character_error_rate(question.get("text"), predicted.get("text"))
            if cer is not None:
                cer_values.append(cer)
            if question.get("boundary") is not None and predicted.get("boundary") is not None:
                boundaries.append(boundary_iou(question["boundary"], predicted["boundary"]))
            cross_page += bool(predicted.get("cross_page")) == bool(question.get("cross_page"))
            multi_region += int(predicted.get("region_count", 1)) == int(
                question.get("region_count", 1)
            )
            parent_child += predicted.get("parent_id") == question.get("parent_id")
            if question.get("score") is None:
                missing_score += predicted.get("score") is None
            else:
                score += predicted.get("score") == question.get("score")

        answer_source_preservation += pred_answer.get("source") == gold_answer.get("source")
        answer_structure += bool(pred_answer.get("structure_valid")) == bool(
            gold_answer.get("structure_valid", True)
        )
        alternative_answer += bool(pred_answer.get("alternatives_valid")) == bool(
            gold_answer.get("alternatives_valid", True)
        )
        rubric_schema += bool(pred_rubric.get("schema_valid")) == bool(
            gold_rubric.get("schema_valid", True)
        )
        rubric_points += bool(pred_rubric.get("points_consistent")) == bool(
            gold_rubric.get("points_consistent", True)
        )
        dependency += bool(pred_rubric.get("dependency_valid")) == bool(
            gold_rubric.get("dependency_valid", True)
        )
        alternative_path += bool(pred_rubric.get("alternative_paths_valid")) == bool(
            gold_rubric.get("alternative_paths_valid", True)
        )
        partial_credit += bool(pred_rubric.get("partial_credit_valid")) == bool(
            gold_rubric.get("partial_credit_valid", True)
        )
        deterministic_routing += prediction.get("routing") == gold_rubric.get("routing")
        manual_routing += bool(prediction.get("manual_review")) == bool(
            row["expected_manual_review"]
        )
        allowed_refs = set(gold_rubric.get("allowed_evidence_refs", []))
        actual_refs = set(pred_rubric.get("evidence_refs", []))
        refs_valid = actual_refs <= allowed_refs and (bool(actual_refs) or not allowed_refs)
        validation_evidence += refs_valid
        evidence_total += 1
        safety["evidence_unknown_reference_count"] += len(actual_refs - allowed_refs)

        blocking_expected = {code for code in expected_issues if code.startswith("BLOCK_")}
        blocking_actual = {code for code in issues if code.startswith("BLOCK_")}
        blocking_gold += len(blocking_expected)
        blocking_hit += len(blocking_expected & blocking_actual)
        false_blocking += len(blocking_actual - blocking_expected)
        confidence = float(prediction.get("confidence", 0))
        high_confidence_errors += confidence >= 0.8 and (
            case_text_error or bool(blocking_actual - blocking_expected)
        )
        teacher_reviewed += int(prediction.get("teacher_reviewed", False))
        teacher_modified += int(prediction.get("teacher_modified", False))
        for name in SAFETY_ZERO_METRICS - {"evidence_unknown_reference_count"}:
            safety[name] += int(prediction.get(name, 0) or 0)
        perf = prediction.get("performance", {})
        if perf.get("end_to_end_latency_seconds") is not None:
            latency.append(float(perf["end_to_end_latency_seconds"]))
        for stage, value in perf.get("stage_latency_seconds", {}).items():
            stage_latency.setdefault(stage, []).append(float(value))
        input_tokens += int(perf.get("provider_input_tokens") or 0)
        output_tokens += int(perf.get("provider_output_tokens") or 0)
        image_count += int(perf.get("image_count") or 0)
        image_bytes += int(perf.get("image_bytes") or 0)
        retries += int(perf.get("retry_count") or 0)
        manual_reviews += int(bool(row["expected_manual_review"]))
        if perf.get("estimated_cost") is not None:
            costs.append(float(perf["estimated_cost"]))

    question_denominator = sum(len(row["question_ground_truth"]) for row in rows)
    scored_denominator = sum(
        question.get("score") is not None
        for row in rows
        for question in row["question_ground_truth"]
    )
    missing_score_denominator = question_denominator - scored_denominator
    real_provider_run = provider in REAL_PROVIDERS
    metrics: dict[str, Any] = {
        "case_count": len(rows),
        "file_role_accuracy": safe_ratio(exact_roles, len(rows)),
        "answer_source_accuracy": safe_ratio(exact_sources, len(rows)),
        "page_count_accuracy": safe_ratio(page_count, len(rows)),
        "page_order_accuracy": safe_ratio(page_order, len(rows)),
        "blank_page_precision": safe_ratio(blank[0], blank[0] + blank[1]),
        "blank_page_recall": safe_ratio(blank[0], blank[0] + blank[2]),
        "duplicate_page_precision": safe_ratio(duplicate[0], duplicate[0] + duplicate[1]),
        "duplicate_page_recall": safe_ratio(duplicate[0], duplicate[0] + duplicate[2]),
        "low_quality_page_recall": safe_ratio(low_quality_hit, low_quality_gold),
        "missing_page_issue_recall": safe_ratio(missing_issue_hit, missing_issue_gold),
        "variant_issue_recall": safe_ratio(variant_hit, variant_gold),
        "question_number_accuracy": safe_ratio(question_number, question_denominator),
        "question_count_accuracy": safe_ratio(question_count, len(rows)),
        "question_boundary_iou": sum(boundaries) / len(boundaries) if boundaries else None,
        "question_boundary_precision": safe_ratio(
            sum(value >= 0.5 for value in boundaries), len(boundaries)
        ),
        "question_boundary_recall": safe_ratio(
            sum(value >= 0.5 for value in boundaries), len(boundaries)
        ),
        "cross_page_accuracy": safe_ratio(cross_page, question_denominator),
        "multi_region_accuracy": safe_ratio(multi_region, question_denominator),
        "parent_child_accuracy": safe_ratio(parent_child, question_denominator),
        "question_type_accuracy": safe_ratio(question_type, question_denominator),
        "question_text_exact_or_normalized_accuracy": safe_ratio(
            question_text, question_denominator
        ),
        "question_text_character_error_rate": sum(cer_values) / len(cer_values)
        if cer_values
        else None,
        "score_accuracy": safe_ratio(score, scored_denominator),
        "missing_score_abstention_accuracy": safe_ratio(missing_score, missing_score_denominator),
        "answer_source_preservation": safe_ratio(answer_source_preservation, len(rows)),
        "answer_structure_validity": safe_ratio(answer_structure, len(rows)),
        "alternative_answer_validity": safe_ratio(alternative_answer, len(rows)),
        "rubric_schema_validity": safe_ratio(rubric_schema, len(rows)),
        "rubric_points_consistency": safe_ratio(rubric_points, len(rows)),
        "dependency_validity": safe_ratio(dependency, len(rows)),
        "alternative_path_validity": safe_ratio(alternative_path, len(rows)),
        "partial_credit_validity": safe_ratio(partial_credit, len(rows)),
        "deterministic_routing_accuracy": safe_ratio(deterministic_routing, len(rows)),
        "manual_only_routing_accuracy": safe_ratio(manual_routing, len(rows)),
        "validation_evidence_accuracy": safe_ratio(validation_evidence, evidence_total),
        "blocking_issue_recall": safe_ratio(blocking_hit, blocking_gold),
        "false_blocking_issue_rate": safe_ratio(false_blocking, len(rows)),
        "evidence_reference_validity": safe_ratio(validation_evidence, evidence_total),
        "high_confidence_error_rate": safe_ratio(high_confidence_errors, len(rows)),
        "teacher_modification_rate": safe_ratio(teacher_modified, teacher_reviewed),
        **dict(safety),
        "end_to_end_latency_seconds": sum(latency) / len(latency) if latency else None,
        "stage_latency_seconds": {
            stage: sum(values) / len(values) for stage, values in sorted(stage_latency.items())
        },
        "provider_input_tokens": input_tokens if real_provider_run else None,
        "provider_output_tokens": output_tokens if real_provider_run else None,
        "image_count": image_count,
        "image_bytes": image_bytes,
        "estimated_cost": sum(costs) if real_provider_run and len(costs) == len(rows) else None,
        "retry_count": retries,
        "manual_review_count": manual_reviews,
    }
    return metrics


def threshold_checks(
    metrics: dict[str, Any], thresholds: dict[str, Any], provider: str
) -> dict[str, Any]:
    groups: dict[str, dict[str, bool]] = {}
    for group, rules in thresholds.items():
        if group in {"version", "minimum_real_provider_cases"}:
            continue
        checks: dict[str, bool] = {}
        for metric, rule in rules.items():
            value = metrics.get(metric)
            checks[metric] = value is not None and (
                ("min" not in rule or value >= rule["min"])
                and ("max" not in rule or value <= rule["max"])
                and ("equals" not in rule or value == rule["equals"])
            )
        groups[group] = checks
    real_run = provider in REAL_PROVIDERS
    minimum = int(thresholds.get("minimum_real_provider_cases", 30))
    real_checks = groups.get("real_provider_thresholds", {})
    return {
        "groups": groups,
        "structural_thresholds_passed": all(groups.get("structural_thresholds", {}).values()),
        "safety_thresholds_passed": all(groups.get("safety_thresholds", {}).values()),
        "fake_flow_thresholds_passed": all(groups.get("fake_flow_thresholds", {}).values()),
        "real_provider_run": real_run,
        "real_provider_thresholds_passed": bool(
            real_run
            and metrics["case_count"] >= minimum
            and real_checks
            and all(real_checks.values())
        ),
    }


def _git_summary(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
        return result.stdout.strip()

    status = run("status", "--short")
    return {
        "commit": run("rev-parse", "HEAD"),
        "dirty": bool(status),
        "changed_path_count": len(status.splitlines()),
    }


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def redactable_json(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if SECRET_PATTERN.search(rendered):
        raise ValueError("evidence contains a secret-like key/value")
    return rendered


def write_run(
    rows: list[dict[str, Any]],
    dataset_path: Path,
    thresholds_path: Path,
    output_root: Path,
    provider: str,
    model: str | None,
    run_id: str | None = None,
) -> Path:
    started = datetime.now(UTC)
    versions = {row["dataset_version"] for row in rows}
    if len(versions) != 1 or next(iter(versions)) not in SUPPORTED_DATASET_VERSIONS:
        raise ValueError("run requires one supported frozen dataset version")
    dataset_version = next(iter(versions))
    version_slug = dataset_version.rsplit("-", 1)[-1]
    run_id = run_id or f"assignment-generation-{version_slug}-{started.strftime('%Y%m%d-%H%M%S')}"
    if not re.fullmatch(r"assignment-generation-v[12]-[A-Za-z0-9_.-]+", run_id):
        raise ValueError("invalid run_id")
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    thresholds = json.loads(thresholds_path.read_text(encoding="utf-8"))
    metrics = evaluate(rows, provider)
    checks = threshold_checks(metrics, thresholds, provider)
    completed = datetime.now(UTC)
    reason = None
    if not checks["real_provider_run"]:
        reason = (
            "credentials_unavailable"
            if provider == "unavailable"
            else "fake_provider_not_quality_evidence"
        )
    elif not checks["real_provider_thresholds_passed"]:
        reason = "insufficient_gold_data_or_threshold_failure"
    status = (
        "passed"
        if checks["structural_thresholds_passed"] and checks["safety_thresholds_passed"]
        else "failed"
    )
    result = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "status": status,
        "provider": provider,
        "model_snapshot": model,
        "dataset_version": dataset_version,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "threshold_version": thresholds["version"],
        "metrics": metrics,
        "threshold_checks": checks,
        "real_provider_quality": "pass" if checks["real_provider_thresholds_passed"] else "pending",
        "real_provider_reason": reason,
        "case_results": [{"case_id": row["case_id"], "status": "evaluated"} for row in rows],
        "failures": []
        if status == "passed"
        else ["one or more structural/safety thresholds failed"],
    }
    common = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "status": status,
    }
    environment = {
        **common,
        "environment": "offline_local",
        "source": _git_summary(Path.cwd()),
        "provider": provider,
        "model_snapshot": model,
    }
    manifest = {
        **common,
        "dataset_version": dataset_version,
        "case_count": len(rows),
        "dataset_sha256": _hash(dataset_path),
        "synthetic_only": all(row["synthetic"] for row in rows),
    }
    files = {
        "environment.json": environment,
        "config-snapshot.json": {
            **common,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "provider": provider,
            "model_snapshot": model,
        },
        "dataset-manifest.json": manifest,
        "evaluation-results.json": result,
        "evaluation-thresholds.json": {**common, "thresholds": thresholds},
    }
    for name, value in files.items():
        redactable_json(value)
        (run_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    hashes = {name: _hash(run_dir / name) for name in files}
    hashes_doc = {**common, "hashes": hashes}
    (run_dir / "hashes.json").write_text(
        json.dumps(hashes_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown = [
        f"# Assignment Generation Evaluation {run_id}",
        "",
        f"- Status: **{status.upper()}**",
        f"- Cases: {len(rows)} synthetic cases ({dataset_version})",
        f"- Provider: `{provider}`",
        f"- Structural thresholds: {checks['structural_thresholds_passed']}",
        f"- Safety thresholds: {checks['safety_thresholds_passed']}",
        f"- Fake/unavailable flow thresholds: {checks['fake_flow_thresholds_passed']}",
        "- Real-provider quality: **"
        + ("PASS" if checks["real_provider_thresholds_passed"] else "PENDING")
        + "**",
        f"- Real-provider reason: `{reason or 'none'}`",
        "",
        "Fake and unavailable results do not prove AI quality. "
        "AI/third-party answers are not official answers. No publish action is performed.",
    ]
    (run_dir / "EVALUATION.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument(
        "--output-root", type=Path, default=Path(".preproduction-assignment-generation")
    )
    parser.add_argument(
        "--provider",
        choices=["unavailable", "fake", "openai", "openai_compatible"],
        default="unavailable",
    )
    parser.add_argument("--model")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    rows = load_dataset(args.dataset)
    run_dir = write_run(
        rows,
        args.dataset,
        args.thresholds,
        args.output_root,
        args.provider,
        args.model,
        args.run_id,
    )
    print(run_dir)


if __name__ == "__main__":
    main()
