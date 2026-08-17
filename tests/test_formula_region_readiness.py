from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from scripts.formula_region_detection_readiness import (
    PILOT_MODALITIES,
    READINESS_REPORT_VERSION,
    READINESS_SCHEMA_VERSION,
    REQUIRED_NEGATIVE_TAGS,
    assess_readiness,
    canonical_predictions_sha256,
    load_json,
)


def uid(number: int) -> str:
    return str(uuid.UUID(int=number))


def _region(number: int) -> dict[str, object]:
    return {
        "region_id": uid(1_000_000 + number),
        "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.08},
        "kind": "display",
        "print_style": "printed",
        "quality_flags": ["none"],
        "annotation_status": "confirmed",
    }


def evidence() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    cases: list[dict[str, object]] = []
    documents: list[dict[str, object]] = []
    predictions: list[dict[str, object]] = []
    case_number = 1
    document_number = 10_000
    proposal_number = 2_000_000
    for modality in PILOT_MODALITIES:
        for _document_offset in range(30):
            document_id = uid(document_number)
            document_number += 1
            documents.append(
                {
                    "document_id": document_id,
                    "sample_origin": "real_deidentified",
                    "deidentified": True,
                    "provenance_attestation_id": uid(3_000_000 + document_number),
                    "license_basis": "institution_permission",
                    "license_or_permission_id": f"permission-{document_number}",
                    "evaluation_use_authorized": True,
                    "local_acquisition_authorized": True,
                }
            )
            for page_offset in range(4):
                contains_formula = page_offset < 3
                case_id = uid(case_number)
                case = {
                    "case_id": case_id,
                    "document_id": document_id,
                    "split": "test",
                    "modality": modality,
                    "page_width": 1000,
                    "page_height": 1400,
                    "contains_formula": contains_formula,
                    "regions": [_region(case_number)] if contains_formula else [],
                    "quality_flags": ["none"],
                    "negative_tags": [] if contains_formula else list(REQUIRED_NEGATIVE_TAGS),
                    "annotation_status": "annotated" if contains_formula else "no_formula",
                    "annotator_decision_version": "decision-v1",
                }
                cases.append(case)
                predictions.append(
                    {
                        "case_id": case_id,
                        "proposals": [
                            {
                                "proposal_id": uid(proposal_number),
                                "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.08},
                                "score": 0.9,
                                "detection_source": "pilot-detector-v1",
                            }
                        ]
                        if contains_formula
                        else [],
                        "inference_ms": 2.0,
                    }
                )
                proposal_number += 1
                case_number += 1
    dataset: dict[str, object] = {
        "schema_version": "formula-region-detection-v1",
        "dataset_id": uid(9_000_000),
        "annotator_decision_version": "decision-v1",
        "cases": cases,
    }
    prediction_set: dict[str, object] = {
        "schema_version": "formula-region-predictions-v1",
        "detector": {"name": "pilot-detector", "version": "1.2.3"},
        "cases": predictions,
    }
    attestation: dict[str, object] = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "dataset_id": dataset["dataset_id"],
        "detector": {
            "name": "pilot-detector",
            "version": "1.2.3",
            "code_license": {"identifier": "Apache-2.0", "verified": True},
            "weights_license": {"identifier": "evaluation-permission-v1", "verified": True},
            "local_acquisition": {
                "authorized": True,
                "attestation_id": uid(9_000_001),
                "method": "preexisting_local_copy",
            },
        },
        "documents": documents,
        "blind_review": {
            "protocol": "blind-double-review-v1",
            "independent_reviewer_count": 2,
            "adjudicated": True,
            "reviewer_identities_excluded": True,
            "annotator_decision_version": "decision-v1",
            "prediction_seal_id": uid(9_000_002),
            "sealed_predictions_sha256": canonical_predictions_sha256(prediction_set),
            "prediction_sealed_at": "2026-08-01T00:00:00Z",
            "labels_unblinded_at": "2026-08-02T00:00:00Z",
        },
    }
    return dataset, prediction_set, attestation


