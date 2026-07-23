"""Standalone real RapidOCR staircase using runtime-generated printed synthetic pages."""

import argparse
import io
import json
import resource
import statistics
import time
from pathlib import Path

from PIL import Image, ImageDraw
from rapidocr import RapidOCR


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def synthetic_page(index: int) -> bytes:
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    for row in range(24):
        draw.text(
            (80, 70 + row * 65),
            f"SYNTHETIC CAPACITY PAGE {index:03d} ROW {row:02d} SCORE {row + index}",
            fill="black",
        )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stages", default="25,50")
    parser.add_argument("--output", default="docs/ocr-capacity-results.json")
    args = parser.parse_args()
    stages = [int(item) for item in args.stages.split(",")]
    engine = RapidOCR()
    page = synthetic_page(1)
    results = []
    for pages in stages:
        elapsed: list[float] = []
        success = 0
        blank = 0
        started = time.perf_counter()
        cpu_started = time.process_time()
        for _index in range(pages):
            page_started = time.perf_counter()
            result = engine(page)
            elapsed.append((time.perf_counter() - page_started) * 1000)
            texts = getattr(result, "txts", None) or []
            if texts:
                success += 1
            else:
                blank += 1
        results.append(
            {
                "pages": pages,
                "files": pages,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cpu_seconds": round(time.process_time() - cpu_started, 3),
                "page_p50_ms": round(statistics.median(elapsed), 2),
                "page_p95_ms": round(percentile(elapsed, 0.95), 2),
                "successful_pages": success,
                "blank_pages": blank,
                "failed_pages": pages - success - blank,
                "worker_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
        )
        print(json.dumps(results[-1]))
    evidence = {
        "schema_version": 1,
        "result": "passed" if all(row["failed_pages"] == 0 for row in results) else "failed",
        "scope": (
            "standalone real RapidOCR staircase on clear printed synthetic PNG pages; "
            "not workflow orchestration and not an accuracy claim"
        ),
        "provider": "rapidocr",
        "results": results,
    }
    Path(args.output).write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
