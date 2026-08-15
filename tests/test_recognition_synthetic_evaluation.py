from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from app.recognition.text_integrity import inspect_text_integrity
from PIL import Image, ImageDraw

from scripts.recognition_synthetic_evaluate import (
    CONTENT_TAGS,
    MODALITIES,
    _lcs_length,
    _math_tokens,
    _region_counts,
    evaluate,
    validate_dataset,
)

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "recognition_synthetic_v1.json"


def dataset() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_strict_sanitized_complete_and_gate_aligned() -> None:
    value = validate_dataset(dataset())
    cases = value["cases"]
    assert {case["modality"] for case in cases} == MODALITIES
    assert {
        tag
        for case in cases
        for tag in case["content_tags"]  # type: ignore[union-attr]
    } == CONTENT_TAGS
    assert {"low_resolution", "slight_rotation", "perspective"} <= {
        tag
        for case in cases
        for tag in case["degradation_tags"]  # type: ignore[union-attr]
    }
    for case in cases:  # type: ignore[union-attr]
        rejected = bool(
            inspect_text_integrity(case["observed_text"], field_path="synthetic.observed_text")
        )
        assert rejected is case["integrity_rejected"]
        assert rejected is case["expect_integrity_rejection"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"unknown": True}), "unknown fields"),
        (
            lambda value: value["cases"][0].update({"source_path": "private"}),
            "private field",
        ),
        (
            lambda value: value["cases"][0].update({"image": "data:image/png;base64,AA"}),
            "unknown fields",
        ),
        (
            lambda value: value["cases"][0]["expected_regions"][0]["bbox"].update({"width": 1.0}),
            "normalized page bounds",
        ),
        (
            lambda value: value["cases"][0].update({"observed_text": "contact user@example.org"}),
            "private value",
        ),
        (
            lambda value: value["cases"][0].update({"observed_text": "电话：13812345678"}),
            "private value",
        ),
        (
            lambda value: value["cases"][0].update({"observed_text": "学号：20260001"}),
            "private value",
        ),
        (
            lambda value: value["cases"][0].update({"observed_text": "姓名：合成甲"}),
            "private value",
        ),
    ],
)
def test_fixture_rejects_unknown_private_image_and_invalid_box_fields(
    mutation: object, message: str
) -> None:
    value = copy.deepcopy(dataset())
    mutation(value)  # type: ignore[operator]
    with pytest.raises(ValueError, match=message):
        validate_dataset(value)


def test_evaluator_is_deterministic_reports_all_metrics_and_never_claims_real_accuracy() -> None:
    first = evaluate(dataset())
    second = evaluate(dataset())
    assert first == second
    assert first["synthetic_only"] is True
    assert first["real_accuracy"] is False
    assert first["production_ready"] is False
    assert first["writes_product_data"] is False
    assert set(first["metrics"]["by_modality"]) == MODALITIES
    assert {"clean", "low_resolution", "slight_rotation", "perspective"} == set(
        first["metrics"]["by_degradation"]
    )

    metrics = first["metrics"]["overall"]
    assert set(metrics) == {
        "page_count",
        "character_completeness",
        "math_symbol_retention",
        "question_number_accuracy",
        "question_number_details",
        "region_precision",
        "region_recall",
        "region_details",
        "false_suggestions_per_page",
        "false_regions_per_page",
        "source_coverage",
        "source_details",
        "manual_required_ratio",
        "manual_required_details",
        "integrity_gate_rejection_count",
        "integrity_gate_details",
    }
    assert 0 < metrics["character_completeness"] < 1
    assert 0 < metrics["math_symbol_retention"] < 1
    assert metrics["question_number_accuracy"] == 1.0
    assert metrics["question_number_details"]["judged_case_count"] == 14
    assert metrics["region_precision"] == pytest.approx(9 / 11, abs=1e-6)
    assert metrics["region_recall"] == 0.9
    assert metrics["false_suggestions_per_page"] == 0.125
    assert metrics["false_regions_per_page"] == 0.125
    assert 0 < metrics["source_coverage"] < 1
    assert metrics["manual_required_ratio"] == 0.8
    assert metrics["integrity_gate_rejection_count"] == 2
    assert metrics["integrity_gate_details"] == {
        "expected_rejection_count": 2,
        "correct_rejection_count": 2,
        "missed_rejection_count": 0,
        "false_rejection_count": 0,
    }
    rendered = json.dumps(first, ensure_ascii=False)
    assert "求函数" not in rendered
    assert "source_path" not in rendered


