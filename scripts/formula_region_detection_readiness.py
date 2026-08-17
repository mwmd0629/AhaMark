"""Offline pilot-readiness gate for formula-region detection evidence.

This module reads JSON metadata only. It never opens images, downloads assets, imports the
application database, or changes product state. Passing the gate means only that a detector is
eligible for a controlled pilot; it never enables a runtime feature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, cast

# ruff: noqa: E402
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.formula_region_detection_evaluate import (
    evaluate,
    validate_dataset,
    validate_predictions,
)

READINESS_SCHEMA_VERSION = "formula-region-readiness-v1"
READINESS_REPORT_VERSION = "formula-region-readiness-report-v1"
PILOT_MODALITIES = ("text_pdf", "scan", "photo")

MIN_DOCUMENTS_PER_MODALITY = 30
MIN_JUDGED_CASES_PER_MODALITY = 100
MIN_FORMULA_CASES_PER_MODALITY = 50
MIN_NEGATIVE_CASES_PER_MODALITY = 20
MIN_NEGATIVE_DOCUMENTS_PER_TAG = 10
MIN_PRECISION = 0.90
MIN_RECALL = 0.90
MIN_FORMULA_COVERAGE = 0.90
MAX_FALSE_POSITIVES_PER_PAGE = 0.10
MAX_FRAGMENTATION_PER_GROUND_TRUTH = 0.05
MAX_MERGE_ERRORS_PER_GROUND_TRUTH = 0.05

REQUIRED_NEGATIVE_TAGS = (
    "body_text",
    "table",
    "geometry",
    "separator",
    "ruled_paper",
    "underline",
    "header_footer",
    "numeric_label",
    "chinese_punctuation",
    "table_border",
    "overwritten_area",
    "faint_or_blurred",
)
LICENSE_BASES = {
    "public_domain",
    "open_license",
    "institution_permission",
    "data_processing_agreement",
    "synthetic_generated",
}
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
PRIVATE_KEY = re.compile(
    r"path|file.?name|student|teacher|person.?name|annotator.?name|reviewer.?name|"
    r"class.?id|assignment.?id|database.?id|source.?hash|pdf.?hash|original.?hash|"
    r"checksum|email|phone|address|url|image|page.?text",
    re.I,
)
ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|file://|^/)")
EMAIL_VALUE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"
)
MAINLAND_PHONE_VALUE = re.compile(r"(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)")
LABELED_IDENTITY_VALUE = re.compile(
    r"(?:学生姓名|姓名|学号|学生号|student[ _-]?(?:name|id))\s*[:：=]\s*\S+",
    re.I,
)

Json = dict[str, Any]


def _object(value: object, label: str) -> Json:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(Json, value)


def _exact_keys(value: Json, required: set[str], label: str) -> None:
    if set(value) != required:
        raise ValueError(f"{label} fields are invalid")


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _safe_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must be a sanitized identifier")
    return value


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp") from exc
    return parsed


def _privacy(value: object, label: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if PRIVATE_KEY.search(str(key)):
                raise ValueError(f"private field forbidden at {label}.{key}")
            _privacy(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _privacy(item, f"{label}[{index}]")
    elif isinstance(value, str):
        if ABSOLUTE_PATH.search(value):
            raise ValueError(f"absolute path forbidden at {label}")
        if EMAIL_VALUE.search(value):
            raise ValueError(f"email value forbidden at {label}")
        if MAINLAND_PHONE_VALUE.search(value):
            raise ValueError(f"phone value forbidden at {label}")
        if LABELED_IDENTITY_VALUE.search(value):
            raise ValueError(f"labeled identity value forbidden at {label}")


def validate_attestation(raw: object, dataset: Json, predictions: Json) -> Json:
    _privacy(raw)
    value = _object(raw, "attestation")
    _exact_keys(
        value,
        {
            "schema_version",
            "dataset_id",
            "detector",
            "documents",
            "blind_review",
        },
        "attestation",
    )
    if value["schema_version"] != READINESS_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {READINESS_SCHEMA_VERSION}")
    if _uuid(value["dataset_id"], "dataset_id") != dataset["dataset_id"]:
        raise ValueError("attestation dataset_id does not match dataset")

    detector = _object(value["detector"], "detector")
    _exact_keys(
        detector,
        {
            "name",
            "version",
            "code_license",
            "weights_license",
            "local_acquisition",
        },
        "detector",
    )
    prediction_detector = _object(predictions["detector"], "predictions.detector")
    for key in ("name", "version"):
        if _safe_identifier(detector[key], f"detector.{key}") != prediction_detector[key]:
            raise ValueError("attested detector does not match predictions")
    for key in ("code_license", "weights_license"):
        license_item = _object(detector[key], f"detector.{key}")
        _exact_keys(license_item, {"identifier", "verified"}, f"detector.{key}")
        _safe_identifier(license_item["identifier"], f"detector.{key}.identifier")
        if not isinstance(license_item["verified"], bool):
            raise ValueError(f"detector.{key}.verified must be boolean")
    acquisition = _object(detector["local_acquisition"], "detector.local_acquisition")
    _exact_keys(
        acquisition,
        {"authorized", "attestation_id", "method"},
        "detector.local_acquisition",
    )
    if not isinstance(acquisition["authorized"], bool):
        raise ValueError("detector.local_acquisition.authorized must be boolean")
    _uuid(acquisition["attestation_id"], "detector.local_acquisition.attestation_id")
    if acquisition["method"] not in {"preexisting_local_copy", "locally_built_weights"}:
        raise ValueError("detector.local_acquisition.method is invalid")

    documents = value["documents"]
    if not isinstance(documents, list):
        raise ValueError("documents must be a list")
    document_ids: set[str] = set()
    for index, raw_document in enumerate(documents):
        label = f"documents[{index}]"
        document = _object(raw_document, label)
        _exact_keys(
            document,
            {
                "document_id",
                "sample_origin",
                "deidentified",
                "provenance_attestation_id",
                "license_basis",
                "license_or_permission_id",
                "evaluation_use_authorized",
                "local_acquisition_authorized",
            },
            label,
        )
        document_id = _uuid(document["document_id"], f"{label}.document_id")
        if document_id in document_ids:
            raise ValueError("document attestations must be unique")
        document_ids.add(document_id)
        if document["sample_origin"] not in {"real_deidentified", "synthetic"}:
            raise ValueError(f"{label}.sample_origin is invalid")
        if not isinstance(document["deidentified"], bool):
            raise ValueError(f"{label}.deidentified must be boolean")
        _uuid(document["provenance_attestation_id"], f"{label}.provenance_attestation_id")
        if document["license_basis"] not in LICENSE_BASES:
            raise ValueError(f"{label}.license_basis is invalid")
        _safe_identifier(document["license_or_permission_id"], f"{label}.license_or_permission_id")
        for field in ("evaluation_use_authorized", "local_acquisition_authorized"):
            if not isinstance(document[field], bool):
                raise ValueError(f"{label}.{field} must be boolean")
    dataset_document_ids = {str(case["document_id"]) for case in cast(list[Json], dataset["cases"])}
    if document_ids != dataset_document_ids:
        raise ValueError("documents must exactly attest every dataset document_id")

    blind = _object(value["blind_review"], "blind_review")
    _exact_keys(
        blind,
        {
            "protocol",
            "independent_reviewer_count",
            "adjudicated",
            "reviewer_identities_excluded",
            "annotator_decision_version",
            "prediction_seal_id",
            "sealed_predictions_sha256",
            "prediction_sealed_at",
            "labels_unblinded_at",
        },
        "blind_review",
    )
    if blind["protocol"] != "blind-double-review-v1":
        raise ValueError("blind_review.protocol is invalid")
    reviewer_count = blind["independent_reviewer_count"]
    if isinstance(reviewer_count, bool) or not isinstance(reviewer_count, int):
        raise ValueError("blind_review.independent_reviewer_count must be an integer")
    for field in ("adjudicated", "reviewer_identities_excluded"):
        if not isinstance(blind[field], bool):
            raise ValueError(f"blind_review.{field} must be boolean")
    if blind["annotator_decision_version"] != dataset["annotator_decision_version"]:
        raise ValueError("blind review decision version does not match dataset")
    _uuid(blind["prediction_seal_id"], "blind_review.prediction_seal_id")
    digest = blind["sealed_predictions_sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("blind_review.sealed_predictions_sha256 must be lowercase SHA-256")
    _utc(blind["prediction_sealed_at"], "blind_review.prediction_sealed_at")
    _utc(blind["labels_unblinded_at"], "blind_review.labels_unblinded_at")
    return value


def _real_test_cases(dataset: Json, attestations: dict[str, Json]) -> list[Json]:
    return [
        case
        for case in cast(list[Json], dataset["cases"])
        if case["split"] == "test"
        and case["modality"] in PILOT_MODALITIES
        and case["annotation_status"] != "unjudgeable"
        and attestations[str(case["document_id"])]["sample_origin"] == "real_deidentified"
    ]


def canonical_predictions_sha256(predictions: object) -> str:
    canonical = json.dumps(
        predictions,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _quality_summary(raw: Json) -> Json:
    ground_truth_count = int(raw["ground_truth_count"])
    return {
        "precision": raw["precision"],
        "recall": raw["recall"],
        "formula_coverage": raw["formula_coverage"],
        "false_positives_per_page": raw["false_positives_per_page"],
        "fragmentation_per_ground_truth": round(int(raw["fragmentation"]) / ground_truth_count, 6)
        if ground_truth_count
        else 0.0,
        "merge_errors_per_ground_truth": round(int(raw["merge_errors"]) / ground_truth_count, 6)
        if ground_truth_count
        else 0.0,
    }


def assess_readiness(dataset_raw: object, prediction_raw: object, attestation_raw: object) -> Json:
    _privacy(dataset_raw, "dataset")
    _privacy(prediction_raw, "predictions")
    dataset = validate_dataset(dataset_raw)
    predictions = validate_predictions(prediction_raw, dataset)
    attestation = validate_attestation(attestation_raw, dataset, predictions)
    blockers: set[str] = set()

    document_attestations = {
        str(document["document_id"]): document
        for document in cast(list[Json], attestation["documents"])
    }
    real_cases = _real_test_cases(dataset, document_attestations)
    cases_by_modality = {
        modality: [case for case in real_cases if case["modality"] == modality]
        for modality in PILOT_MODALITIES
    }
    documents_by_modality = {
        modality: {str(case["document_id"]) for case in cases}
        for modality, cases in cases_by_modality.items()
    }
    formula_cases_by_modality = {
        modality: sum(bool(case["contains_formula"]) for case in cases)
        for modality, cases in cases_by_modality.items()
    }
    negative_cases_by_modality = {
        modality: sum(not bool(case["contains_formula"]) for case in cases)
        for modality, cases in cases_by_modality.items()
    }
    for modality in PILOT_MODALITIES:
        upper = modality.upper()
        if len(documents_by_modality[modality]) < MIN_DOCUMENTS_PER_MODALITY:
            blockers.add(f"INSUFFICIENT_{upper}_DOCUMENTS")
        if len(cases_by_modality[modality]) < MIN_JUDGED_CASES_PER_MODALITY:
            blockers.add(f"INSUFFICIENT_{upper}_JUDGED_CASES")
        if formula_cases_by_modality[modality] < MIN_FORMULA_CASES_PER_MODALITY:
            blockers.add(f"INSUFFICIENT_{upper}_FORMULA_CASES")
        if negative_cases_by_modality[modality] < MIN_NEGATIVE_CASES_PER_MODALITY:
            blockers.add(f"INSUFFICIENT_{upper}_NEGATIVE_CASES")

    negative_documents_by_tag: dict[str, set[str]] = defaultdict(set)
    for case in real_cases:
        if case["contains_formula"]:
            continue
        for tag in cast(list[str], case["negative_tags"]):
            negative_documents_by_tag[tag].add(str(case["document_id"]))
    for tag in REQUIRED_NEGATIVE_TAGS:
        if len(negative_documents_by_tag[tag]) < MIN_NEGATIVE_DOCUMENTS_PER_TAG:
            blockers.add(f"INSUFFICIENT_NEGATIVE_{tag.upper()}_DOCUMENTS")

    for document in document_attestations.values():
        origin = document["sample_origin"]
        if origin == "real_deidentified":
            if not document["deidentified"]:
                blockers.add("REAL_DOCUMENT_NOT_DEIDENTIFIED")
            if document["license_basis"] == "synthetic_generated":
                blockers.add("REAL_DOCUMENT_LICENSE_INVALID")
            if not document["evaluation_use_authorized"]:
                blockers.add("DOCUMENT_EVALUATION_USE_UNAUTHORIZED")
            if not document["local_acquisition_authorized"]:
                blockers.add("DOCUMENT_LOCAL_ACQUISITION_UNAUTHORIZED")
        elif document["license_basis"] != "synthetic_generated":
            blockers.add("SYNTHETIC_DOCUMENT_PROVENANCE_INVALID")

    detector = cast(Json, attestation["detector"])
    if not cast(Json, detector["code_license"])["verified"]:
        blockers.add("DETECTOR_CODE_LICENSE_UNVERIFIED")
    if not cast(Json, detector["weights_license"])["verified"]:
        blockers.add("DETECTOR_WEIGHTS_LICENSE_UNVERIFIED")
    if not cast(Json, detector["local_acquisition"])["authorized"]:
        blockers.add("DETECTOR_LOCAL_ACQUISITION_UNAUTHORIZED")

    blind = cast(Json, attestation["blind_review"])
    if blind["independent_reviewer_count"] < 2:
        blockers.add("BLIND_REVIEWER_COUNT_INSUFFICIENT")
    if not blind["adjudicated"]:
        blockers.add("BLIND_REVIEW_NOT_ADJUDICATED")
    if not blind["reviewer_identities_excluded"]:
        blockers.add("REVIEWER_IDENTITIES_PRESENT")
    if _utc(blind["prediction_sealed_at"], "prediction_sealed_at") >= _utc(
        blind["labels_unblinded_at"], "labels_unblinded_at"
    ):
        blockers.add("PREDICTIONS_NOT_SEALED_BEFORE_UNBLINDING")
    if blind["sealed_predictions_sha256"] != canonical_predictions_sha256(predictions):
        blockers.add("PREDICTIONS_CHANGED_AFTER_SEAL")

    required_prediction_ids = {str(case["case_id"]) for case in real_cases}
    prediction_ids = {str(case["case_id"]) for case in cast(list[Json], predictions["cases"])}
    if required_prediction_ids - prediction_ids:
        blockers.add("PREDICTION_COVERAGE_INCOMPLETE")
    if prediction_ids - required_prediction_ids:
        blockers.add("PREDICTIONS_INCLUDE_NON_PILOT_CASES")

    prediction_by_id = {
        str(case["case_id"]): case for case in cast(list[Json], predictions["cases"])
    }
    evaluation_predictions = {
        "schema_version": predictions["schema_version"],
        "detector": predictions["detector"],
        "cases": [
            prediction_by_id.get(
                str(case["case_id"]),
                {"case_id": case["case_id"], "proposals": [], "inference_ms": 0.0},
            )
            for case in real_cases
        ],
    }
    evaluation_dataset = {
        "schema_version": dataset["schema_version"],
        "dataset_id": dataset["dataset_id"],
        "annotator_decision_version": dataset["annotator_decision_version"],
        "cases": real_cases,
    }
    evaluated = evaluate(evaluation_dataset, evaluation_predictions)
    evaluated_metrics = cast(Json, evaluated["metrics"])
    overall_quality = _quality_summary(cast(Json, evaluated_metrics["overall"]))
    raw_by_modality = cast(Json, evaluated_metrics["by_modality"])
    by_modality_quality: Json = {
        modality: _quality_summary(cast(Json, raw_by_modality[modality]))
        for modality in PILOT_MODALITIES
    }
    negative_quality: Json = {
        "false_positives_per_page": cast(Json, evaluated_metrics["negative_pages"])[
            "false_positives_per_page"
        ]
    }
    quality_metrics: Json = {
        "overall": overall_quality,
        "by_modality": by_modality_quality,
        "negative_pages": negative_quality,
    }
    metric_policies = (
        ("precision", MIN_PRECISION, "minimum", "PILOT_PRECISION_BELOW_FLOOR"),
        ("recall", MIN_RECALL, "minimum", "PILOT_RECALL_BELOW_FLOOR"),
        (
            "formula_coverage",
            MIN_FORMULA_COVERAGE,
            "minimum",
            "PILOT_FORMULA_COVERAGE_BELOW_FLOOR",
        ),
        (
            "fragmentation_per_ground_truth",
            MAX_FRAGMENTATION_PER_GROUND_TRUTH,
            "maximum",
            "PILOT_FRAGMENTATION_PER_GROUND_TRUTH_ABOVE_CEILING",
        ),
        (
            "merge_errors_per_ground_truth",
            MAX_MERGE_ERRORS_PER_GROUND_TRUTH,
            "maximum",
            "PILOT_MERGE_ERRORS_PER_GROUND_TRUTH_ABOVE_CEILING",
        ),
    )
    for metric, boundary, direction, blocker in metric_policies:
        measured = float(overall_quality[metric])
        if (direction == "minimum" and measured < boundary) or (
            direction == "maximum" and measured > boundary
        ):
            blockers.add(blocker)
    for modality in PILOT_MODALITIES:
        modality_metrics = cast(Json, by_modality_quality[modality])
        upper = modality.upper()
        for metric, boundary, direction, blocker_suffix in metric_policies:
            measured = float(modality_metrics[metric])
            if (direction == "minimum" and measured < boundary) or (
                direction == "maximum" and measured > boundary
            ):
                blockers.add(f"{upper}_{blocker_suffix.removeprefix('PILOT_')}")
    if float(negative_quality["false_positives_per_page"]) > MAX_FALSE_POSITIVES_PER_PAGE:
        blockers.add("NEGATIVE_PAGES_FALSE_POSITIVES_PER_PAGE_ABOVE_CEILING")

    counts: Json = {
        "real_test_documents_by_modality": {
            modality: len(documents_by_modality[modality]) for modality in PILOT_MODALITIES
        },
        "real_test_judged_cases_by_modality": {
            modality: len(cases_by_modality[modality]) for modality in PILOT_MODALITIES
        },
        "real_test_formula_cases_by_modality": formula_cases_by_modality,
        "real_test_negative_cases_by_modality": negative_cases_by_modality,
        "negative_documents_by_tag": {
            tag: len(negative_documents_by_tag[tag]) for tag in REQUIRED_NEGATIVE_TAGS
        },
        "prediction_cases": len(prediction_ids),
    }
    self_attested_evaluation_complete = not blockers
    blockers.add("TRUSTED_ATTESTATION_REQUIRED")
    return {
        "schema_version": READINESS_REPORT_VERSION,
        "detector_identity": {"verified_against_private_attestation": True},
        "policy_version": READINESS_SCHEMA_VERSION,
        "enabled": False,
        "status": "self_attested_evaluation_only",
        "self_attested_evaluation_complete": self_attested_evaluation_complete,
        "eligible_for_pilot": False,
        "production_ready": False,
        "human_confirmation_required": True,
        "writes_product_data": False,
        "counts": counts,
        "metrics": quality_metrics,
        "blocker_codes": sorted(blockers),
    }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def load_json(path: Path) -> object:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("attestation", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = assess_readiness(
        load_json(args.dataset),
        load_json(args.predictions),
        load_json(args.attestation),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