def test_complete_real_blind_authorized_evidence_is_only_eligible_for_disabled_pilot() -> None:
    dataset, predictions, attestation = evidence()

    first = assess_readiness(dataset, predictions, attestation)

    assert first == assess_readiness(dataset, predictions, attestation)
    assert first["schema_version"] == READINESS_REPORT_VERSION
    assert first["detector_identity"] == {"verified_against_private_attestation": True}
    assert first["status"] == "self_attested_evaluation_only"
    assert first["self_attested_evaluation_complete"] is True
    assert first["eligible_for_pilot"] is False
    assert first["blocker_codes"] == ["TRUSTED_ATTESTATION_REQUIRED"]
    assert first["enabled"] is False
    assert first["production_ready"] is False
    assert first["human_confirmation_required"] is True
    assert first["writes_product_data"] is False
    perfect_metrics = {
        "precision": 1.0,
        "recall": 1.0,
        "formula_coverage": 1.0,
        "false_positives_per_page": 0.0,
        "fragmentation_per_ground_truth": 0.0,
        "merge_errors_per_ground_truth": 0.0,
    }
    assert first["metrics"] == {
        "overall": perfect_metrics,
        "by_modality": {modality: perfect_metrics for modality in PILOT_MODALITIES},
        "negative_pages": {"false_positives_per_page": 0.0},
    }
    assert first["counts"]["real_test_documents_by_modality"] == {
        modality: 30 for modality in PILOT_MODALITIES
    }
    assert first["counts"]["real_test_judged_cases_by_modality"] == {
        modality: 120 for modality in PILOT_MODALITIES
    }
    assert set(first) == {
        "schema_version",
        "detector_identity",
        "policy_version",
        "status",
        "self_attested_evaluation_complete",
        "enabled",
        "eligible_for_pilot",
        "production_ready",
        "human_confirmation_required",
        "writes_product_data",
        "counts",
        "metrics",
        "blocker_codes",
    }
    rendered = json.dumps(first, sort_keys=True)
    assert uid(1) not in rendered
    assert uid(10_000) not in rendered
    assert "bbox" not in rendered


@pytest.mark.parametrize(
    ("mutate", "blocker"),
    [
        (
            lambda _dataset, _predictions, attestation: attestation["detector"][
                "code_license"
            ].update({"verified": False}),
            "DETECTOR_CODE_LICENSE_UNVERIFIED",
        ),
        (
            lambda _dataset, _predictions, attestation: attestation["detector"][
                "weights_license"
            ].update({"verified": False}),
            "DETECTOR_WEIGHTS_LICENSE_UNVERIFIED",
        ),
        (
            lambda _dataset, _predictions, attestation: attestation["detector"][
                "local_acquisition"
            ].update({"authorized": False}),
            "DETECTOR_LOCAL_ACQUISITION_UNAUTHORIZED",
        ),
        (
            lambda _dataset, _predictions, attestation: attestation["documents"][0].update(
                {"evaluation_use_authorized": False}
            ),
            "DOCUMENT_EVALUATION_USE_UNAUTHORIZED",
        ),
        (
            lambda _dataset, _predictions, attestation: attestation["blind_review"].update(
                {"independent_reviewer_count": 1}
            ),
            "BLIND_REVIEWER_COUNT_INSUFFICIENT",
        ),
        (
            lambda _dataset, _predictions, attestation: attestation["blind_review"].update(
                {"prediction_sealed_at": "2026-08-03T00:00:00Z"}
            ),
            "PREDICTIONS_NOT_SEALED_BEFORE_UNBLINDING",
        ),
        (
            lambda _dataset, predictions, _attestation: predictions["cases"].pop(),
            "PREDICTION_COVERAGE_INCOMPLETE",
        ),
    ],
)
def test_readiness_blockers_are_aggregate_and_never_enable_product(
    mutate: Any, blocker: str
) -> None:
    dataset, predictions, attestation = evidence()
    mutate(dataset, predictions, attestation)

    report = assess_readiness(dataset, predictions, attestation)

    assert blocker in report["blocker_codes"]
    assert report["eligible_for_pilot"] is False
    assert report["self_attested_evaluation_complete"] is False
    assert report["enabled"] is False
    assert report["production_ready"] is False
    assert report["writes_product_data"] is False


