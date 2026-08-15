"""Strict offline metrics for the sanitized synthetic recognition baseline."""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from app.recognition.question_numbers import normalize_question_number

SCHEMA_VERSION = "recognition-synthetic-v1"
REPORT_VERSION = "recognition-synthetic-report-v1"
IOU_THRESHOLD = 0.5
MODALITIES = {"text_pdf", "scan", "photo", "mixed"}
CONTENT_TAGS = {
    "chinese_stem",
    "english_stem",
    "inline_formula",
    "display_formula",
    "partial_derivative",
    "integral",
    "limit",
    "greek",
    "unicode_super_sub",
    "latex",
    "matrix",
    "piecewise_function",
    "hierarchical_question",
    "multi_column",
    "table_negative",
    "geometry_negative",
}
DEGRADATION_TAGS = {"clean", "low_resolution", "slight_rotation", "perspective"}
REQUIRED_CONTENT_TAGS = CONTENT_TAGS
REQUIRED_DEGRADATION_TAGS = {"low_resolution", "slight_rotation", "perspective"}
PRIVATE_KEY = re.compile(
    r"path|file.?name|student|teacher|person.?name|annotator.?name|class.?id|"
    r"assignment.?id|database.?id|source_hash|pdf_hash|original_hash|email",
    re.I,
)
ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|file://|^/)")
EMAIL_VALUE = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE_VALUE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LABELED_IDENTITY_VALUE = re.compile(
    r"(?:姓名|学生姓名|学号|student\s*id|student\s*name|person\s*name|full\s*name)"
    r"\s*[:：]\s*\S+",
    re.I,
)
CASE_ID = re.compile(r"synthetic-[a-z0-9-]{1,64}")
SOURCE_NAMES = {"pdf_text", "rapidocr"}
LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")
LATIN1_SUPERSCRIPTS = {"¹", "²", "³"}
Box = tuple[float, float, float, float]
Json = dict[str, Any]


def _object(value: object, label: str) -> Json:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(Json, value)


def _strict_keys(value: Json, required: set[str], label: str) -> None:
    if missing := required - value.keys():
        raise ValueError(f"{label} missing fields: {sorted(missing)}")
    if unknown := value.keys() - required:
        raise ValueError(f"{label} unknown fields: {sorted(unknown)}")


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
        if (
            EMAIL_VALUE.search(value)
            or PHONE_VALUE.search(value)
            or LABELED_IDENTITY_VALUE.search(value)
        ):
            raise ValueError(f"private value forbidden at {label}")


