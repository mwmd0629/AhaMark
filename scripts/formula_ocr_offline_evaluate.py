"""Evaluate formula-recognition candidates from a sanitized, local JSON manifest."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

# ruff: noqa: E402
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.recognition.formula import (
    FormulaCandidate,
    normalize_latex,
    select_top_candidate,
    token_similarity,
    validate_eval_dataset,
)

REPORT_SCHEMA_VERSION = "formula-ocr-eval-report-v1"
REVIEW_CONFIDENCE_THRESHOLD = 0.90


def _candidate(raw: object) -> FormulaCandidate:
    if not isinstance(raw, dict) or not isinstance(raw.get("latex"), str):
        raise ValueError("each formula prediction must contain latex")
    confidence = raw.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1
    ):
        raise ValueError("formula prediction confidence must be within 0..1")
    warnings = raw.get("warning_codes", [])
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise ValueError("warning_codes must be a string list")
    return FormulaCandidate(
        latex=raw["latex"],
        confidence=float(confidence) if confidence is not None else None,
        provider=str(raw.get("provider", "offline")),
        provider_version=str(raw.get("provider_version", "unknown")),
        warning_codes=tuple(warnings),
    )


def evaluate(dataset: object) -> dict[str, Any]:
    cases = validate_eval_dataset(dataset)
    results: list[dict[str, Any]] = []
    for case in cases:
        candidates = [_candidate(raw) for raw in cast(list[object], case["predictions"])]
        top = select_top_candidate(candidates)
        expected = str(case["expected_latex"])
        exact = top is not None and normalize_latex(top.latex) == normalize_latex(expected)
        confidence = top.confidence if top is not None else None
        warning_codes = list(top.warning_codes) if top is not None else ["NO_CANDIDATE"]
        review_required = (
            top is None
            or confidence is None
            or confidence < REVIEW_CONFIDENCE_THRESHOLD
            or bool(warning_codes)
        )
        results.append(
            {
                "id": case["id"],
                "modality": case["modality"],
                "candidate_count": len(candidates),
                "normalized_exact": exact,
                "token_similarity": token_similarity(expected, top.latex) if top else 0.0,
                "confidence": confidence,
                "review_required": review_required,
                "warning_codes": warning_codes,
            }
        )
    total = len(results)
    modality_counts = Counter(str(row["modality"]) for row in results)
    modality_metrics: dict[str, dict[str, int | float]] = {}
    for modality in sorted(modality_counts):
        rows = [row for row in results if row["modality"] == modality]
        count = len(rows)
        modality_metrics[modality] = {
            "total_cases": count,
            "normalized_exact_match_rate": sum(row["normalized_exact"] for row in rows) / count,
            "mean_token_similarity": sum(row["token_similarity"] for row in rows) / count,
            "manual_review_rate": sum(row["review_required"] for row in rows) / count,
        }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "dataset_schema_version": "formula-ocr-eval-v1",
        "production_ready": False,
        "human_confirmation_required": True,
        "metrics": {
            "total_cases": total,
            "normalized_exact_match_rate": sum(row["normalized_exact"] for row in results) / total,
            "mean_token_similarity": sum(row["token_similarity"] for row in results) / total,
            "manual_review_rate": sum(row["review_required"] for row in results) / total,
            "modality_counts": dict(sorted(modality_counts.items())),
            "by_modality": modality_metrics,
        },
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(json.loads(args.dataset.read_text(encoding="utf-8")))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