def test_synthetic_volume_does_not_count_as_real_and_missing_hard_negative_tag_blocks() -> None:
    dataset, predictions, attestation = evidence()
    cases = dataset["cases"]
    documents = attestation["documents"]
    text_pdf_ids = {
        case["document_id"]
        for case in cases
        if case["modality"] == "text_pdf"  # type: ignore[index]
    }
    for document in documents:  # type: ignore[union-attr]
        if document["document_id"] in text_pdf_ids:
            document.update(
                {
                    "sample_origin": "synthetic",
                    "license_basis": "synthetic_generated",
                }
            )
    for case in cases:  # type: ignore[union-attr]
        if not case["contains_formula"]:
            case["negative_tags"] = [tag for tag in case["negative_tags"] if tag != "geometry"]

    report = assess_readiness(dataset, predictions, attestation)

    assert "INSUFFICIENT_TEXT_PDF_DOCUMENTS" in report["blocker_codes"]
    assert "INSUFFICIENT_TEXT_PDF_JUDGED_CASES" in report["blocker_codes"]
    assert "INSUFFICIENT_NEGATIVE_GEOMETRY_DOCUMENTS" in report["blocker_codes"]
    assert "PREDICTIONS_INCLUDE_NON_PILOT_CASES" in report["blocker_codes"]


def test_complete_prediction_rows_with_no_proposals_are_quality_blocked() -> None:
    dataset, predictions, attestation = evidence()
    for prediction in predictions["cases"]:  # type: ignore[union-attr]
        prediction["proposals"] = []

    report = assess_readiness(dataset, predictions, attestation)

    assert report["counts"]["prediction_cases"] == 360
    assert "PREDICTION_COVERAGE_INCOMPLETE" not in report["blocker_codes"]
    assert report["metrics"]["overall"]["recall"] == 0.0
    assert report["metrics"]["overall"]["formula_coverage"] == 0.0
    assert "PILOT_RECALL_BELOW_FLOOR" in report["blocker_codes"]
    assert "PILOT_FORMULA_COVERAGE_BELOW_FLOOR" in report["blocker_codes"]
    assert report["eligible_for_pilot"] is False


def test_complete_low_quality_boxes_are_quality_blocked() -> None:
    dataset, predictions, attestation = evidence()
    for prediction in predictions["cases"]:  # type: ignore[union-attr]
        prediction["proposals"] = [
            {
                "proposal_id": uid(7_000_000 + len(prediction["case_id"])),
                "bbox": {"x": 0.7, "y": 0.8, "width": 0.1, "height": 0.05},
                "score": 0.99,
                "detection_source": "pilot-detector-v1",
            }
        ]

    report = assess_readiness(dataset, predictions, attestation)

    assert "PREDICTION_COVERAGE_INCOMPLETE" not in report["blocker_codes"]
    assert report["metrics"]["overall"]["precision"] == 0.0
    assert report["metrics"]["overall"]["recall"] == 0.0
    assert report["metrics"]["negative_pages"]["false_positives_per_page"] == 1.0
    assert "PILOT_PRECISION_BELOW_FLOOR" in report["blocker_codes"]
    assert "PILOT_RECALL_BELOW_FLOOR" in report["blocker_codes"]
    assert "NEGATIVE_PAGES_FALSE_POSITIVES_PER_PAGE_ABOVE_CEILING" in report["blocker_codes"]
    assert report["eligible_for_pilot"] is False


