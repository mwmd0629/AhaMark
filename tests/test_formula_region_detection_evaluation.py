from __future__ import annotations

import copy
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from scripts.formula_region_detection_evaluate import (
    evaluate,
    validate_dataset,
    validate_predictions,
)
from scripts.formula_region_detection_synthetic_baseline import run_baseline


def uid(number: int) -> str:
    return str(uuid.UUID(int=number))


def region(
    number: int, bbox: tuple[float, float, float, float], kind: str, style: str
) -> dict[str, object]:
    x, y, width, height = bbox
    return {
        "region_id": uid(100 + number),
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "kind": kind,
        "print_style": style,
        "quality_flags": ["none"],
        "annotation_status": "confirmed",
    }


def case(
    number: int,
    modality: str,
    regions: list[dict[str, object]],
    *,
    status: str = "annotated",
    document_id: str | None = None,
    split: str = "test",
) -> dict[str, object]:
    return {
        "case_id": uid(number),
        "document_id": document_id or uid(50 + number),
        "split": split,
        "modality": modality,
        "page_width": 1000,
        "page_height": 1400,
        "contains_formula": bool(regions),
        "regions": regions,
        "quality_flags": ["none"],
        "negative_tags": [] if regions else ["body_text"],
        "annotation_status": status,
        "annotator_decision_version": "decision-v1",
    }


def dataset() -> dict[str, object]:
    return {
        "schema_version": "formula-region-detection-v1",
        "dataset_id": uid(500),
        "annotator_decision_version": "decision-v1",
        "cases": [
            case(1, "text_pdf", [region(1, (0.1, 0.1, 0.3, 0.1), "inline", "printed")]),
            case(2, "scan", [region(2, (0.1, 0.2, 0.4, 0.2), "multiline", "handwritten")]),
            case(
                3,
                "photo",
                [
                    region(3, (0.1, 0.1, 0.3, 0.2), "display", "mixed"),
                    region(4, (0.25, 0.1, 0.3, 0.2), "matrix", "mixed"),
                ],
            ),
            case(4, "synthetic", [], status="no_formula"),
            case(5, "scan", [], status="unjudgeable"),
        ],
    }


def proposal(number: int, bbox: tuple[float, float, float, float]) -> dict[str, object]:
    x, y, width, height = bbox
    return {
        "proposal_id": uid(200 + number),
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "score": 0.8,
        "detection_source": "synthetic-cc-v1",
    }


def predictions() -> dict[str, object]:
    return {
        "schema_version": "formula-region-predictions-v1",
        "detector": {"name": "synthetic-cc-smoke", "version": "1"},
        "cases": [
            {
                "case_id": uid(1),
                "proposals": [proposal(1, (0.1, 0.1, 0.3, 0.1))],
                "inference_ms": 2.0,
                "peak_memory_mb": 10.0,
            },
            {
                "case_id": uid(2),
                "proposals": [proposal(2, (0.1, 0.2, 0.2, 0.2)), proposal(3, (0.3, 0.2, 0.2, 0.2))],
                "inference_ms": 3.0,
                "peak_memory_mb": 12.0,
            },
            {
                "case_id": uid(3),
                "proposals": [proposal(4, (0.1, 0.1, 0.45, 0.2))],
                "inference_ms": 4.0,
            },
            {
                "case_id": uid(4),
                "proposals": [proposal(5, (0.2, 0.2, 0.2, 0.1))],
                "inference_ms": 1.0,
            },
            {
                "case_id": uid(5),
                "proposals": [proposal(6, (0.2, 0.2, 0.2, 0.1))],
                "inference_ms": 99.0,
            },
        ],
    }


def test_schema_accepts_sanitized_document_isolated_dataset() -> None:
    assert validate_dataset(dataset())["schema_version"] == "formula-region-detection-v1"


def test_schema_rejects_empty_annotated_page_without_human_no_formula_decision() -> None:
    value = dataset()
    empty = value["cases"][3]  # type: ignore[index]
    empty["annotation_status"] = "annotated"  # type: ignore[index]
    with pytest.raises(ValueError, match="annotated pages need regions"):
        validate_dataset(value)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["cases"][0].update({"extra": True}), "unknown fields"),
        (
            lambda value: value["cases"][0]["regions"][0]["bbox"].update({"width": 1.0}),
            "normalized page bounds",
        ),
        (lambda value: value["cases"][0].update({"source_path": "private"}), "private field"),
        (
            lambda value: value["cases"][0].update({"image": "data:image/png;base64,AA"}),
            "unknown fields",
        ),
    ],
)
def test_schema_rejects_unknown_out_of_bounds_private_and_image_fields(
    mutate: object, message: str
) -> None:
    value = copy.deepcopy(dataset())
    mutate(value)  # type: ignore[operator]
    with pytest.raises(ValueError, match=message):
        validate_dataset(value)


