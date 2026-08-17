from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from PIL import Image

import scripts.recognition_private_benchmark_evaluate as benchmark
from scripts.recognition_private_benchmark_evaluate import (
    ATTESTATION_VERSION,
    DEGRADATIONS,
    FORMULA_GOLD_VERSION,
    GOLD_VERSION,
    MODALITIES,
    PREDICTION_VERSION,
    canonical_predictions_sha256,
    evaluate,
    load_json,
    validate_gold,
)


def uid(number: int) -> str:
    return str(uuid.UUID(int=number))


def evidence(root: Path) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    cases: list[dict[str, object]] = []
    predictions: list[dict[str, object]] = []
    documents: list[dict[str, object]] = []
    root.mkdir()
    degradations = sorted(DEGRADATIONS)
    for index in range(8):
        case_id, document_id = uid(100 + index), uid(200 + index)
        modality = MODALITIES[index % 4]
        has_region = index % 2 == 0
        text = "1. 求 α²+x=2" if index < 4 else "1. Find x plus y"
        cases.append(
            {
                "case_id": case_id,
                "document_id": document_id,
                "split": "test",
                "modality": modality,
                "image_file": f"{case_id}.png",
                "page_width": 64,
                "page_height": 48,
                "degradation_tags": [degradations[index]],
                "content_tags": [
                    "math",
                    "question_number",
                    "chinese" if index < 4 else "english",
                ]
                + (["negative"] if not has_region else []),
                "annotation_status": "annotated",
                "expected_text": text,
                "expected_question_numbers": ["1"],
                "expected_regions": [
                    {
                        "region_id": uid(300 + index),
                        "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1},
                    }
                ]
                if has_region
                else [],
                "expect_integrity_rejection": index == 0,
                "annotator_decision_version": "decision-v1",
            }
        )
        predictions.append(
            {
                "case_id": case_id,
                "observed_text": text,
                "observed_question_numbers": ["1"],
                "proposed_regions": [
                    {
                        "proposal_id": uid(400 + index),
                        "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1},
                    }
                ]
                if has_region
                else [],
                "inference_ms": float(index + 1),
                "peak_memory_mb": float(100 + index),
                "suggestion_count": 2,
                "manual_required_count": 1,
                "integrity_rejected": index == 0,
            }
        )
        documents.append(
            {
                "document_id": document_id,
                "sample_origin": "real_deidentified",
                "deidentified": True,
                "evaluation_use_authorized": True,
                "local_acquisition_authorized": True,
            }
        )
        Image.new("L", (64, 48), "white").save(root / f"{case_id}.png")
    gold: dict[str, object] = {
        "schema_version": GOLD_VERSION,
        "dataset_id": uid(1),
        "annotator_decision_version": "decision-v1",
        "cases": cases,
    }
    prediction_set: dict[str, object] = {
        "schema_version": PREDICTION_VERSION,
        "detector": {"name": "private-provider", "version": "school-model"},
        "cases": predictions,
    }
    attestation: dict[str, object] = {
        "schema_version": ATTESTATION_VERSION,
        "dataset_id": uid(1),
        "documents": documents,
        "blind_review": {
            "independent_reviewer_count": 2,
            "adjudicated": True,
            "reviewer_identities_excluded": True,
            "sealed_predictions_sha256": canonical_predictions_sha256(prediction_set),
            "prediction_sealed_at": "2026-08-01T00:00:00Z",
            "labels_unblinded_at": "2026-08-02T00:00:00Z",
        },
    }
    return gold, prediction_set, attestation


