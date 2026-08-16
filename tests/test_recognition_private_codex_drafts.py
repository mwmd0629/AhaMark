from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from scripts.recognition_private_codex_drafts import (
    DRAFT_VERSION,
    generate_drafts,
    validate_result,
)


def uid(number: int) -> str:
    return str(uuid.UUID(int=number))


def private_seed(root: Path, count: int = 3) -> tuple[Path, Path, str]:
    image_root = root / "private-images"
    image_root.mkdir(parents=True)
    dataset_id = uid(1)
    cases = []
    for index in range(count):
        case_id = uid(100 + index)
        relative = f"images/{case_id}.png"
        image = image_root / relative
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"synthetic-png-placeholder")
        cases.append({"case_id": case_id, "image_file": relative})
    seed = root / "annotation-seed.json"
    seed.write_text(
        json.dumps(
            {
                "schema_version": "recognition-private-annotation-v1",
                "dataset_id": dataset_id,
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )
    return seed, image_root, dataset_id


def result_for(image: Path) -> dict[str, object]:
    return {
        "draft_text": f"draft-{image.stem}",
        "question_numbers": ["1"],
        "formula_drafts": [
            {
                "linear_text": "x^2",
                "latex": "x^2",
                "location_hint": "第一行",
                "uncertain": False,
            }
        ],
        "uncertainties": [],
        "manual_review_required": True,
    }


def test_generate_drafts_checkpoints_and_resumes_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    seed, image_root, dataset_id = private_seed(private)
    output = private / "codex-drafts.json"
    calls: list[str] = []

    def runner(image: Path, schema: Path, result: Path, workdir: Path) -> dict[str, object]:
        assert schema.is_file()
        assert workdir.is_dir()
        assert not result.exists()
        calls.append(image.stem)
        return result_for(image)

    first = generate_drafts(
        seed_path=seed,
        image_root=image_root,
        output=output,
        repository_root=repository,
        runner=runner,
        limit=2,
    )
    assert first == {"completed": 2, "remaining": 1, "formula_drafts": 2, "uncertainties": 0}
    checkpoint = json.loads(output.read_text(encoding="utf-8"))
    assert checkpoint["schema_version"] == DRAFT_VERSION
    assert checkpoint["private"] is True
    assert checkpoint["dataset_id"] == dataset_id
    assert len(checkpoint["cases"]) == 2
    assert all(row["manual_review_required"] is True for row in checkpoint["cases"])

    second = generate_drafts(
        seed_path=seed,
        image_root=image_root,
        output=output,
        repository_root=repository,
        runner=runner,
    )
    assert second == {"completed": 3, "remaining": 0, "formula_drafts": 3, "uncertainties": 0}
    assert len(calls) == 3
    assert len(json.loads(output.read_text(encoding="utf-8"))["cases"]) == 3


def test_generate_drafts_rejects_private_output_inside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    seed, image_root, _ = private_seed(private, count=1)

    with pytest.raises(ValueError, match="private output must stay outside"):
        generate_drafts(
            seed_path=seed,
            image_root=image_root,
            output=repository / "drafts.json",
            repository_root=repository,
            runner=lambda image, schema, result, workdir: result_for(image),
        )


def test_validate_result_rejects_automatic_review_and_unknown_fields() -> None:
    payload = result_for(Path(f"{uid(100)}.png"))
    payload["manual_review_required"] = False
    with pytest.raises(ValueError, match="must require manual review"):
        validate_result(payload)

    payload = result_for(Path(f"{uid(100)}.png"))
    payload["confidence"] = 0.99
    with pytest.raises(ValueError, match="unexpected fields"):
        validate_result(payload)


def test_generate_drafts_never_calls_runner_for_completed_checkpoint(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    seed, image_root, _ = private_seed(private, count=1)
    output = private / "codex-drafts.json"

    generate_drafts(
        seed_path=seed,
        image_root=image_root,
        output=output,
        repository_root=repository,
        runner=lambda image, schema, result, workdir: result_for(image),
    )

    def unexpected_runner(
        image: Path, schema: Path, result: Path, workdir: Path
    ) -> dict[str, object]:
        raise AssertionError("completed page was sent to Codex again")

    summary = generate_drafts(
        seed_path=seed,
        image_root=image_root,
        output=output,
        repository_root=repository,
        runner=unexpected_runner,
    )
    assert summary["completed"] == 1
    assert summary["remaining"] == 0
