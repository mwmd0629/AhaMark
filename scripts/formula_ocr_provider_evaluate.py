"""Run a formula provider against sanitized PNG crops and emit path-free metrics."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError
from pydantic import SecretStr

# ruff: noqa: E402
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.core.config import Settings
from app.recognition.formula import (
    FORMULA_EVAL_SCHEMA_VERSION,
    FormulaRecognitionProvider,
    FormulaRegionArtifact,
    HttpFormulaProvider,
    validate_eval_dataset,
)
from app.recognition.pipeline import PageArtifact

from scripts.formula_ocr_offline_evaluate import evaluate

PROVIDER_DATASET_SCHEMA_VERSION = "formula-ocr-provider-eval-v1"
ALLOWED_CASE_KEYS = {"id", "modality", "expected_latex", "region_kind"}
ALLOWED_REGION_KINDS = {"inline", "display", "unknown"}
MAX_CASES = 1_000
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


def validate_provider_dataset(dataset: object) -> list[dict[str, object]]:
    if (
        not isinstance(dataset, dict)
        or set(dataset) != {"schema_version", "cases"}
        or dataset.get("schema_version") != PROVIDER_DATASET_SCHEMA_VERSION
    ):
        raise ValueError("invalid formula provider evaluation schema")
    raw_cases = dataset.get("cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= MAX_CASES:
        raise ValueError(f"formula provider evaluation requires 1..{MAX_CASES} cases")
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or set(raw_case) != ALLOWED_CASE_KEYS:
            raise ValueError("formula provider evaluation case fields are invalid")
        if raw_case.get("region_kind") not in ALLOWED_REGION_KINDS:
            raise ValueError("unsupported formula region kind")
    validation_copy = {
        "schema_version": FORMULA_EVAL_SCHEMA_VERSION,
        "cases": [dict(cast(dict[str, object], case), predictions=[]) for case in raw_cases],
    }
    validate_eval_dataset(validation_copy)
    return cast(list[dict[str, object]], raw_cases)


def _read_image(image_path: Path) -> PageArtifact:
    if image_path.is_symlink() or not image_path.is_file():
        raise ValueError("each evaluation case must have a regular PNG crop")
    content = image_path.read_bytes()
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("formula evaluation crop exceeds byte limit")
    try:
        with Image.open(image_path) as image:
            if image.format != "PNG":
                raise ValueError("formula evaluation crops must be PNG")
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("formula evaluation crop is not a valid PNG") from exc
    if width < 2 or height < 2 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("formula evaluation crop dimensions are invalid")
    return PageArtifact(content, width, height)


def evaluate_provider(
    dataset: object,
    image_dir: Path,
    provider: FormulaRecognitionProvider,
) -> dict[str, Any]:
    cases = validate_provider_dataset(dataset)
    if image_dir.is_symlink() or not image_dir.is_dir():
        raise ValueError("image directory must be a regular local directory")
    expected_names = {f"{case['id']}.png" for case in cases}
    actual_names = {item.name for item in image_dir.iterdir()}
    if actual_names != expected_names:
        raise ValueError("image directory must contain exactly one case-id PNG per case")
    available, reason = provider.available()
    if not available:
        raise RuntimeError(reason or "formula provider is unavailable")

    scored_cases: list[dict[str, object]] = []
    for case in cases:
        artifact = FormulaRegionArtifact(
            _read_image(image_dir / f"{case['id']}.png"),
            (0.0, 0.0, 1.0, 1.0),
            str(case["region_kind"]),
        )
        candidates = provider.recognize(artifact)
        scored_cases.append(
            {
                "id": case["id"],
                "modality": case["modality"],
                "expected_latex": case["expected_latex"],
                "predictions": [
                    {
                        "latex": candidate.latex,
                        "confidence": candidate.confidence,
                        "provider": candidate.provider,
                        "provider_version": candidate.provider_version,
                        "warning_codes": list(candidate.warning_codes),
                    }
                    for candidate in candidates
                ],
            }
        )
    report = evaluate({"schema_version": FORMULA_EVAL_SCHEMA_VERSION, "cases": scored_cases})
    report["source_schema_version"] = PROVIDER_DATASET_SCHEMA_VERSION
    return report


def _http_provider(base_url: str, token: str) -> HttpFormulaProvider:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("provider evaluation is restricted to localhost HTTP")
    if len(token) < 32:
        raise ValueError("provider token must contain at least 32 characters")
    return HttpFormulaProvider(
        Settings(
            app_env="development",
            formula_recognition_provider="http",
            formula_recognition_base_url=base_url.rstrip("/"),
            formula_recognition_api_key=SecretStr(token),
            formula_recognition_allowed_hosts=[parsed.hostname],
            formula_recognition_timeout_seconds=120,
            formula_recognition_max_image_bytes=MAX_IMAGE_BYTES,
            formula_recognition_max_pixels=MAX_IMAGE_PIXELS,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("image_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--provider-base-url",
        default="http://127.0.0.1:8765",
    )
    args = parser.parse_args()
    token = os.environ.get("AHAMARK_FORMULA_PROVIDER_TOKEN", "")
    report = evaluate_provider(
        json.loads(args.dataset.read_text(encoding="utf-8")),
        args.image_dir,
        _http_provider(args.provider_base_url, token),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
