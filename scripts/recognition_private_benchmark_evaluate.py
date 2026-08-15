"""Strict aggregate-only evaluator for a private, offline OCR benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import unicodedata
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from PIL import Image

GOLD_VERSION = "recognition-private-gold-v1"
PREDICTION_VERSION = "recognition-private-predictions-v1"
ATTESTATION_VERSION = "recognition-private-attestation-v1"
REPORT_VERSION = "recognition-private-report-v1"
MODALITIES = ("text_pdf", "scan", "photo", "mixed")
DEGRADATIONS = {
    "clean",
    "low_resolution",
    "blurred",
    "rotation",
    "perspective",
    "low_contrast",
    "shadow",
    "cropped",
}
CONTENT_TAGS = {"chinese", "english", "math", "question_number", "negative"}
IOU_THRESHOLD = 0.5
MIN_SLICE_DOCUMENTS = 2
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
SAFE_IMAGE = re.compile(r"[0-9a-f-]{36}\.(?:png|jpg|jpeg)", re.I)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE = re.compile(r"(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)")
IDENTITY = re.compile(
    r"(?:学生姓名|姓名|学号|学生号|student[ _-]?(?:name|id))\s*[:：=]\s*\S+", re.I
)
ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|file://|^/)")
PRIVATE_KEY = re.compile(
    r"student|teacher|person.?name|reviewer.?name|class.?id|assignment.?id|"
    r"database.?id|source.?hash|original.?hash|email|phone|address|url",
    re.I,
)
MATH_COMMAND = re.compile(r"\\[A-Za-z]+")
Json = dict[str, Any]
Box = tuple[float, float, float, float]


def _object(value: object, label: str) -> Json:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(Json, value)


def _keys(value: Json, required: set[str], label: str) -> None:
    if set(value) != required:
        raise ValueError(f"{label} fields are invalid")


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _privacy(value: object, label: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if PRIVATE_KEY.search(str(key)):
                raise ValueError(f"private field forbidden at {label}.{key}")
            _privacy(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _privacy(item, f"{label}[{index}]")
    elif isinstance(value, str) and (
        ABSOLUTE_PATH.search(value)
        or EMAIL.search(value)
        or PHONE.search(value)
        or IDENTITY.search(value)
    ):
        raise ValueError(f"private value forbidden at {label}")


def _string_list(value: object, allowed: set[str] | None, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    result = cast(list[str], value)
    if len(result) != len(set(result)) or (allowed is not None and set(result) - allowed):
        raise ValueError(f"{label} contains duplicate or unknown values")
    return result


def _box(value: object, label: str) -> Box:
    row = _object(value, label)
    _keys(row, {"x", "y", "width", "height"}, label)
    values = [row[key] for key in ("x", "y", "width", "height")]
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values):
        raise ValueError(f"{label} values must be numeric")
    x, y, width, height = map(float, values)
    if not all(math.isfinite(item) for item in (x, y, width, height)) or (
        x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1
    ):
        raise ValueError(f"{label} must be within normalized bounds")
    return x, y, width, height


def _regions(value: object, id_key: str, label: str) -> list[Json]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    rows = cast(list[Json], value)
    seen: set[str] = set()
    for index, item in enumerate(rows):
        row = _object(item, f"{label}[{index}]")
        _keys(row, {id_key, "bbox"}, f"{label}[{index}]")
        identifier = _uuid(row[id_key], f"{label}[{index}].{id_key}")
        if identifier in seen:
            raise ValueError(f"{label} ids must be unique")
        seen.add(identifier)
        _box(row["bbox"], f"{label}[{index}].bbox")
    return rows


def validate_gold(raw: object) -> Json:
    _privacy(raw, "gold")
    data = _object(raw, "gold")
    _keys(data, {"schema_version", "dataset_id", "annotator_decision_version", "cases"}, "gold")
    if data["schema_version"] != GOLD_VERSION:
        raise ValueError(f"schema_version must be {GOLD_VERSION}")
    _uuid(data["dataset_id"], "dataset_id")
    decision = data["annotator_decision_version"]
    if not isinstance(decision, str) or re.fullmatch(r"[A-Za-z0-9._-]{1,40}", decision) is None:
        raise ValueError("annotator_decision_version is invalid")
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    seen: set[str] = set()
    document_splits: dict[str, str] = {}
    for index, item in enumerate(cast(list[Json], cases)):
        label = f"cases[{index}]"
        case = _object(item, label)
        _keys(
            case,
            {
                "case_id",
                "document_id",
                "split",
                "modality",
                "image_file",
                "page_width",
                "page_height",
                "degradation_tags",
                "content_tags",
                "annotation_status",
                "expected_text",
                "expected_question_numbers",
                "expected_regions",
                "expect_integrity_rejection",
                "annotator_decision_version",
            },
            label,
        )
        case_id = _uuid(case["case_id"], f"{label}.case_id")
        document_id = _uuid(case["document_id"], f"{label}.document_id")
        if case_id in seen:
            raise ValueError("case_id must be unique")
        seen.add(case_id)
        split = case["split"]
        if split not in {"train", "dev", "test"}:
            raise ValueError(f"{label}.split is invalid")
        if document_id in document_splits and document_splits[document_id] != split:
            raise ValueError("train/dev/test split must be isolated by document_id")
        document_splits[document_id] = str(split)
        if case["modality"] not in MODALITIES:
            raise ValueError(f"{label}.modality is invalid")
        if (
            case["image_file"] != f"{case_id}.png"
            or SAFE_IMAGE.fullmatch(str(case["image_file"])) is None
        ):
            raise ValueError(f"{label}.image_file must be the case UUID plus .png")
        for key in ("page_width", "page_height"):
            if isinstance(case[key], bool) or not isinstance(case[key], int) or case[key] <= 0:
                raise ValueError(f"{label}.{key} must be positive")
        if int(case["page_width"]) * int(case["page_height"]) > MAX_IMAGE_PIXELS:
            raise ValueError(f"{label} exceeds the image pixel limit")
        if not _string_list(case["degradation_tags"], DEGRADATIONS, f"{label}.degradation_tags"):
            raise ValueError(f"{label}.degradation_tags must not be empty")
        if not _string_list(case["content_tags"], CONTENT_TAGS, f"{label}.content_tags"):
            raise ValueError(f"{label}.content_tags must not be empty")
        if case["annotation_status"] not in {"annotated", "unjudgeable"}:
            raise ValueError(f"{label}.annotation_status is invalid")
        if not isinstance(case["expected_text"], str):
            raise ValueError(f"{label}.expected_text must be a string")
        _string_list(case["expected_question_numbers"], None, f"{label}.expected_question_numbers")
        _regions(case["expected_regions"], "region_id", f"{label}.expected_regions")
        if not isinstance(case["expect_integrity_rejection"], bool):
            raise ValueError(f"{label}.expect_integrity_rejection must be boolean")
        if case["annotator_decision_version"] != decision:
            raise ValueError(f"{label} decision version mismatch")
    return data


def validate_predictions(raw: object, gold: Json) -> Json:
    _privacy(raw, "predictions")
    data = _object(raw, "predictions")
    _keys(data, {"schema_version", "detector", "cases"}, "predictions")
    if data["schema_version"] != PREDICTION_VERSION:
        raise ValueError(f"schema_version must be {PREDICTION_VERSION}")
    detector = _object(data["detector"], "detector")
    _keys(detector, {"name", "version"}, "detector")
    if not all(isinstance(value, str) and value for value in detector.values()):
        raise ValueError("detector fields must be non-empty strings")
    known = {case["case_id"] for case in cast(list[Json], gold["cases"])}
    if not isinstance(data["cases"], list):
        raise ValueError("prediction cases must be a list")
    seen: set[str] = set()
    for index, item in enumerate(cast(list[Json], data["cases"])):
        label = f"prediction cases[{index}]"
        case = _object(item, label)
        _keys(
            case,
            {
                "case_id",
                "observed_text",
                "observed_question_numbers",
                "proposed_regions",
                "inference_ms",
                "peak_memory_mb",
                "suggestion_count",
                "manual_required_count",
                "integrity_rejected",
            },
            label,
        )
        case_id = _uuid(case["case_id"], f"{label}.case_id")
        if case_id not in known or case_id in seen:
            raise ValueError(f"{label}.case_id is unknown or duplicate")
        seen.add(case_id)
        if not isinstance(case["observed_text"], str):
            raise ValueError(f"{label}.observed_text must be a string")
        _string_list(case["observed_question_numbers"], None, f"{label}.observed_question_numbers")
        _regions(case["proposed_regions"], "proposal_id", f"{label}.proposed_regions")
        for key in ("inference_ms", "peak_memory_mb"):
            value = case[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{label}.{key} must be finite and non-negative")
        for key in ("suggestion_count", "manual_required_count"):
            if isinstance(case[key], bool) or not isinstance(case[key], int) or case[key] < 0:
                raise ValueError(f"{label}.{key} must be non-negative integer")
        if case["manual_required_count"] > case["suggestion_count"]:
            raise ValueError(f"{label} manual count exceeds suggestions")
        if not isinstance(case["integrity_rejected"], bool):
            raise ValueError(f"{label}.integrity_rejected must be boolean")
    return data


def canonical_predictions_sha256(predictions: object) -> str:
    encoded = json.dumps(
        predictions, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_attestation(raw: object, gold: Json, predictions: Json) -> Json:
    _privacy(raw, "attestation")
    data = _object(raw, "attestation")
    _keys(data, {"schema_version", "dataset_id", "documents", "blind_review"}, "attestation")
    if data["schema_version"] != ATTESTATION_VERSION or data["dataset_id"] != gold["dataset_id"]:
        raise ValueError("attestation schema or dataset mismatch")
    documents = data["documents"]
    if not isinstance(documents, list):
        raise ValueError("documents must be a list")
    seen: set[str] = set()
    for index, item in enumerate(cast(list[Json], documents)):
        label = f"documents[{index}]"
        document = _object(item, label)
        _keys(
            document,
            {
                "document_id",
                "sample_origin",
                "deidentified",
                "evaluation_use_authorized",
                "local_acquisition_authorized",
            },
            label,
        )
        document_id = _uuid(document["document_id"], f"{label}.document_id")
        if document_id in seen:
            raise ValueError("document attestations must be unique")
        seen.add(document_id)
        if document["sample_origin"] not in {"real_deidentified", "synthetic"}:
            raise ValueError(f"{label}.sample_origin is invalid")
        for key in ("deidentified", "evaluation_use_authorized", "local_acquisition_authorized"):
            if not isinstance(document[key], bool):
                raise ValueError(f"{label}.{key} must be boolean")
    gold_documents = {str(case["document_id"]) for case in cast(list[Json], gold["cases"])}
    if seen != gold_documents:
        raise ValueError("documents must exactly attest gold document IDs")
    blind = _object(data["blind_review"], "blind_review")
    _keys(
        blind,
        {
            "independent_reviewer_count",
            "adjudicated",
            "reviewer_identities_excluded",
            "sealed_predictions_sha256",
            "prediction_sealed_at",
            "labels_unblinded_at",
        },
        "blind_review",
    )
    reviewer_count = blind["independent_reviewer_count"]
    if isinstance(reviewer_count, bool) or not isinstance(reviewer_count, int):
        raise ValueError("independent_reviewer_count must be an integer")
    if not isinstance(blind["adjudicated"], bool) or not isinstance(
        blind["reviewer_identities_excluded"], bool
    ):
        raise ValueError("blind review flags must be boolean")
    if reviewer_count < 2 or not blind["adjudicated"] or not blind["reviewer_identities_excluded"]:
        raise ValueError("blind double review attestation is incomplete")
    digest = blind["sealed_predictions_sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("sealed_predictions_sha256 is invalid")
    if digest != canonical_predictions_sha256(predictions):
        raise ValueError("predictions changed after sealing")
    sealed_raw, unblinded_raw = blind["prediction_sealed_at"], blind["labels_unblinded_at"]
    if (
        not isinstance(sealed_raw, str)
        or not sealed_raw.endswith("Z")
        or not isinstance(unblinded_raw, str)
        or not unblinded_raw.endswith("Z")
    ):
        raise ValueError("blind review timestamps must be RFC3339 UTC")
    try:
        sealed = datetime.fromisoformat(sealed_raw[:-1] + "+00:00")
        unblinded = datetime.fromisoformat(unblinded_raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("blind review timestamps are invalid") from exc
    if sealed >= unblinded:
        raise ValueError("predictions must be sealed before unblinding")
    return data


def _validate_images(image_root: Path, cases: list[Json]) -> None:
    root = image_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("image_root must be a directory")
    expected = {str(case["image_file"]) for case in cases}
    actual = {item.name for item in root.iterdir()}
    if actual != expected:
        raise ValueError("image inventory must exactly match evaluated cases")
    for case in cases:
        path = root / str(case["image_file"])
        if path.is_symlink() or path.resolve().parent != root or not path.is_file():
            raise ValueError("image must be a regular file confined to image_root")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds the byte limit")
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if image.size != (case["page_width"], case["page_height"]):
                    raise ValueError("image dimensions do not match gold")
        except (OSError, SyntaxError) as exc:
            raise ValueError("image is invalid") from exc


def _edit_distance(left: Sequence[str], right: Sequence[str]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, 1):
        current = [left_index]
        for right_index, right_item in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
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


def _math_tokens(text: str) -> list[str]:
    starts = {match.start(): match for match in MATH_COMMAND.finditer(text)}
    tokens: list[str] = []
    index = 0
    while index < len(text):
        if match := starts.get(index):
            tokens.append(match.group())
            index = match.end()
            continue
        character = text[index]
        if character.isascii() and character.isalpha():
            identifier = re.match(r"[A-Za-z]+", text[index:])
            assert identifier is not None
            value = identifier.group()
            if len(value) == 1:
                tokens.append(value.lower())
            index += len(value)
            continue
        if character.isdecimal():
            number = re.match(r"\d+(?:\.\d+)?", text[index:])
            assert number is not None
            tokens.append(number.group())
            index += len(number.group())
            continue
        name = unicodedata.name(character, "")
        codepoint = ord(character)
        if (
            unicodedata.category(character) == "Sm"
            or "GREEK" in name
            or 0x2070 <= codepoint <= 0x209F
            or character in "+-=*/<>^_{}&()[]¹²³"
        ):
            tokens.append(character)
        index += 1
    return tokens


def _iou(left: Box, right: Box) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    intersection = max(0.0, min(lx + lw, rx + rw) - max(lx, rx)) * max(
        0.0, min(ly + lh, ry + rh) - max(ly, ry)
    )
    union = lw * lh + rw * rh - intersection
    return intersection / union if union else 0.0


def _maximum_match(adjacency: dict[int, list[int]]) -> int:
    matched: dict[int, int] = {}

    def augment(index: int, seen: set[int]) -> bool:
        for proposal_index in adjacency[index]:
            if proposal_index in seen:
                continue
            seen.add(proposal_index)
            if proposal_index not in matched or augment(matched[proposal_index], seen):
                matched[proposal_index] = index
                return True
        return False

    return sum(augment(index, set()) for index in adjacency)


def _region_counts(cases: list[Json], prediction_map: dict[str, Json]) -> tuple[int, int, int, int]:
    tp = fp = fn = negative_fp = 0
    for case in cases:
        gold = cast(list[Json], case["expected_regions"])
        proposed = cast(list[Json], prediction_map[str(case["case_id"])]["proposed_regions"])
        adjacency = {
            index: [
                j
                for j, proposal in enumerate(proposed)
                if _iou(_box(region["bbox"], "bbox"), _box(proposal["bbox"], "bbox"))
                >= IOU_THRESHOLD
            ]
            for index, region in enumerate(gold)
        }
        matches = _maximum_match(adjacency)
        tp += matches
        fp += len(proposed) - matches
        fn += len(gold) - matches
        if not gold:
            negative_fp += len(proposed)
    return tp, fp, fn, negative_fp


def _ratio(numerator: int, denominator: int, empty: float = 1.0) -> float:
    return round(numerator / denominator, 6) if denominator else empty


def _supported_ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _insertion_aware_error_rate(errors: int, gold_units: int, observed_units: int) -> float:
    """Use observed units only when an empty gold corpus contains insertions."""

    denominator = gold_units if gold_units else observed_units
    return round(errors / denominator, 6) if denominator else 0.0


def _metrics(cases: list[Json], prediction_map: dict[str, Json]) -> Json:
    char_errors = char_total = observed_char_total = word_errors = word_total = (
        observed_word_total
    ) = 0
    math_errors = math_gold = math_observed = math_matches = 0
    question_exact = question_pages = question_gold = question_observed = question_matches = 0
    latency: list[float] = []
    memory: list[float] = []
    suggestions = manual = integrity_tp = integrity_fp = integrity_fn = integrity_tn = 0
    for case in cases:
        prediction = prediction_map[str(case["case_id"])]
        expected_chars = list(str(case["expected_text"]))
        observed_chars = list(str(prediction["observed_text"]))
        char_errors += _edit_distance(expected_chars, observed_chars)
        char_total += len(expected_chars)
        observed_char_total += len(observed_chars)
        expected_words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", str(case["expected_text"]).lower())
        observed_words = re.findall(
            r"[A-Za-z]+(?:'[A-Za-z]+)?", str(prediction["observed_text"]).lower()
        )
        word_errors += _edit_distance(expected_words, observed_words)
        word_total += len(expected_words)
        observed_word_total += len(observed_words)
        is_math_case = "math" in cast(list[str], case["content_tags"])
        expected_math = _math_tokens(str(case["expected_text"])) if is_math_case else []
        observed_math = _math_tokens(str(prediction["observed_text"])) if is_math_case else []
        distance = _edit_distance(expected_math, observed_math)
        math_errors += distance
        math_gold += len(expected_math)
        math_observed += len(observed_math)
        math_matches += _lcs_length(expected_math, observed_math)
        expected_questions = cast(list[str], case["expected_question_numbers"])
        observed_questions = cast(list[str], prediction["observed_question_numbers"])
        if expected_questions:
            question_pages += 1
            question_exact += expected_questions == observed_questions
        overlap = sum((Counter(expected_questions) & Counter(observed_questions)).values())
        question_matches += overlap
        question_gold += len(expected_questions)
        question_observed += len(observed_questions)
        latency.append(float(prediction["inference_ms"]))
        memory.append(float(prediction["peak_memory_mb"]))
        suggestions += int(prediction["suggestion_count"])
        manual += int(prediction["manual_required_count"])
        expected_reject = bool(case["expect_integrity_rejection"])
        actual_reject = bool(prediction["integrity_rejected"])
        integrity_tp += expected_reject and actual_reject
        integrity_fp += not expected_reject and actual_reject
        integrity_fn += expected_reject and not actual_reject
        integrity_tn += not expected_reject and not actual_reject
    tp, fp, fn, negative_fp = _region_counts(cases, prediction_map)
    sorted_latency = sorted(latency)

    def percentile(fraction: float) -> float:
        return sorted_latency[max(0, math.ceil(fraction * len(sorted_latency)) - 1)]

    return {
        "document_count": len({case["document_id"] for case in cases}),
        "page_count": len(cases),
        "cer": _insertion_aware_error_rate(char_errors, char_total, observed_char_total),
        "character_accuracy": round(
            max(
                0.0,
                1 - _insertion_aware_error_rate(char_errors, char_total, observed_char_total),
            ),
            6,
        ),
        "english_wer": _insertion_aware_error_rate(word_errors, word_total, observed_word_total),
        "math": {
            "precision": _supported_ratio(math_matches, math_observed),
            "recall": _supported_ratio(math_matches, math_gold),
            "f1": _supported_ratio(2 * math_matches, math_gold + math_observed),
            "token_edit_rate": _insertion_aware_error_rate(math_errors, math_gold, math_observed),
            "support": {
                "gold_token_count": math_gold,
                "observed_token_count": math_observed,
                "matched_token_count": math_matches,
            },
        },
        "question_numbers": {
            "exact_page_ratio": _supported_ratio(question_exact, question_pages),
            "anchor_precision": _supported_ratio(question_matches, question_observed),
            "anchor_recall": _supported_ratio(question_matches, question_gold),
            "support": {
                "judged_page_count": question_pages,
                "gold_anchor_count": question_gold,
                "observed_anchor_count": question_observed,
                "matched_anchor_count": question_matches,
            },
        },
        "regions": {
            "precision": _supported_ratio(tp, tp + fp),
            "recall": _supported_ratio(tp, tp + fn),
            "false_positives_per_negative_page": _supported_ratio(
                negative_fp, sum(not case["expected_regions"] for case in cases)
            ),
            "support": {
                "gold_region_count": tp + fn,
                "proposal_count": tp + fp,
                "matched_region_count": tp,
                "negative_page_count": sum(not case["expected_regions"] for case in cases),
            },
        },
        "performance": {
            "latency_ms_mean": round(statistics.fmean(latency), 6),
            "latency_ms_p50": percentile(0.50),
            "latency_ms_p95": percentile(0.95),
            "peak_memory_mb": max(memory),
        },
        "manual_required_ratio": _ratio(manual, suggestions, 0.0),
        "integrity": {
            "true_positive": integrity_tp,
            "false_positive": integrity_fp,
            "false_negative": integrity_fn,
            "true_negative": integrity_tn,
            "precision": _supported_ratio(integrity_tp, integrity_tp + integrity_fp),
            "recall": _supported_ratio(integrity_tp, integrity_tp + integrity_fn),
            "support": {
                "expected_positive_count": integrity_tp + integrity_fn,
                "predicted_positive_count": integrity_tp + integrity_fp,
                "page_count": len(cases),
            },
        },
    }


def evaluate(
    gold_raw: object, prediction_raw: object, attestation_raw: object, image_root: Path
) -> Json:
    gold = validate_gold(gold_raw)
    predictions = validate_predictions(prediction_raw, gold)
    attestation = validate_attestation(attestation_raw, gold, predictions)
    attestations = {
        str(row["document_id"]): row for row in cast(list[Json], attestation["documents"])
    }
    cases = [
        case
        for case in cast(list[Json], gold["cases"])
        if case["split"] == "test"
        and case["annotation_status"] != "unjudgeable"
        and attestations[str(case["document_id"])]["sample_origin"] == "real_deidentified"
        and attestations[str(case["document_id"])]["deidentified"]
        and attestations[str(case["document_id"])]["evaluation_use_authorized"]
        and attestations[str(case["document_id"])]["local_acquisition_authorized"]
    ]
    if not cases or {case["modality"] for case in cases} != set(MODALITIES):
        raise ValueError("real held-out evaluation must cover all four modalities")
    covered_degradations = {
        tag for case in cases for tag in cast(list[str], case["degradation_tags"])
    }
    covered_content = {tag for case in cases for tag in cast(list[str], case["content_tags"])}
    if covered_degradations != DEGRADATIONS:
        raise ValueError("real held-out evaluation must cover every degradation")
    if covered_content != CONTENT_TAGS:
        raise ValueError(
            "real held-out evaluation must cover Chinese, English, math, "
            "question, and negative cases"
        )
    required = {str(case["case_id"]) for case in cases}
    prediction_map = {str(case["case_id"]): case for case in cast(list[Json], predictions["cases"])}
    if set(prediction_map) != required:
        raise ValueError("predictions must exactly cover real held-out cases")
    _validate_images(image_root, cases)
    degradation_tags = sorted(
        {tag for case in cases for tag in cast(list[str], case["degradation_tags"])}
    )

    def slice_metrics(rows: list[Json]) -> Json:
        document_count = len({case["document_id"] for case in rows})
        if document_count < MIN_SLICE_DOCUMENTS:
            return {"document_count": document_count, "page_count": len(rows), "suppressed": True}
        return _metrics(rows, prediction_map)

    return {
        "schema_version": REPORT_VERSION,
        "status": "self_attested_evaluation_only",
        "self_attested_evaluation_complete": True,
        "eligible_for_pilot": False,
        "production_ready": False,
        "writes_product_data": False,
        "human_confirmation_required": True,
        "detector_identity": {"trusted_identity_verified": False},
        "blocker_codes": ["TRUSTED_ATTESTATION_REQUIRED"],
        "metrics": {
            "overall": slice_metrics(cases),
            "by_modality": {
                modality: slice_metrics([case for case in cases if case["modality"] == modality])
                for modality in MODALITIES
            },
            "by_degradation": {
                tag: slice_metrics([case for case in cases if tag in case["degradation_tags"]])
                for tag in degradation_tags
            },
        },
    }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def load_json(path: Path) -> object:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON number forbidden: {value}")

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=reject_constant,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("gold", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("attestation", type=Path)
    parser.add_argument("--image-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(
        load_json(args.gold),
        load_json(args.predictions),
        load_json(args.attestation),
        args.image_root,
    )
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
