from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image

from scripts.recognition_private_gold_prepare import (
    ANNOTATION_VERSION,
    DIAGNOSTIC_VERSION,
    DRAFT_VERSION,
    PRIVATE_PREDICTION_VERSION,
    draft_text_from_blocks,
    load_json,
    prepare_bundle,
)


def uid(number: int) -> str:
    return str(uuid.UUID(int=number))


def private_inputs(root: Path) -> tuple[dict[str, object], dict[str, object], Path]:
    images = root / "images"
    images.mkdir(parents=True)
    cases: list[dict[str, object]] = []
    entries: list[dict[str, object]] = []
    for index in range(12):
        case_id = uid(100 + index)
        modality = "photo" if index == 0 else "scan" if index < 4 else "text_pdf"
        role = "reference_answer" if index % 2 == 0 else "student_or_assignment_material"
        cases.append(
            {
                "case_id": case_id,
                "page_index": index % 2,
                "role": role,
                "modality": modality,
                "width": 64,
                "height": 48,
                "gold_text_available": modality == "text_pdf",
                "gold_text_chars": 40 if modality == "text_pdf" else 0,
            }
        )
        entries.append(
            {
                "case_id": case_id,
                "source_kind": "private_pdf",
                "source_ref": f"C:\\private\\student-name-{index // 2}.pdf",
                "page_index": index % 2,
            }
        )
        Image.new("RGB", (64, 48), "white").save(images / f"{case_id}.png")
    return (
        {"schema_version": DIAGNOSTIC_VERSION, "cases": cases},
        {"private": True, "entries": entries},
        images,
    )


def draft_predictions(diagnostic: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": PRIVATE_PREDICTION_VERSION,
        "provider": {"private": "ignored"},
        "results": [
            {
                "case_id": case["case_id"],
                "status": "ok",
                "runtime_ms": 1,
                "blocks": [
                    {
                        "text": f"OCR draft {index}",
                        "confidence": 0.5,
                        "region": [0, 0, 1, 1],
                        "status": "manual_required",
                    }
                ],
            }
            for index, case in enumerate(diagnostic["cases"])  # type: ignore[index]
        ],
    }


def block(text: str, x: float, y: float, width: float, height: float) -> dict[str, object]:
    return {
        "text": text,
        "confidence": 0.8,
        "region": [x, y, width, height],
        "status": "recognized",
    }


def test_draft_text_reconstructs_visual_lines_deterministically() -> None:
    blocks = [
        block("7", 0.48, 0.9, 0.02, 0.04),
        block("连续", 0.29, 0.1, 0.06, 0.05),
        block("9.1", 0.01, 0.1, 0.05, 0.05),
        block("其", 0.26, 0.1, 0.02, 0.05),
        block("函数", 0.17, 0.1, 0.06, 0.05),
        block("多", 0.1, 0.1, 0.02, 0.05),
        block("及", 0.235, 0.1, 0.02, 0.05),
        block("性", 0.36, 0.1, 0.02, 0.05),
        block("变量", 0.125, 0.1, 0.04, 0.05),
    ]
    expected = "9.1 多变量函数及其连续性\n7"
    assert draft_text_from_blocks(blocks) == expected
    assert draft_text_from_blocks(list(reversed(blocks))) == expected


def test_draft_text_preserves_latin_word_boundaries_and_math_scripts() -> None:
    blocks = [
        block("Find", 0.1, 0.3, 0.08, 0.05),
        block("limit", 0.185, 0.3, 0.08, 0.05),
        block("x", 0.28, 0.3, 0.02, 0.05),
        block("2", 0.301, 0.28, 0.01, 0.02),
    ]
    assert draft_text_from_blocks(blocks) == "Find limit x 2"