def test_one_bad_modality_is_blocked_even_when_overall_metrics_pass() -> None:
    dataset, predictions, attestation = evidence()
    photo_formula_ids = [
        case["case_id"]
        for case in dataset["cases"]  # type: ignore[union-attr]
        if case["modality"] == "photo" and case["contains_formula"]
    ][:10]
    for prediction in predictions["cases"]:  # type: ignore[union-attr]
        if prediction["case_id"] in photo_formula_ids:
            prediction["proposals"] = []

    report = assess_readiness(dataset, predictions, attestation)

    assert report["metrics"]["overall"]["recall"] >= 0.90
    assert report["metrics"]["by_modality"]["photo"]["recall"] < 0.90
    assert "PILOT_RECALL_BELOW_FLOOR" not in report["blocker_codes"]
    assert "PHOTO_RECALL_BELOW_FLOOR" in report["blocker_codes"]


def test_false_positives_confined_to_negative_pages_use_negative_page_ceiling() -> None:
    dataset, predictions, attestation = evidence()
    cases_by_id = {
        case["case_id"]: case
        for case in dataset["cases"]  # type: ignore[union-attr]
    }
    added_by_modality = {modality: 0 for modality in PILOT_MODALITIES}
    for prediction in predictions["cases"]:  # type: ignore[union-attr]
        case = cases_by_id[prediction["case_id"]]
        modality = case["modality"]
        if not case["contains_formula"] and added_by_modality[modality] < 10:
            prediction["proposals"] = [
                {
                    "proposal_id": uid(7_100_000 + sum(added_by_modality.values())),
                    "bbox": {"x": 0.7, "y": 0.8, "width": 0.1, "height": 0.05},
                    "score": 0.99,
                    "detection_source": "pilot-detector-v1",
                }
            ]
            added_by_modality[modality] += 1

    report = assess_readiness(dataset, predictions, attestation)

    assert report["metrics"]["overall"]["precision"] == 0.90
    assert all(
        report["metrics"]["by_modality"][modality]["precision"] == 0.90
        for modality in PILOT_MODALITIES
    )
    assert report["metrics"]["negative_pages"]["false_positives_per_page"] > 0.10
    assert "PILOT_PRECISION_BELOW_FLOOR" not in report["blocker_codes"]
    assert not any(code.endswith("PRECISION_BELOW_FLOOR") for code in report["blocker_codes"])
    assert "NEGATIVE_PAGES_FALSE_POSITIVES_PER_PAGE_ABOVE_CEILING" in report["blocker_codes"]


def test_predictions_changed_after_sealing_are_blocked_without_exposing_digest() -> None:
    dataset, predictions, attestation = evidence()
    predictions["cases"][0]["inference_ms"] = 2.5  # type: ignore[index]

    report = assess_readiness(dataset, predictions, attestation)

    assert "PREDICTIONS_CHANGED_AFTER_SEAL" in report["blocker_codes"]
    assert report["self_attested_evaluation_complete"] is False
    assert "sha256" not in json.dumps(report).lower()


def test_public_report_never_echoes_private_detector_name_or_version() -> None:
    dataset, predictions, attestation = evidence()
    predictions["detector"] = {"name": "ZhangSan", "version": "SchoolA_Model"}
    attestation["detector"]["name"] = "ZhangSan"  # type: ignore[index]
    attestation["detector"]["version"] = "SchoolA_Model"  # type: ignore[index]
    attestation["blind_review"]["sealed_predictions_sha256"] = (  # type: ignore[index]
        canonical_predictions_sha256(predictions)
    )

    report = assess_readiness(dataset, predictions, attestation)
    rendered = json.dumps(report, sort_keys=True)

    assert report["detector_identity"] == {"verified_against_private_attestation": True}
    assert "ZhangSan" not in rendered
    assert "SchoolA_Model" not in rendered


