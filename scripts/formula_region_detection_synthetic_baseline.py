"""Connected-component proposal baseline for synthetic smoke tests only."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, cast

from PIL import Image

from scripts.formula_region_detection_evaluate import validate_dataset


def _components(image: Image.Image) -> list[tuple[int, int, int, int]]:
    gray = image.convert("L")
    width, height = gray.size
    dark = {
        (index % width, index // width) for index, value in enumerate(gray.tobytes()) if value < 100
    }
    boxes: list[tuple[int, int, int, int]] = []
    while dark:
        first = dark.pop()
        queue = deque([first])
        min_x = max_x = first[0]
        min_y = max_y = first[1]
        while queue:
            x, y = queue.popleft()
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if point in dark:
                    dark.remove(point)
                    queue.append(point)
        box_width, box_height = max_x - min_x + 1, max_y - min_y + 1
        if (
            box_width / width >= 0.12
            and box_height / height >= 0.015
            and box_width / box_height <= 30
        ):
            boxes.append((min_x, min_y, box_width, box_height))
    return sorted(boxes, key=lambda item: (item[1], item[0], item[2], item[3]))


def run_baseline(dataset_raw: object, image_dir: Path) -> dict[str, Any]:
    dataset = validate_dataset(dataset_raw)
    cases = cast(list[dict[str, Any]], dataset["cases"])
    if any(case["modality"] != "synthetic" for case in cases):
        raise ValueError("synthetic baseline accepts only synthetic cases")
    expected = {f"{case['case_id']}.png" for case in cases}
    paths = list(image_dir.iterdir())
    if {path.name for path in paths if path.is_file()} != expected or any(
        path.is_symlink() for path in paths
    ):
        raise ValueError("image directory must contain exactly one non-symlink PNG per case")
    output_cases: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        with Image.open(image_dir / f"{case['case_id']}.png") as image:
            image.load()
            if image.format != "PNG" or image.size != (case["page_width"], case["page_height"]):
                raise ValueError("synthetic image format or dimensions do not match manifest")
            width, height = image.size
            boxes = _components(image)
        output_cases.append(
            {
                "case_id": case["case_id"],
                "proposals": [
                    {
                        "proposal_id": str(
                            uuid.uuid5(uuid.NAMESPACE_URL, f"{case['case_id']}:{box}")
                        ),
                        "bbox": {
                            "x": round(box[0] / width, 6),
                            "y": round(box[1] / height, 6),
                            "width": round(box[2] / width, 6),
                            "height": round(box[3] / height, 6),
                        },
                        "score": 0.5,
                        "detection_source": "synthetic-connected-component-v1",
                    }
                    for box in boxes
                ],
                "inference_ms": round((time.perf_counter() - started) * 1000, 6),
            }
        )
    return {
        "schema_version": "formula-region-predictions-v1",
        "detector": {"name": "synthetic-connected-component", "version": "1"},
        "cases": output_cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("image_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_baseline(json.loads(args.dataset.read_text(encoding="utf-8")), args.image_dir)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