def test_formula_gold_v2_is_strict_and_keeps_text_metrics_separate(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    gold["schema_version"] = FORMULA_GOLD_VERSION
    for case in gold["cases"]:  # type: ignore[index]
        case["formula_spans"] = []
    gold["cases"][0]["formula_spans"] = [  # type: ignore[index]
        {
            "formula_id": uid(900),
            "bbox": {"x": 0.2, "y": 0.25, "width": 0.4, "height": 0.15},
            "latex": r"\frac{\sqrt{xy+1}-1}{x+y}",
            "linear_text": "[√(xy+1)−1]/(x+y)",
            "review_status": "reviewed",
        }
    ]

    report = evaluate(gold, predictions, attestation, tmp_path / "images")
    assert report["formula_structure_evaluated"] is False
    assert report["gold_formula_span_count"] == 1

    invalid = copy.deepcopy(gold)
    invalid["cases"][0]["formula_spans"][0]["latex"] = ""  # type: ignore[index]
    with pytest.raises(ValueError, match="reviewed formula text must not be empty"):
        validate_gold(invalid)


def test_perfect_private_evidence_reports_only_aggregate_metrics(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")

    report = evaluate(gold, predictions, attestation, tmp_path / "images")

    assert report["status"] == "self_attested_evaluation_only"
    assert report["eligible_for_pilot"] is False
    assert report["production_ready"] is False
    assert report["writes_product_data"] is False
    assert report["detector_identity"] == {"trusted_identity_verified": False}
    assert report["blocker_codes"] == ["TRUSTED_ATTESTATION_REQUIRED"]
    assert set(report["metrics"]["by_modality"]) == set(MODALITIES)
    assert all(row["suppressed"] is True for row in report["metrics"]["by_degradation"].values())
    overall = report["metrics"]["overall"]
    assert overall["cer"] == 0.0
    assert overall["character_accuracy"] == 1.0
    assert overall["english_wer"] == 0.0
    assert overall["math"]["f1"] == 1.0
    assert overall["question_numbers"]["exact_page_ratio"] == 1.0
    assert overall["question_numbers"]["anchor_precision"] == 1.0
    assert overall["question_numbers"]["anchor_recall"] == 1.0
    assert overall["regions"]["precision"] == 1.0
    assert overall["regions"]["recall"] == 1.0
    assert overall["performance"] == {
        "latency_ms_mean": 4.5,
        "latency_ms_p50": 4.0,
        "latency_ms_p95": 8.0,
        "peak_memory_mb": 107.0,
    }
    assert overall["manual_required_ratio"] == 0.5
    assert overall["integrity"]["true_positive"] == 1
    assert overall["integrity"]["true_negative"] == 7
    assert overall["integrity"]["precision"] == 1.0
    assert overall["integrity"]["recall"] == 1.0
    rendered = json.dumps(report, ensure_ascii=False)
    assert "private-provider" not in rendered
    assert "school-model" not in rendered
    assert uid(100) not in rendered
    assert "expected_text" not in rendered
    assert "bbox" not in rendered
    assert "sha256" not in rendered.lower()


def test_insertions_raise_cer_and_math_insertions_lower_precision(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    predictions["cases"][0]["observed_text"] += "+β"  # type: ignore[index]
    attestation["blind_review"]["sealed_predictions_sha256"] = canonical_predictions_sha256(  # type: ignore[index]
        predictions
    )

    metrics = evaluate(gold, predictions, attestation, tmp_path / "images")["metrics"]["overall"]

    assert metrics["cer"] > 0
    assert metrics["character_accuracy"] < 1
    assert metrics["math"]["precision"] < 1
    assert metrics["math"]["token_edit_rate"] > 0


def test_math_operand_replacement_is_scored_but_ordinary_english_words_are_not(
    tmp_path: Path,
) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    predictions["cases"][0]["observed_text"] = "1. 求 β²+x=2"  # type: ignore[index]
    attestation["blind_review"]["sealed_predictions_sha256"] = canonical_predictions_sha256(  # type: ignore[index]
        predictions
    )

    math_metrics = evaluate(gold, predictions, attestation, tmp_path / "images")["metrics"][
        "overall"
    ]["math"]

    assert math_metrics["recall"] < 1.0
    assert math_metrics["token_edit_rate"] > 0
    assert math_metrics["support"]["gold_token_count"] < sum(
        len(case["expected_text"])
        for case in gold["cases"]  # type: ignore[index]
    )


def test_overall_is_suppressed_below_two_independent_documents(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    shared_document_id = gold["cases"][0]["document_id"]  # type: ignore[index]
    for case in gold["cases"]:  # type: ignore[union-attr]
        case["document_id"] = shared_document_id
    attestation["documents"] = [attestation["documents"][0]]  # type: ignore[index]

    report = evaluate(gold, predictions, attestation, tmp_path / "images")

    assert report["metrics"]["overall"] == {
        "document_count": 1,
        "page_count": 8,
        "suppressed": True,
    }


def test_zero_positive_support_uses_null_ratios_and_explicit_counts(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    for case in gold["cases"]:  # type: ignore[union-attr]
        case["expected_text"] = "普通题干"
        case["expected_question_numbers"] = []
        case["expected_regions"] = []
        case["expect_integrity_rejection"] = False
    for prediction in predictions["cases"]:  # type: ignore[union-attr]
        prediction["observed_text"] = "普通题干"
        prediction["observed_question_numbers"] = []
        prediction["proposed_regions"] = []
        prediction["integrity_rejected"] = False
    attestation["blind_review"]["sealed_predictions_sha256"] = canonical_predictions_sha256(  # type: ignore[index]
        predictions
    )

    metrics = evaluate(gold, predictions, attestation, tmp_path / "images")["metrics"]["overall"]

    assert metrics["math"]["precision"] is None
    assert metrics["math"]["recall"] is None
    assert metrics["math"]["f1"] is None
    assert metrics["math"]["support"] == {
        "gold_token_count": 0,
        "observed_token_count": 0,
        "matched_token_count": 0,
    }
    assert metrics["question_numbers"]["exact_page_ratio"] is None
    assert metrics["question_numbers"]["anchor_precision"] is None
    assert metrics["question_numbers"]["anchor_recall"] is None
    assert metrics["regions"]["precision"] is None
    assert metrics["regions"]["recall"] is None
    assert metrics["integrity"]["precision"] is None
    assert metrics["integrity"]["recall"] is None


def test_empty_gold_negative_corpus_counts_insertions_in_every_modality(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    for case in gold["cases"]:  # type: ignore[union-attr]
        case["expected_text"] = ""
        case["expected_regions"] = []
        if "negative" not in case["content_tags"]:
            case["content_tags"].append("negative")
    for prediction in predictions["cases"]:  # type: ignore[union-attr]
        prediction["observed_text"] = "insert beta β"
        prediction["proposed_regions"] = []
    attestation["blind_review"]["sealed_predictions_sha256"] = canonical_predictions_sha256(  # type: ignore[index]
        predictions
    )

    report = evaluate(gold, predictions, attestation, tmp_path / "images")

    for metrics in [
        report["metrics"]["overall"],
        *report["metrics"]["by_modality"].values(),
    ]:
        assert metrics["cer"] == 1.0
        assert metrics["character_accuracy"] == 0.0
        assert metrics["english_wer"] == 1.0
        assert metrics["math"]["token_edit_rate"] == 1.0


def test_question_region_negative_and_integrity_errors_are_counted(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    predictions["cases"][0]["observed_question_numbers"] = ["9"]  # type: ignore[index]
    predictions["cases"][0]["proposed_regions"] = []  # type: ignore[index]
    predictions["cases"][1]["proposed_regions"] = [  # type: ignore[index]
        {"proposal_id": uid(900), "bbox": {"x": 0.7, "y": 0.7, "width": 0.1, "height": 0.1}}
    ]
    predictions["cases"][0]["integrity_rejected"] = False  # type: ignore[index]
    predictions["cases"][1]["integrity_rejected"] = True  # type: ignore[index]
    attestation["blind_review"]["sealed_predictions_sha256"] = canonical_predictions_sha256(  # type: ignore[index]
        predictions
    )

    metrics = evaluate(gold, predictions, attestation, tmp_path / "images")["metrics"]["overall"]

    assert metrics["question_numbers"]["anchor_precision"] < 1
    assert metrics["regions"]["recall"] < 1
    assert metrics["regions"]["false_positives_per_negative_page"] > 0
    assert metrics["integrity"]["false_positive"] == 1
    assert metrics["integrity"]["false_negative"] == 1


def test_document_split_prediction_coverage_and_seal_are_enforced(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    duplicate = copy.deepcopy(gold["cases"][0])  # type: ignore[index]
    duplicate["case_id"] = uid(999)
    duplicate["image_file"] = f"{uid(999)}.png"
    duplicate["split"] = "dev"
    gold["cases"].append(duplicate)  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="isolated by document_id"):
        evaluate(gold, predictions, attestation, tmp_path / "images")

    gold, predictions, attestation = evidence(tmp_path / "images-2")
    predictions["cases"].pop()  # type: ignore[union-attr]
    attestation["blind_review"]["sealed_predictions_sha256"] = canonical_predictions_sha256(  # type: ignore[index]
        predictions
    )
    with pytest.raises(ValueError, match="exactly cover"):
        evaluate(gold, predictions, attestation, tmp_path / "images-2")

    gold, predictions, attestation = evidence(tmp_path / "images-3")
    predictions["cases"][0]["inference_ms"] = 99.0  # type: ignore[index]
    with pytest.raises(ValueError, match="changed after sealing"):
        evaluate(gold, predictions, attestation, tmp_path / "images-3")


@pytest.mark.parametrize(
    "private_value", ["owner@example.edu", "+86 13812345678", "姓名：张三", "学号: 20260001"]
)
def test_value_level_private_data_is_rejected(tmp_path: Path, private_value: str) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    predictions["cases"][0]["observed_text"] = private_value  # type: ignore[index]
    with pytest.raises(ValueError, match="private value"):
        evaluate(gold, predictions, attestation, tmp_path / "images")


def test_image_inventory_rejects_extra_corrupt_and_wrong_dimensions(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    Image.new("L", (1, 1)).save(tmp_path / "images" / "extra.png")
    with pytest.raises(ValueError, match="inventory"):
        evaluate(gold, predictions, attestation, tmp_path / "images")

    gold, predictions, attestation = evidence(tmp_path / "images-2")
    image = tmp_path / "images-2" / str(gold["cases"][0]["image_file"])  # type: ignore[index]
    image.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="image is invalid"):
        evaluate(gold, predictions, attestation, tmp_path / "images-2")

    gold, predictions, attestation = evidence(tmp_path / "images-3")
    image = tmp_path / "images-3" / str(gold["cases"][0]["image_file"])  # type: ignore[index]
    Image.new("L", (10, 10)).save(image)
    with pytest.raises(ValueError, match="dimensions"):
        evaluate(gold, predictions, attestation, tmp_path / "images-3")


def test_image_byte_and_pixel_limits_precede_expensive_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    monkeypatch.setattr(benchmark, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(ValueError, match="byte limit"):
        evaluate(gold, predictions, attestation, tmp_path / "images")

    gold, predictions, attestation = evidence(tmp_path / "images-2")
    monkeypatch.setattr(benchmark, "MAX_IMAGE_BYTES", 20 * 1024 * 1024)
    monkeypatch.setattr(benchmark, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="pixel limit"):
        evaluate(gold, predictions, attestation, tmp_path / "images-2")


def test_strict_loader_rejects_duplicates_and_nonfinite(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_json(duplicate)
    nonfinite = tmp_path / "nan.json"
    nonfinite.write_text('{"a":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        load_json(nonfinite)


def test_cli_is_offline_and_emits_same_aggregate_report(tmp_path: Path) -> None:
    gold, predictions, attestation = evidence(tmp_path / "images")
    paths = [tmp_path / name for name in ("gold.json", "predictions.json", "attestation.json")]
    for path, value in zip(paths, (gold, predictions, attestation), strict=True):
        path.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "report.json"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = "not-a-database://unused"
    environment["HTTPS_PROXY"] = "http://127.0.0.1:1"
    script = Path(__file__).parents[1] / "scripts" / "recognition_private_benchmark_evaluate.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            *map(str, paths),
            "--image-root",
            str(tmp_path / "images"),
            "--output",
            str(output),
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == json.loads(output.read_text(encoding="utf-8"))
    assert json.loads(completed.stdout)["writes_product_data"] is False


def test_evaluator_has_no_database_network_or_model_runtime_dependency() -> None:
    source = (
        Path(__file__).parents[1] / "scripts" / "recognition_private_benchmark_evaluate.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "sqlalchemy",
        "app.db",
        "app.models",
        "requests",
        "httpx",
        "torch",
        "transformers",
    ):
        assert forbidden not in source