def test_prepare_bundle_is_anonymous_stratified_and_repository_external(tmp_path: Path) -> None:
    diagnostic, source_map, images = private_inputs(tmp_path)
    output = tmp_path / "annotation"
    summary = prepare_bundle(
        diagnostic,
        source_map,
        images,
        output,
        sample_size=8,
        max_pages_per_document=2,
        scan_target=2,
        photo_target=1,
        reference_target=4,
        seed="stable-test-seed",
        dataset_id=uid(1),
        draft_predictions_raw=draft_predictions(diagnostic),
    )

    seed = json.loads((output / "annotation-seed.json").read_text(encoding="utf-8"))
    private_map = json.loads((output / "private-document-map.json").read_text(encoding="utf-8"))
    drafts = json.loads((output / "ocr-drafts.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == ANNOTATION_VERSION
    assert summary["selected_page_count"] == 8
    assert summary["selected_document_count"] >= 4
    assert summary["modality_counts"]["photo"] == 1
    assert summary["modality_counts"]["scan"] >= 2
    assert summary["role_counts"] == {
        "reference_answer": 4,
        "student_or_assignment_material": 4,
    }
    assert summary["annotation_complete"] is False
    assert summary["accuracy_claim"] is False
    assert summary["writes_product_data"] is False
    assert summary["draft_page_count"] == 8
    assert seed["schema_version"] == ANNOTATION_VERSION
    assert len(seed["cases"]) == 8
    assert all(case["annotation_status"] == "pending" for case in seed["cases"])
    assert all(case["privacy_status"] == "pending" for case in seed["cases"])
    assert all(case["split"] == "test" for case in seed["cases"])
    assert max(Counter(case["document_id"] for case in seed["cases"]).values()) <= 2
    assert {path.name for path in (output / "images").iterdir()} == {
        case["image_file"] for case in seed["cases"]
    }
    public_text = json.dumps(seed, ensure_ascii=False)
    assert "student-name" not in public_text
    assert "C:\\private" not in public_text
    assert private_map["private"] is True
    assert "student-name" in json.dumps(private_map)
    assert drafts["schema_version"] == DRAFT_VERSION
    assert drafts["private"] is True
    assert len(drafts["cases"]) == 8
    assert all(set(row) == {"case_id", "draft_text"} for row in drafts["cases"])
    assert "provider" not in json.dumps(drafts)


def test_prepare_rejects_source_map_mismatch_and_unsafe_images(tmp_path: Path) -> None:
    diagnostic, source_map, images = private_inputs(tmp_path)
    source_map["entries"] = source_map["entries"][:-1]  # type: ignore[index]
    with pytest.raises(ValueError, match="exactly cover"):
        prepare_bundle(
            diagnostic,
            source_map,
            images,
            tmp_path / "mismatch",
            sample_size=4,
            scan_target=1,
            photo_target=1,
            reference_target=2,
        )

    diagnostic, source_map, images = private_inputs(tmp_path / "second")
    selected = diagnostic["cases"][0]  # type: ignore[index]
    (images / f"{selected['case_id']}.png").write_bytes(b"not a png")
    with pytest.raises(OSError):
        prepare_bundle(
            diagnostic,
            source_map,
            images,
            tmp_path / "invalid-image",
            sample_size=12,
            max_pages_per_document=2,
            scan_target=3,
            photo_target=1,
            reference_target=6,
        )

    diagnostic, source_map, images = private_inputs(tmp_path / "drafts")
    incomplete_drafts = draft_predictions(diagnostic)
    incomplete_drafts["results"] = incomplete_drafts["results"][:-1]  # type: ignore[index]
    with pytest.raises(ValueError, match="exactly cover selected cases"):
        prepare_bundle(
            diagnostic,
            source_map,
            images,
            tmp_path / "missing-draft",
            sample_size=12,
            max_pages_per_document=2,
            scan_target=3,
            photo_target=1,
            reference_target=6,
            draft_predictions_raw=incomplete_drafts,
        )


def test_prepare_refuses_nonempty_output_and_duplicate_json_keys(tmp_path: Path) -> None:
    diagnostic, source_map, images = private_inputs(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(ValueError, match="must not already exist"):
        prepare_bundle(
            diagnostic,
            source_map,
            images,
            output,
            sample_size=4,
            max_pages_per_document=2,
            scan_target=1,
            photo_target=1,
            reference_target=2,
        )
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_json(duplicate)