def _string_list(value: object, allowed: set[str] | None, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    result = cast(list[str], value)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must not contain duplicates")
    if allowed is not None and set(result) - allowed:
        raise ValueError(f"{label} contains unknown values")
    return result


def _box(value: object, label: str) -> Box:
    raw = _object(value, label)
    _strict_keys(raw, {"x", "y", "width", "height"}, label)
    values: list[float] = []
    for key in ("x", "y", "width", "height"):
        item = raw[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{label}.{key} must be numeric")
        values.append(float(item))
    x, y, width, height = values
    if not all(math.isfinite(item) for item in values) or (
        x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1
    ):
        raise ValueError(f"{label} must be within normalized page bounds")
    return x, y, width, height


def _regions(value: object, id_key: str, label: str) -> list[Json]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    output: list[Json] = []
    ids: set[str] = set()
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        row = _object(item, item_label)
        _strict_keys(row, {id_key, "bbox"}, item_label)
        identifier = row[id_key]
        if not isinstance(identifier, str) or not CASE_ID.fullmatch(identifier):
            raise ValueError(f"{item_label}.{id_key} is invalid")
        if identifier in ids:
            raise ValueError(f"{label} contains duplicate ids")
        ids.add(identifier)
        _box(row["bbox"], f"{item_label}.bbox")
        output.append(row)
    return output


def validate_dataset(raw: object) -> Json:
    """Validate a content-synthetic fixture and reject accidental private metadata."""

    _privacy(raw)
    data = _object(raw, "dataset")
    _strict_keys(data, {"schema_version", "dataset_id", "synthetic_only", "cases"}, "dataset")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(data["dataset_id"], str) or not CASE_ID.fullmatch(data["dataset_id"]):
        raise ValueError("dataset_id is invalid")
    if data["synthetic_only"] is not True:
        raise ValueError("synthetic_only must be true")
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    required = {
        "case_id",
        "modality",
        "content_tags",
        "degradation_tags",
        "expected_text",
        "observed_text",
        "expected_question_numbers",
        "observed_question_lines",
        "expected_regions",
        "proposed_regions",
        "expected_sources",
        "observed_sources",
        "suggestion_count",
        "manual_required_count",
        "expect_integrity_rejection",
        "integrity_rejected",
    }
    case_ids: set[str] = set()
    all_content_tags: set[str] = set()
    all_degradation_tags: set[str] = set()
    modalities: set[str] = set()
    for index, item in enumerate(cases):
        label = f"cases[{index}]"
        case = _object(item, label)
        _strict_keys(case, required, label)
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id):
            raise ValueError(f"{label}.case_id is invalid")
        if case_id in case_ids:
            raise ValueError("case_id must be unique")
        case_ids.add(case_id)
        modality = case["modality"]
        if not isinstance(modality, str) or modality not in MODALITIES:
            raise ValueError(f"{label}.modality is invalid")
        modalities.add(modality)
        content_tags = _string_list(case["content_tags"], CONTENT_TAGS, f"{label}.content_tags")
        degradation_tags = _string_list(
            case["degradation_tags"], DEGRADATION_TAGS, f"{label}.degradation_tags"
        )
        if not content_tags or not degradation_tags:
            raise ValueError(f"{label} tags must be non-empty")
        all_content_tags.update(content_tags)
        all_degradation_tags.update(degradation_tags)
        for key in ("expected_text", "observed_text"):
            if not isinstance(case[key], str):
                raise ValueError(f"{label}.{key} must be a string")
        _string_list(case["expected_question_numbers"], None, f"{label}.expected_question_numbers")
        _string_list(case["observed_question_lines"], None, f"{label}.observed_question_lines")
        _regions(case["expected_regions"], "region_id", f"{label}.expected_regions")
        _regions(case["proposed_regions"], "proposal_id", f"{label}.proposed_regions")
        _string_list(case["expected_sources"], SOURCE_NAMES, f"{label}.expected_sources")
        _string_list(case["observed_sources"], SOURCE_NAMES, f"{label}.observed_sources")
        suggestion_count = case["suggestion_count"]
        manual_count = case["manual_required_count"]
        if (
            isinstance(suggestion_count, bool)
            or not isinstance(suggestion_count, int)
            or suggestion_count < 0
            or isinstance(manual_count, bool)
            or not isinstance(manual_count, int)
            or not 0 <= manual_count <= suggestion_count
        ):
            raise ValueError(f"{label} suggestion counts are invalid")
        if not isinstance(case["expect_integrity_rejection"], bool) or not isinstance(
            case["integrity_rejected"], bool
        ):
            raise ValueError(f"{label} integrity flags must be boolean")
    if modalities != MODALITIES:
        raise ValueError("fixture must cover all four modalities")
    if not REQUIRED_CONTENT_TAGS <= all_content_tags:
        raise ValueError("fixture is missing required content tags")
    if not REQUIRED_DEGRADATION_TAGS <= all_degradation_tags:
        raise ValueError("fixture is missing required degradation tags")
    return data


def _normalized_characters(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return [character for character in normalized if not character.isspace()]


def _math_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text)
    command_starts = {match.start(): match for match in LATEX_COMMAND.finditer(normalized)}
    tokens: list[str] = []
    index = 0
    while index < len(normalized):
        if match := command_starts.get(index):
            tokens.append(match.group(0))
            index = match.end()
            continue
        character = normalized[index]
        codepoint = ord(character)
        name = unicodedata.name(character, "")
        if (
            unicodedata.category(character) == "Sm"
            or "GREEK" in name
            or 0x2070 <= codepoint <= 0x209F
            or character in LATIN1_SUPERSCRIPTS
            or character in "^_{}&"
        ):
            tokens.append(character)
        index += 1
    return tokens


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = [0] * (len(right) + 1)
    for left_item in left:
        current = [0]
        for index, right_item in enumerate(right, 1):
            current.append(
                previous[index - 1] + 1
                if left_item == right_item
                else max(previous[index], current[-1])
            )
        previous = current
    return previous[-1]


def iou(left: Box, right: Box) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    intersection = max(0.0, min(lx + lw, rx + rw) - max(lx, rx)) * max(
        0.0, min(ly + lh, ry + rh) - max(ly, ry)
    )
    union = lw * lh + rw * rh - intersection
    return intersection / union if union else 0.0


def _maximum_cardinality_match(adjacency: dict[str, list[tuple[float, str]]]) -> int:
    proposal_match: dict[str, str] = {}

    def augment(region_id: str, seen: set[str]) -> bool:
        for _overlap, proposal_id in adjacency[region_id]:
            if proposal_id in seen:
                continue
            seen.add(proposal_id)
            prior_region = proposal_match.get(proposal_id)
            if prior_region is None or augment(prior_region, seen):
                proposal_match[proposal_id] = region_id
                return True
        return False

    return sum(
        augment(region_id, set())
        for region_id in sorted(
            adjacency,
            key=lambda item: (
                -adjacency[item][0][0] if adjacency[item] else 0.0,
                item,
            ),
        )
    )


def _region_counts(cases: Sequence[Json]) -> tuple[int, int, int]:
    true_positive = false_positive = false_negative = 0
    for case in cases:
        expected = cast(list[Json], case["expected_regions"])
        proposed = cast(list[Json], case["proposed_regions"])
        adjacency: dict[str, list[tuple[float, str]]] = {}
        for gold in expected:
            region_id = str(gold["region_id"])
            edges = [
                (
                    iou(_box(gold["bbox"], "bbox"), _box(prediction["bbox"], "bbox")),
                    str(prediction["proposal_id"]),
                )
                for prediction in proposed
            ]
            adjacency[region_id] = sorted(
                (edge for edge in edges if edge[0] >= IOU_THRESHOLD),
                key=lambda edge: (-edge[0], edge[1]),
            )
        matched_count = _maximum_cardinality_match(adjacency)
        true_positive += matched_count
        false_positive += len(proposed) - matched_count
        false_negative += len(expected) - matched_count
    return true_positive, false_positive, false_negative


def _safe_ratio(numerator: int, denominator: int, *, empty: float = 1.0) -> float:
    return round(numerator / denominator, 6) if denominator else empty


def _metrics(cases: Sequence[Json]) -> Json:
    expected_characters = [_normalized_characters(str(case["expected_text"])) for case in cases]
    observed_characters = [_normalized_characters(str(case["observed_text"])) for case in cases]
    character_denominator = sum(map(len, expected_characters))
    character_matches = sum(
        _lcs_length(expected, observed)
        for expected, observed in zip(expected_characters, observed_characters, strict=True)
    )
    expected_math = [_math_tokens(str(case["expected_text"])) for case in cases]
    observed_math = [_math_tokens(str(case["observed_text"])) for case in cases]
    math_denominator = sum(map(len, expected_math))
    math_matches = sum(
        _lcs_length(expected, observed)
        for expected, observed in zip(expected_math, observed_math, strict=True)
    )
    exact_question_cases = 0
    judged_question_cases = 0
    exact_question_anchors = 0
    expected_question_anchors = 0
    extra_question_anchors = 0
    for case in cases:
        expected = cast(list[str], case["expected_question_numbers"])
        observed = [
            normalized
            for line in cast(list[str], case["observed_question_lines"])
            if (normalized := normalize_question_number(line)) is not None
        ]
        extra_question_anchors += max(0, len(observed) - len(expected))
        if not expected:
            continue
        judged_question_cases += 1
        exact_question_cases += observed == expected
        exact_question_anchors += sum(
            left == right for left, right in zip(expected, observed, strict=False)
        )
        expected_question_anchors += len(expected)
    true_positive, false_positive, false_negative = _region_counts(cases)
    expected_source_pairs = {
        (str(case["case_id"]), source)
        for case in cases
        for source in cast(list[str], case["expected_sources"])
    }
    observed_source_pairs = {
        (str(case["case_id"]), source)
        for case in cases
        for source in cast(list[str], case["observed_sources"])
    }
    suggestions = sum(int(case["suggestion_count"]) for case in cases)
    manual = sum(int(case["manual_required_count"]) for case in cases)
    expected_rejections = sum(bool(case["expect_integrity_rejection"]) for case in cases)
    actual_rejections = sum(bool(case["integrity_rejected"]) for case in cases)
    correct_rejections = sum(
        bool(case["expect_integrity_rejection"]) and bool(case["integrity_rejected"])
        for case in cases
    )
    false_rejections = sum(
        not bool(case["expect_integrity_rejection"]) and bool(case["integrity_rejected"])
        for case in cases
    )
    precision = _safe_ratio(true_positive, true_positive + false_positive)
    recall = _safe_ratio(true_positive, true_positive + false_negative)
    return {
        "page_count": len(cases),
        "character_completeness": _safe_ratio(character_matches, character_denominator),
        "math_symbol_retention": _safe_ratio(math_matches, math_denominator),
        "question_number_accuracy": _safe_ratio(exact_question_cases, judged_question_cases),
        "question_number_details": {
            "judged_case_count": judged_question_cases,
            "exact_case_count": exact_question_cases,
            "exact_anchor_count": exact_question_anchors,
            "expected_anchor_count": expected_question_anchors,
            "missed_anchor_count": expected_question_anchors - exact_question_anchors,
            "extra_anchor_count": extra_question_anchors,
        },
        "region_precision": precision,
        "region_recall": recall,
        "region_details": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "iou_threshold": IOU_THRESHOLD,
        },
        "false_suggestions_per_page": _safe_ratio(false_positive, len(cases), empty=0.0),
        "false_regions_per_page": _safe_ratio(false_positive, len(cases), empty=0.0),
        "source_coverage": _safe_ratio(
            len(expected_source_pairs & observed_source_pairs), len(expected_source_pairs)
        ),
        "source_details": {
            "covered_pairs": len(expected_source_pairs & observed_source_pairs),
            "expected_pairs": len(expected_source_pairs),
        },
        "manual_required_ratio": _safe_ratio(manual, suggestions, empty=0.0),
        "manual_required_details": {
            "manual_required_count": manual,
            "suggestion_count": suggestions,
        },
        "integrity_gate_rejection_count": actual_rejections,
        "integrity_gate_details": {
            "expected_rejection_count": expected_rejections,
            "correct_rejection_count": correct_rejections,
            "missed_rejection_count": expected_rejections - correct_rejections,
            "false_rejection_count": false_rejections,
        },
    }