def test_schema_detects_duplicate_boxes_and_document_split_leakage() -> None:
    value = copy.deepcopy(dataset())
    value["cases"][0]["regions"].append(copy.deepcopy(value["cases"][0]["regions"][0]))  # type: ignore[index]
    value["cases"][0]["regions"][1]["region_id"] = uid(999)  # type: ignore[index]
    with pytest.raises(ValueError, match="duplicate formula region"):
        validate_dataset(value)

    value = copy.deepcopy(dataset())
    value["cases"][1]["document_id"] = value["cases"][0]["document_id"]  # type: ignore[index]
    value["cases"][1]["split"] = "dev"  # type: ignore[index]
    with pytest.raises(ValueError, match="isolated by document_id"):
        validate_dataset(value)


def test_predictions_reject_unknown_pages() -> None:
    value = predictions()
    value["cases"][0]["case_id"] = uid(9999)  # type: ignore[index]
    with pytest.raises(ValueError, match="unknown page"):
        validate_predictions(value, validate_dataset(dataset()))


def test_evaluator_reports_matching_structure_strata_empty_pages_and_fixed_safety() -> None:
    report = evaluate(dataset(), predictions())
    assert report["production_ready"] is False
    assert report["human_confirmation_required"] is True
    assert report["writes_product_data"] is False
    overall = report["metrics"]["overall"]
    assert overall["precision"] == pytest.approx(0.6)
    assert overall["recall"] == pytest.approx(0.75)
    assert overall["f1"] == pytest.approx(2 / 3, abs=1e-6)
    assert overall["false_positives_per_page"] == pytest.approx(0.5)
    assert overall["missed_regions"] == 1
    assert overall["fragmentation"] == 1
    assert overall["merge_errors"] == 1
    assert overall["formula_coverage"] == 1.0
    assert overall["peak_memory_mb"] == 12.0
    assert overall["manual_workload_proxy"]["estimated_operations_saved"] == -1.75
    assert report["metrics"]["negative_pages"]["precision"] == 0.0
    assert report["metrics"]["by_modality"]["text_pdf"]["recall"] == 1.0
    assert report["metrics"]["by_region_kind"]["matrix"]["recall"] == 0.0
    assert report["metrics"]["by_print_style"]["handwritten"]["fragmentation"] == 1
    assert "path" not in str(report).lower()


def test_evaluator_cli_is_directly_runnable() -> None:
    root = Path(__file__).parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "formula_region_detection_evaluate.py"), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "predictions" in completed.stdout


def test_synthetic_connected_component_baseline_is_image_driven_and_rejects_extra_files(
    tmp_path: Path,
) -> None:
    value = dataset()
    value["cases"] = [
        case(10, "synthetic", [region(10, (0.2, 0.2, 0.3, 0.1), "display", "printed")])
    ]
    image = Image.new("RGB", (1000, 1400), "white")
    ImageDraw.Draw(image).rectangle((200, 280, 499, 419), fill="black")
    image.save(tmp_path / f"{uid(10)}.png")
    prediction = run_baseline(value, tmp_path)
    report = evaluate(value, prediction)
    assert report["metrics"]["overall"]["precision"] == 1.0
    assert report["metrics"]["overall"]["recall"] == 1.0
    (tmp_path / "extra.png").write_bytes(b"not an image")
    with pytest.raises(ValueError, match="exactly one"):
        run_baseline(value, tmp_path)


def test_annotation_tool_uses_isolated_storage_and_exports_no_images() -> None:
    root = Path(__file__).parents[1]
    page = (root / "scripts" / "formula_region_annotation_v1.html").read_text(encoding="utf-8")
    assert "ahamark-formula-region-annotation-v1" in page
    assert "ahamark-formula-region-private-images-v1" in page
    assert "viewBox" in page and "crypto.randomUUID" in page
    assert "formula-region-detection-v1.json" in page
    assert "const clean = {" in page
    assert "schema_version: state.schema_version" in page
    assert "cases: state.cases" in page
