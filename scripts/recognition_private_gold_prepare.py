"""Prepare an anonymous, repository-external OCR gold annotation bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, cast

from PIL import Image

DIAGNOSTIC_VERSION = "ahamark-private-ocr-diagnostic-v1"
ANNOTATION_VERSION = "recognition-private-annotation-v1"
DECISION_VERSION = "decision-v1"
MODALITIES = {"text_pdf", "scan", "photo", "mixed"}
ROLES = {"reference_answer", "student_or_assignment_material"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
Json = dict[str, Any]


def _object(value: object, label: str) -> Json:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(Json, value)


def _exact_keys(value: Json, expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields are invalid")


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def validate_diagnostic_cases(raw: object) -> list[Json]:
    data = _object(raw, "diagnostic")
    _exact_keys(data, {"schema_version", "cases"}, "diagnostic")
    if data["schema_version"] != DIAGNOSTIC_VERSION:
        raise ValueError("diagnostic schema_version is invalid")
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("diagnostic.cases must be a non-empty list")
    seen: set[str] = set()
    validated: list[Json] = []
    expected = {
        "case_id",
        "page_index",
        "role",
        "modality",
        "width",
        "height",
        "gold_text_available",
        "gold_text_chars",
    }
    for index, value in enumerate(cast(list[object], cases)):
        label = f"diagnostic.cases[{index}]"
        case = _object(value, label)
        _exact_keys(case, expected, label)
        case_id = _uuid(case["case_id"], f"{label}.case_id")
        if case_id in seen:
            raise ValueError("diagnostic case_id must be unique")
        seen.add(case_id)
        if case["role"] not in ROLES or case["modality"] not in MODALITIES:
            raise ValueError(f"{label} role or modality is invalid")
        _positive_int(case["width"], f"{label}.width")
        _positive_int(case["height"], f"{label}.height")
        if int(case["width"]) * int(case["height"]) > MAX_IMAGE_PIXELS:
            raise ValueError(f"{label} exceeds the pixel limit")
        if isinstance(case["page_index"], bool) or not isinstance(case["page_index"], int):
            raise ValueError(f"{label}.page_index must be an integer")
        if int(case["page_index"]) < 0:
            raise ValueError(f"{label}.page_index must be non-negative")
        if not isinstance(case["gold_text_available"], bool):
            raise ValueError(f"{label}.gold_text_available must be boolean")
        if (
            isinstance(case["gold_text_chars"], bool)
            or not isinstance(case["gold_text_chars"], int)
            or int(case["gold_text_chars"]) < 0
        ):
            raise ValueError(f"{label}.gold_text_chars must be non-negative")
        validated.append(case)
    return validated


def validate_private_source_map(raw: object, case_ids: set[str]) -> list[Json]:
    data = _object(raw, "source_map")
    _exact_keys(data, {"private", "entries"}, "source_map")
    if data["private"] is not True:
        raise ValueError("source_map must be explicitly private")
    entries = data["entries"]
    if not isinstance(entries, list):
        raise ValueError("source_map.entries must be a list")
    seen: set[str] = set()
    validated: list[Json] = []
    for index, value in enumerate(cast(list[object], entries)):
        label = f"source_map.entries[{index}]"
        entry = _object(value, label)
        _exact_keys(entry, {"case_id", "source_kind", "source_ref", "page_index"}, label)
        case_id = _uuid(entry["case_id"], f"{label}.case_id")
        if case_id in seen:
            raise ValueError("source map case_id must be unique")
        seen.add(case_id)
        if not isinstance(entry["source_kind"], str) or not entry["source_kind"]:
            raise ValueError(f"{label}.source_kind is invalid")
        if not isinstance(entry["source_ref"], str) or not entry["source_ref"]:
            raise ValueError(f"{label}.source_ref is invalid")
        if isinstance(entry["page_index"], bool) or not isinstance(entry["page_index"], int):
            raise ValueError(f"{label}.page_index must be an integer")
        validated.append(entry)
    if seen != case_ids:
        raise ValueError("source map must exactly cover diagnostic cases")
    return validated


def _rank(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}\0{value}".encode()).hexdigest()


def _select_cases(
    cases: list[Json],
    source_by_case: dict[str, Json],
    *,
    sample_size: int,
    max_pages_per_document: int,
    scan_target: int,
    photo_target: int,
    reference_target: int,
    seed: str,
) -> list[Json]:
    if sample_size > len(cases):
        raise ValueError("sample_size exceeds available cases")
    ordered = sorted(cases, key=lambda row: _rank(seed, str(row["case_id"])))
    selected: list[Json] = []
    selected_ids: set[str] = set()
    document_counts: Counter[str] = Counter()

    def add_matching(predicate: Any, limit: int) -> None:
        for case in ordered:
            if limit <= 0:
                return
            case_id = str(case["case_id"])
            source_ref = str(source_by_case[case_id]["source_ref"])
            if (
                case_id not in selected_ids
                and document_counts[source_ref] < max_pages_per_document
                and predicate(case)
            ):
                selected.append(case)
                selected_ids.add(case_id)
                document_counts[source_ref] += 1
                limit -= 1

    add_matching(lambda row: row["modality"] == "photo", min(photo_target, sample_size))
    add_matching(
        lambda row: row["modality"] == "scan",
        min(scan_target, sample_size - len(selected)),
    )
    current_reference = sum(row["role"] == "reference_answer" for row in selected)
    add_matching(
        lambda row: row["role"] == "reference_answer",
        min(max(0, reference_target - current_reference), sample_size - len(selected)),
    )
    student_target = sample_size - reference_target
    current_student = sum(row["role"] == "student_or_assignment_material" for row in selected)
    add_matching(
        lambda row: row["role"] == "student_or_assignment_material",
        min(max(0, student_target - current_student), sample_size - len(selected)),
    )
    add_matching(lambda _row: True, sample_size - len(selected))
    if len(selected) != sample_size:
        raise ValueError("sampling constraints cannot produce the requested sample")
    return sorted(selected, key=lambda row: str(row["case_id"]))


def prepare_bundle(
    diagnostic_raw: object,
    source_map_raw: object,
    image_root: Path,
    output_root: Path,
    *,
    sample_size: int = 60,
    max_pages_per_document: int = 3,
    scan_target: int = 10,
    photo_target: int = 5,
    reference_target: int = 25,
    seed: str = "20260816",
    dataset_id: str | None = None,
) -> Json:
    for value, label in (
        (sample_size, "sample_size"),
        (max_pages_per_document, "max_pages_per_document"),
    ):
        _positive_int(value, label)
    for value, label in (
        (scan_target, "scan_target"),
        (photo_target, "photo_target"),
        (reference_target, "reference_target"),
    ):
        _nonnegative_int(value, label)
    if reference_target > sample_size or scan_target > sample_size or photo_target > sample_size:
        raise ValueError("sampling targets must not exceed sample_size")
    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be a non-empty string")
    cases = validate_diagnostic_cases(diagnostic_raw)
    case_ids = {str(case["case_id"]) for case in cases}
    source_entries = validate_private_source_map(source_map_raw, case_ids)
    source_by_case = {str(entry["case_id"]): entry for entry in source_entries}
    for case in cases:
        entry = source_by_case[str(case["case_id"])]
        if entry["page_index"] != case["page_index"]:
            raise ValueError("source map page_index must match diagnostic metadata")
    selected = _select_cases(
        cases,
        source_by_case,
        sample_size=sample_size,
        max_pages_per_document=max_pages_per_document,
        scan_target=scan_target,
        photo_target=photo_target,
        reference_target=reference_target,
        seed=seed,
    )
    if output_root.exists():
        raise ValueError("output_root must not already exist")
    validated_images: dict[str, Path] = {}
    for case in selected:
        case_id = str(case["case_id"])
        source_image = image_root / f"{case_id}.png"
        if not source_image.is_file() or source_image.is_symlink():
            raise ValueError("a selected source image is missing or unsafe")
        if source_image.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("a selected source image exceeds the byte limit")
        with Image.open(source_image) as image:
            image.load()
            if image.format != "PNG" or image.size != (int(case["width"]), int(case["height"])):
                raise ValueError("a selected source image does not match diagnostic metadata")
        validated_images[case_id] = source_image
    dataset_uuid = _uuid(dataset_id or str(uuid.uuid4()), "dataset_id")
    source_refs = sorted(
        {str(source_by_case[str(case["case_id"])]["source_ref"]) for case in selected},
        key=lambda value: _rank(seed, value),
    )
    document_ids = {
        source_ref: str(uuid.uuid5(uuid.UUID(dataset_uuid), f"document-{index}"))
        for index, source_ref in enumerate(source_refs)
    }
    annotation_cases: list[Json] = []
    for case in selected:
        case_id = str(case["case_id"])
        image_name = f"{case_id}.png"
        source_ref = str(source_by_case[case_id]["source_ref"])
        annotation_cases.append(
            {
                "case_id": case_id,
                "document_id": document_ids[source_ref],
                "split": "test",
                "modality": case["modality"],
                "role": case["role"],
                "image_file": image_name,
                "page_width": case["width"],
                "page_height": case["height"],
                "degradation_tags": [],
                "content_tags": [],
                "annotation_status": "pending",
                "privacy_status": "pending",
                "expected_text": "",
                "expected_question_numbers": [],
                "expected_regions": [],
                "expect_integrity_rejection": False,
                "annotator_decision_version": DECISION_VERSION,
            }
        )
    annotation = {
        "schema_version": ANNOTATION_VERSION,
        "dataset_id": dataset_uuid,
        "annotator_decision_version": DECISION_VERSION,
        "cases": annotation_cases,
    }
    private_map = {
        "private": True,
        "dataset_id": dataset_uuid,
        "documents": [
            {"document_id": document_ids[source_ref], "source_ref": source_ref}
            for source_ref in source_refs
        ],
    }
    temporary_root = output_root.with_name(f".{output_root.name}.tmp-{uuid.uuid4()}")
    temporary_root.mkdir(parents=True)
    try:
        image_output = temporary_root / "images"
        image_output.mkdir()
        for case in selected:
            case_id = str(case["case_id"])
            shutil.copy2(validated_images[case_id], image_output / f"{case_id}.png")
        (temporary_root / "annotation-seed.json").write_text(
            json.dumps(annotation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (temporary_root / "private-document-map.json").write_text(
            json.dumps(private_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary_root.replace(output_root)
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise
    modality_counts = Counter(str(case["modality"]) for case in annotation_cases)
    role_counts = Counter(str(case["role"]) for case in annotation_cases)
    return {
        "schema_version": ANNOTATION_VERSION,
        "selected_page_count": len(annotation_cases),
        "selected_document_count": len(source_refs),
        "modality_counts": dict(sorted(modality_counts.items())),
        "role_counts": dict(sorted(role_counts.items())),
        "annotation_complete": False,
        "accuracy_claim": False,
        "writes_product_data": False,
    }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> object:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid number: {value}")),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostic", type=Path)
    parser.add_argument("private_source_map", type=Path)
    parser.add_argument("image_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--sample-size", type=int, default=60)
    parser.add_argument("--max-pages-per-document", type=int, default=3)
    parser.add_argument("--scan-target", type=int, default=10)
    parser.add_argument("--photo-target", type=int, default=5)
    parser.add_argument("--reference-target", type=int, default=25)
    parser.add_argument("--seed", default="20260816")
    parser.add_argument("--dataset-id")
    args = parser.parse_args()
    summary = prepare_bundle(
        load_json(args.diagnostic),
        load_json(args.private_source_map),
        args.image_root,
        args.output_root,
        sample_size=args.sample_size,
        max_pages_per_document=args.max_pages_per_document,
        scan_target=args.scan_target,
        photo_target=args.photo_target,
        reference_target=args.reference_target,
        seed=args.seed,
        dataset_id=args.dataset_id,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