def evaluate(raw: object) -> Json:
    data = validate_dataset(raw)
    cases = cast(list[Json], data["cases"])
    degradation_tags = sorted(
        {tag for case in cases for tag in cast(list[str], case["degradation_tags"])}
    )
    return {
        "schema_version": REPORT_VERSION,
        "dataset_schema_version": SCHEMA_VERSION,
        "dataset_id": data["dataset_id"],
        "synthetic_only": True,
        "real_accuracy": False,
        "production_ready": False,
        "writes_product_data": False,
        "metric_policy": {
            "text_normalization": "NFC, CRLF to LF, Unicode whitespace removed",
            "character_matching": "micro LCS over Unicode code points",
            "math_matching": (
                "micro LCS over Unicode Sm, Greek, superscript/subscript, "
                "LaTeX and structure tokens"
            ),
            "question_number_matching": "ordered exact match after shared normalization",
            "region_matching": (
                "deterministic maximum-cardinality bipartite matching at IoU threshold; "
                "edge preference descending IoU, then IDs"
            ),
            "region_iou_threshold": IOU_THRESHOLD,
            "false_suggestion_scope": (
                "one region proposal is one region suggestion; unmatched proposals "
                "per synthetic page"
            ),
            "source_coverage_scope": "observed required (case, source) pairs",
        },
        "metrics": {
            "overall": _metrics(cases),
            "by_modality": {
                modality: _metrics([case for case in cases if case["modality"] == modality])
                for modality in sorted(MODALITIES)
            },
            "by_degradation": {
                tag: _metrics(
                    [case for case in cases if tag in cast(list[str], case["degradation_tags"])]
                )
                for tag in degradation_tags
            },
        },
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