def test_question_accuracy_excludes_empty_gold_pages_from_its_denominator() -> None:
    value = dataset()
    value["cases"][0]["observed_question_lines"] = ["99. wrong anchor"]  # type: ignore[index]
    metrics = evaluate(value)["metrics"]["overall"]
    assert metrics["question_number_details"]["judged_case_count"] == 14
    assert metrics["question_number_details"]["exact_case_count"] == 13
    assert metrics["question_number_accuracy"] == pytest.approx(13 / 14, abs=1e-6)


def test_question_details_count_false_positive_anchors_on_empty_gold_pages() -> None:
    value = dataset()
    baseline = evaluate(value)["metrics"]["overall"]
    empty_gold = next(
        case
        for case in value["cases"]
        if not case["expected_question_numbers"]  # type: ignore[index]
    )
    empty_gold["observed_question_lines"] = ["99. synthetic false anchor"]  # type: ignore[index]

    metrics = evaluate(value)["metrics"]["overall"]

    assert metrics["question_number_accuracy"] == baseline["question_number_accuracy"]
    assert metrics["question_number_details"]["extra_anchor_count"] == (
        baseline["question_number_details"]["extra_anchor_count"] + 1
    )


def test_latin1_unicode_superscript_loss_reduces_math_retention() -> None:
    expected = _math_tokens("x²+x³+x¹")
    observed = _math_tokens("x2+x3+x1")
    assert expected == ["²", "+", "³", "+", "¹"]
    assert observed == ["+", "+"]
    assert _lcs_length(expected, observed) / len(expected) == pytest.approx(0.4)


def test_region_matching_uses_maximum_cardinality_not_edge_greedy() -> None:
    case = {
        "expected_regions": [
            {
                "region_id": "synthetic-region-a",
                "bbox": {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.4},
            },
            {
                "region_id": "synthetic-region-b",
                "bbox": {"x": 0.35, "y": 0.2, "width": 0.4, "height": 0.4},
            },
        ],
        "proposed_regions": [
            {
                "proposal_id": "synthetic-proposal-shared",
                "bbox": {"x": 0.27, "y": 0.2, "width": 0.4, "height": 0.4},
            },
            {
                "proposal_id": "synthetic-proposal-a-only",
                "bbox": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.4},
            },
        ],
    }
    assert _region_counts([case]) == (2, 0, 0)


def test_runtime_only_degradation_images_need_no_binary_fixture(tmp_path: Path) -> None:
    base = Image.new("L", (320, 180), "white")
    draw = ImageDraw.Draw(base)
    draw.rectangle((24, 35, 290, 48), fill="black")
    draw.rectangle((80, 85, 230, 105), fill="black")

    low_resolution = base.resize((80, 45)).resize(base.size)
    slight_rotation = base.rotate(2.0, expand=False, fillcolor="white")
    perspective = base.transform(
        base.size,
        Image.Transform.PERSPECTIVE,
        (1.0, 0.08, -7.0, 0.03, 1.0, -5.0, 0.0002, 0.0003),
        fillcolor="white",
    )
    for name, image in (
        ("low-resolution.png", low_resolution),
        ("slight-rotation.png", slight_rotation),
        ("perspective.png", perspective),
    ):
        target = tmp_path / name
        image.save(target)
        assert target.stat().st_size > 0
        assert image.size == base.size


def test_evaluator_cli_is_directly_runnable() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "recognition_synthetic_evaluate.py"),
            str(FIXTURE),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["synthetic_only"] is True
    assert report["real_accuracy"] is False
    assert report["production_ready"] is False