def test_document_split_leakage_private_provenance_and_unknown_fields_are_rejected() -> None:
    dataset, predictions, attestation = evidence()
    duplicate = copy.deepcopy(dataset["cases"][0])  # type: ignore[index]
    duplicate["case_id"] = uid(8_000_000)
    duplicate["split"] = "dev"
    dataset["cases"].append(duplicate)  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="isolated by document_id"):
        assess_readiness(dataset, predictions, attestation)

    dataset, predictions, attestation = evidence()
    attestation["documents"][0]["source_path"] = "C:/private/student.png"  # type: ignore[index]
    with pytest.raises(ValueError, match="private field"):
        assess_readiness(dataset, predictions, attestation)

    dataset, predictions, attestation = evidence()
    attestation["detector"]["download_url"] = "https://example.invalid/model"  # type: ignore[index]
    with pytest.raises(ValueError, match="private field|fields are invalid"):
        assess_readiness(dataset, predictions, attestation)


@pytest.mark.parametrize(
    ("target", "value", "message"),
    [
        ("license", "owner@example.edu", "email value"),
        ("source", "+86 13812345678", "phone value"),
        ("version", "姓名：张三", "labeled identity value"),
        ("version", "学号: 2026123456", "labeled identity value"),
    ],
)
def test_value_level_pii_is_rejected(target: str, value: str, message: str) -> None:
    dataset, predictions, attestation = evidence()
    if target == "license":
        attestation["documents"][0]["license_or_permission_id"] = value  # type: ignore[index]
    elif target == "source":
        predictions["cases"][0]["proposals"][0]["detection_source"] = value  # type: ignore[index]
    else:
        dataset["annotator_decision_version"] = value
        for case in dataset["cases"]:  # type: ignore[union-attr]
            case["annotator_decision_version"] = value
        attestation["blind_review"]["annotator_decision_version"] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=message):
        assess_readiness(dataset, predictions, attestation)


def test_validator_never_reads_images_or_uses_network(monkeypatch: pytest.MonkeyPatch) -> None:
    dataset, predictions, attestation = evidence()

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("images must not be read")),
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )

    report = assess_readiness(dataset, predictions, attestation)

    assert report["self_attested_evaluation_complete"] is True
    assert report["eligible_for_pilot"] is False
    assert report["writes_product_data"] is False


def test_validator_has_no_product_database_or_formula_region_dependency() -> None:
    source = (
        Path(__file__).parents[1] / "scripts/formula_region_detection_readiness.py"
    ).read_text(encoding="utf-8")

    assert "sqlalchemy" not in source
    assert "app.db" not in source
    assert "app.models" not in source
    assert "FormulaRegion" not in source
    assert "requests" not in source
    assert "httpx" not in source


def test_strict_json_loader_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_json(duplicate)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"score":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        load_json(nonfinite)


def test_cli_runs_with_invalid_database_and_emits_only_offline_report(tmp_path: Path) -> None:
    dataset, predictions, attestation = evidence()
    dataset_path = tmp_path / "dataset.json"
    predictions_path = tmp_path / "predictions.json"
    attestation_path = tmp_path / "attestation.json"
    output_path = tmp_path / "report.json"
    for path, value in (
        (dataset_path, dataset),
        (predictions_path, predictions),
        (attestation_path, attestation),
    ):
        path.write_text(json.dumps(value), encoding="utf-8")
    root = Path(__file__).parents[1]
    environment = dict(os.environ)
    environment["DATABASE_URL"] = "not-a-database://must-not-be-used"

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/formula_region_detection_readiness.py"),
            str(dataset_path),
            str(predictions_path),
            str(attestation_path),
            "--output",
            str(output_path),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["self_attested_evaluation_complete"] is True
    assert report["eligible_for_pilot"] is False
    assert report["enabled"] is False
    assert report["writes_product_data"] is False
    assert json.loads(completed.stdout) == report
