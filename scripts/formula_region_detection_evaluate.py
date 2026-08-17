"""Strict, offline evaluation for sanitized formula-region boxes only."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

DATASET_VERSION = "formula-region-detection-v1"
PREDICTION_VERSION = "formula-region-predictions-v1"
REPORT_VERSION = "formula-region-detection-report-v1"
IOU_THRESHOLD = 0.5
DUPLICATE_THRESHOLD = 0.9
STRUCTURE_THRESHOLD = 0.1
MODALITIES = {"text_pdf", "scan", "photo", "synthetic"}
SPLITS = {"train", "dev", "test"}
KINDS = {"inline", "display", "multiline", "matrix", "unknown"}
STYLES = {"printed", "handwritten", "mixed", "unknown"}
PAGE_STATUSES = {"annotated", "no_formula", "unjudgeable"}
REGION_STATUSES = {"confirmed", "ignored"}
QUALITY_FLAGS = {
    "none",
    "blurred",
    "faint",
    "perspective",
    "ruled_paper",
    "overwritten",
    "occluded",
    "cropped",
    "low_resolution",
}
NEGATIVE_TAGS = {
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
}
PRIVATE_KEY = re.compile(
    r"path|file.?name|student|teacher|person.?name|annotator.?name|class.?id|"
    r"assignment.?id|database.?id|source_hash|pdf_hash|original_hash|email",
    re.I,
)
ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|file://|^/)")
Box = tuple[float, float, float, float]
Json = dict[str, Any]


def _obj(value: object, label: str) -> Json:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(Json, value)


def _keys(value: Json, required: set[str], optional: set[str], label: str) -> None:
    if missing := required - value.keys():
        raise ValueError(f"{label} missing fields: {sorted(missing)}")
    if unknown := value.keys() - required - optional:
        raise ValueError(f"{label} unknown fields: {sorted(unknown)}")


def _uuid(value: object, label: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _enum(value: object, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} must be one of {sorted(allowed)}")
    return value


def _flags(value: object, allowed: set[str], label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    if len(value) != len(set(value)) or set(value) - allowed:
        raise ValueError(f"{label} contains duplicate or unknown values")
    if "none" in value and len(value) != 1:
        raise ValueError(f"{label} cannot combine none with another value")
    return cast(list[str], value)


def _box(value: object, label: str) -> Box:
    raw = _obj(value, label)
    _keys(raw, {"x", "y", "width", "height"}, set(), label)
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


def _privacy(value: object, label: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if PRIVATE_KEY.search(str(key)):
                raise ValueError(f"private field forbidden at {label}.{key}")
            _privacy(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _privacy(item, f"{label}[{index}]")
    elif isinstance(value, str) and ABSOLUTE_PATH.search(value):
        raise ValueError(f"absolute path forbidden at {label}")


def iou(left: Box, right: Box) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    area = max(0.0, min(lx + lw, rx + rw) - max(lx, rx)) * max(
        0.0, min(ly + lh, ry + rh) - max(ly, ry)
    )
    union = lw * lh + rw * rh - area
    return area / union if union else 0.0


def _intersection(left: Box, right: Box) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    return max(0.0, min(lx + lw, rx + rw) - max(lx, rx)) * max(
        0.0, min(ly + lh, ry + rh) - max(ly, ry)
    )


def _item_box(item: Json) -> Box:
    return _box(item["bbox"], "bbox")


def _coverage(gt: Box, predictions: list[Box]) -> float:
    # Rectangle union through x slabs, after clipping to the ground truth.
    gx, gy, gw, gh = gt
    clips: list[Box] = []
    for px, py, pw, ph in predictions:
        left, top = max(gx, px), max(gy, py)
        right, bottom = min(gx + gw, px + pw), min(gy + gh, py + ph)
        if right > left and bottom > top:
            clips.append((left, top, right - left, bottom - top))
    xs = sorted({point for box in clips for point in (box[0], box[0] + box[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:], strict=False):
        intervals = sorted(
            (y, y + height) for x, y, width, height in clips if x < right and x + width > left
        )
        covered = 0.0
        if intervals:
            start, end = intervals[0]
            for next_start, next_end in intervals[1:]:
                if next_start > end:
                    covered += end - start
                    start, end = next_start, next_end
                else:
                    end = max(end, next_end)
            covered += end - start
        area += (right - left) * covered
    return min(1.0, area / (gw * gh)) if clips else 0.0


def validate_dataset(raw: object) -> Json:
    _privacy(raw)
    data = _obj(raw, "dataset")
    _keys(
        data,
        {"schema_version", "dataset_id", "annotator_decision_version", "cases"},
        set(),
        "dataset",
    )
    if data["schema_version"] != DATASET_VERSION:
        raise ValueError(f"schema_version must be {DATASET_VERSION}")
    _uuid(data["dataset_id"], "dataset_id")
    version = data["annotator_decision_version"]
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", version):
        raise ValueError("annotator_decision_version is invalid")
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    case_ids: set[str] = set()
    doc_splits: dict[str, str] = {}
    for ci, item in enumerate(cases):
        label = f"cases[{ci}]"
        case = _obj(item, label)
        required = {
            "case_id",
            "document_id",
            "split",
            "modality",
            "page_width",
            "page_height",
            "contains_formula",
            "regions",
            "quality_flags",
            "negative_tags",
            "annotation_status",
            "annotator_decision_version",
        }
        _keys(case, required, set(), label)
        case_id, doc_id = _uuid(case["case_id"], label), _uuid(case["document_id"], label)
        if case_id in case_ids:
            raise ValueError("case_id must be unique")
        case_ids.add(case_id)
        split = _enum(case["split"], SPLITS, f"{label}.split")
        if doc_id in doc_splits and doc_splits[doc_id] != split:
            raise ValueError("train/dev/test split must be isolated by document_id")
        doc_splits[doc_id] = split
        _enum(case["modality"], MODALITIES, f"{label}.modality")
        for key in ("page_width", "page_height"):
            if isinstance(case[key], bool) or not isinstance(case[key], int) or case[key] <= 0:
                raise ValueError(f"{label}.{key} must be positive")
        if (
            not isinstance(case["contains_formula"], bool)
            or case["annotator_decision_version"] != version
        ):
            raise ValueError(f"{label} has invalid decision metadata")
        _flags(case["quality_flags"], QUALITY_FLAGS, f"{label}.quality_flags")
        _flags(case["negative_tags"], NEGATIVE_TAGS, f"{label}.negative_tags")
        status = _enum(case["annotation_status"], PAGE_STATUSES, f"{label}.annotation_status")
        regions = case["regions"]
        if not isinstance(regions, list):
            raise ValueError(f"{label}.regions must be a list")
        boxes: list[Box] = []
        region_ids: set[str] = set()
        confirmed = 0
        for ri, item in enumerate(regions):
            rlabel = f"{label}.regions[{ri}]"
            region = _obj(item, rlabel)
            _keys(
                region,
                {"region_id", "bbox", "kind", "print_style", "quality_flags", "annotation_status"},
                set(),
                rlabel,
            )
            region_id = _uuid(region["region_id"], rlabel)
            if region_id in region_ids:
                raise ValueError("region_id must be unique within a page")
            region_ids.add(region_id)
            box = _box(region["bbox"], f"{rlabel}.bbox")
            if any(iou(box, previous) >= DUPLICATE_THRESHOLD for previous in boxes):
                raise ValueError(f"duplicate formula region at {rlabel}")
            boxes.append(box)
            _enum(region["kind"], KINDS, f"{rlabel}.kind")
            _enum(region["print_style"], STYLES, f"{rlabel}.print_style")
            _flags(region["quality_flags"], QUALITY_FLAGS, f"{rlabel}.quality_flags")
            confirmed += (
                _enum(region["annotation_status"], REGION_STATUSES, f"{rlabel}.annotation_status")
                == "confirmed"
            )
        if status == "unjudgeable" and regions:
            raise ValueError("unjudgeable pages must have no regions")
        if status == "no_formula" and (regions or case["contains_formula"]):
            raise ValueError("no_formula pages must be empty")
        if status == "annotated" and (not regions or case["contains_formula"] != bool(confirmed)):
            raise ValueError("annotated pages need regions and contains_formula must reflect them")
    return data


def validate_predictions(raw: object, dataset: Json) -> Json:
    _privacy(raw)
    data = _obj(raw, "predictions")
    _keys(data, {"schema_version", "detector", "cases"}, set(), "predictions")
    if data["schema_version"] != PREDICTION_VERSION:
        raise ValueError(f"schema_version must be {PREDICTION_VERSION}")
    detector = _obj(data["detector"], "detector")
    _keys(detector, {"name", "version"}, set(), "detector")
    if not all(isinstance(detector[key], str) and detector[key] for key in detector):
        raise ValueError("detector fields must be non-empty strings")
    known = {case["case_id"] for case in cast(list[Json], dataset["cases"])}
    cases = data["cases"]
    if not isinstance(cases, list):
        raise ValueError("prediction cases must be a list")
    seen: set[str] = set()
    for ci, item in enumerate(cases):
        label = f"prediction cases[{ci}]"
        case = _obj(item, label)
        _keys(case, {"case_id", "proposals", "inference_ms"}, {"peak_memory_mb"}, label)
        case_id = _uuid(case["case_id"], label)
        if case_id not in known:
            raise ValueError(f"unknown page: {case_id}")
        if case_id in seen:
            raise ValueError("prediction case_id must be unique")
        seen.add(case_id)
        for key in ("inference_ms", "peak_memory_mb"):
            value = case.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            ):
                raise ValueError(f"{label}.{key} must be non-negative")
        proposals = case["proposals"]
        if not isinstance(proposals, list):
            raise ValueError(f"{label}.proposals must be a list")
        ids: set[str] = set()
        for pi, item in enumerate(proposals):
            plabel = f"{label}.proposals[{pi}]"
            proposal = _obj(item, plabel)
            _keys(proposal, {"proposal_id", "bbox", "score", "detection_source"}, set(), plabel)
            proposal_id = _uuid(proposal["proposal_id"], plabel)
            if proposal_id in ids:
                raise ValueError("proposal_id must be unique within a page")
            ids.add(proposal_id)
            _box(proposal["bbox"], f"{plabel}.bbox")
            if (
                isinstance(proposal["score"], bool)
                or not isinstance(proposal["score"], (int, float))
                or not 0 <= proposal["score"] <= 1
            ):
                raise ValueError(f"{plabel}.score must be within 0..1")
            if (
                not isinstance(proposal["detection_source"], str)
                or not proposal["detection_source"]
            ):
                raise ValueError(f"{plabel}.detection_source is invalid")
    return data


def _divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _region_field_equals(field: str, value: str) -> Callable[[Json], bool]:
    def matches(region: Json) -> bool:
        return str(region[field]) == value

    return matches


def _metrics(
    cases: list[Json],
    prediction_map: dict[str, Json],
    region_filter: Callable[[Json], bool] | None = None,
) -> Json:
    tp = fp = fn = duplicates = fragmentation = merges = proposal_count = 0
    coverages: list[float] = []
    runtimes: list[float] = []
    memories: list[float] = []
    rows: list[Json] = []
    for case in cases:
        if case["annotation_status"] == "unjudgeable":
            continue
        prediction = prediction_map.get(case["case_id"], {"proposals": [], "inference_ms": 0})
        regions = cast(list[Json], case["regions"])
        eligible = [
            r
            for r in regions
            if r["annotation_status"] == "confirmed" and (region_filter is None or region_filter(r))
        ]
        excluded = [r for r in regions if r not in eligible]
        proposals = [
            p
            for p in cast(list[Json], prediction["proposals"])
            if not any(iou(_item_box(p), _item_box(r)) >= IOU_THRESHOLD for r in excluded)
        ]
        pairs = sorted(
            (-iou(_item_box(r), _item_box(p)), str(r["region_id"]), str(p["proposal_id"]), gi, pi)
            for gi, r in enumerate(eligible)
            for pi, p in enumerate(proposals)
            if iou(_item_box(r), _item_box(p)) >= IOU_THRESHOLD
        )
        matched_gt: set[int] = set()
        matched_pred: set[int] = set()
        matches: list[Json] = []
        for negative_iou, region_id, proposal_id, gi, pi in pairs:
            if gi not in matched_gt and pi not in matched_pred:
                matched_gt.add(gi)
                matched_pred.add(pi)
                matches.append(
                    {
                        "region_id": region_id,
                        "proposal_id": proposal_id,
                        "iou": round(-negative_iou, 6),
                    }
                )
        page_duplicates = sum(
            iou(_item_box(left), _item_box(right)) >= DUPLICATE_THRESHOLD
            for index, left in enumerate(proposals)
            for right in proposals[index + 1 :]
        )
        page_fragments = sum(
            sum(
                _intersection(_item_box(r), _item_box(p)) / (_item_box(r)[2] * _item_box(r)[3])
                >= STRUCTURE_THRESHOLD
                for p in proposals
            )
            >= 2
            for r in eligible
        )
        page_merges = sum(
            sum(
                _intersection(_item_box(r), _item_box(p)) / (_item_box(r)[2] * _item_box(r)[3])
                >= STRUCTURE_THRESHOLD
                for r in eligible
            )
            >= 2
            for p in proposals
        )
        page_coverages = [
            _coverage(_item_box(r), [_item_box(p) for p in proposals]) for r in eligible
        ]
        page_tp, page_fp, page_fn = (
            len(matches),
            len(proposals) - len(matches),
            len(eligible) - len(matches),
        )
        tp += page_tp
        fp += page_fp
        fn += page_fn
        duplicates += page_duplicates
        fragmentation += page_fragments
        merges += page_merges
        proposal_count += len(proposals)
        coverages.extend(page_coverages)
        runtimes.append(float(prediction["inference_ms"]))
        if prediction.get("peak_memory_mb") is not None:
            memories.append(float(prediction["peak_memory_mb"]))
        rows.append(
            {
                "case_id": case["case_id"],
                "ground_truth_count": len(eligible),
                "proposal_count": len(proposals),
                "matches": matches,
                "false_positives": page_fp,
                "missed_regions": page_fn,
                "duplicate_boxes": page_duplicates,
                "fragmentation": page_fragments,
                "merge_errors": page_merges,
                "formula_coverage": round(statistics.fmean(page_coverages), 6)
                if page_coverages
                else 1.0,
            }
        )
    precision, recall = _divide(tp, tp + fp), _divide(tp, tp + fn)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    page_count, gt_count = len(rows), tp + fn
    teacher_ops = 0.25 * tp + fp + fn + duplicates + fragmentation + merges
    return {
        "page_count": page_count,
        "ground_truth_count": gt_count,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "true_positives": tp,
        "false_positives": fp,
        "false_positives_per_page": round(fp / page_count, 6) if page_count else 0.0,
        "missed_regions": fn,
        "duplicate_boxes": duplicates,
        "fragmentation": fragmentation,
        "merge_errors": merges,
        "formula_coverage": round(statistics.fmean(coverages), 6) if coverages else 1.0,
        "proposal_count": proposal_count,
        "proposals_per_page": round(proposal_count / page_count, 6) if page_count else 0.0,
        "mean_inference_ms_per_page": round(statistics.fmean(runtimes), 6) if runtimes else 0.0,
        "peak_memory_mb": round(max(memories), 6) if memories else None,
        "manual_workload_proxy": {
            "manual_drawing_baseline_operations": gt_count,
            "estimated_teacher_operations": round(teacher_ops, 6),
            "estimated_operations_saved": round(gt_count - teacher_ops, 6),
        },
        "cases": rows,
    }


def evaluate(dataset_raw: object, prediction_raw: object) -> Json:
    dataset = validate_dataset(dataset_raw)
    predictions = validate_predictions(prediction_raw, dataset)
    cases = cast(list[Json], dataset["cases"])
    prediction_map = {case["case_id"]: case for case in cast(list[Json], predictions["cases"])}
    judged = [case for case in cases if case["annotation_status"] != "unjudgeable"]
    negative = [
        case
        for case in judged
        if not any(r["annotation_status"] == "confirmed" for r in case["regions"])
    ]
    return {
        "schema_version": REPORT_VERSION,
        "dataset_schema_version": DATASET_VERSION,
        "prediction_schema_version": PREDICTION_VERSION,
        "detector": predictions["detector"],
        "matching_policy": {
            "algorithm": "greedy descending IoU, then region_id, then proposal_id",
            "iou_threshold": IOU_THRESHOLD,
            "many_predictions_to_one_ground_truth": (
                "one match; remaining predictions are false positives"
            ),
            "one_prediction_to_many_ground_truth": "one match; remaining ground truths are misses",
            "ignored_annotations": "predictions at IoU>=0.5 are excluded",
            "unjudgeable_pages": "excluded",
            "empty_page_convention": "zero-denominator precision or recall is 1",
        },
        "manual_workload_policy": {
            "confirmed_proposal": 0.25,
            "false_positive_delete": 1.0,
            "missed_region_draw": 1.0,
            "structure_fix": 1.0,
            "scope": "comparison proxy, not measured teacher time",
        },
        "production_ready": False,
        "human_confirmation_required": True,
        "writes_product_data": False,
        "metrics": {
            "overall": _metrics(judged, prediction_map),
            "by_modality": {
                value: _metrics(
                    [case for case in judged if case["modality"] == value], prediction_map
                )
                for value in sorted(MODALITIES)
            },
            "by_print_style": {
                value: _metrics(judged, prediction_map, _region_field_equals("print_style", value))
                for value in sorted(STYLES)
            },
            "by_region_kind": {
                value: _metrics(judged, prediction_map, _region_field_equals("kind", value))
                for value in sorted(KINDS)
            },
            "negative_pages": _metrics(negative, prediction_map),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(
        json.loads(args.dataset.read_text(encoding="utf-8")),
        json.loads(args.predictions.read_text(encoding="utf-8")),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
