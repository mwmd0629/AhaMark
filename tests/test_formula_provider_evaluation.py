from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest
from app.recognition.formula import FormulaCandidate, FormulaRegionArtifact
from PIL import Image

from scripts.formula_ocr_provider_evaluate import (
    evaluate_provider,
    validate_provider_dataset,
)


class StubProvider:
    name = "stub"
    version = "1"

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def recognize(self, artifact: FormulaRegionArtifact) -> list[FormulaCandidate]:
        assert artifact.region == (0.0, 0.0, 1.0, 1.0)
        if artifact.region_kind == "inline":
            return [FormulaCandidate("x^2", 0.95, self.name, self.version)]
        return [
            FormulaCandidate(
                "x^3",
                None,
                self.name,
                self.version,
                ("UNCALIBRATED_CONFIDENCE", "TEACHER_REVIEW_REQUIRED"),
            )
        ]


def dataset() -> dict[str, object]:
    return {
        "schema_version": "formula-ocr-provider-eval-v1",
        "cases": [
            {
                "id": "text-pdf-001",
                "modality": "text_pdf",
                "expected_latex": "x^2",
                "region_kind": "inline",
            },
            {
                "id": "scan-001",
                "modality": "scan",
                "expected_latex": "x^2",
                "region_kind": "display",
            },
        ],
    }


def write_png(path: Path) -> None:
    output = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(output, "PNG")
    path.write_bytes(output.getvalue())


def test_provider_evaluation_reports_metrics_by_modality_without_paths(
    tmp_path: Path,
) -> None:
    write_png(tmp_path / "text-pdf-001.png")
    write_png(tmp_path / "scan-001.png")
    report = evaluate_provider(dataset(), tmp_path, StubProvider())
    assert report["production_ready"] is False
    assert report["human_confirmation_required"] is True
    assert report["metrics"]["normalized_exact_match_rate"] == pytest.approx(0.5)
    assert report["metrics"]["by_modality"]["text_pdf"] == {
        "total_cases": 1,
        "normalized_exact_match_rate": 1.0,
        "mean_token_similarity": 1.0,
        "manual_review_rate": 0.0,
    }
    assert report["metrics"]["by_modality"]["scan"]["manual_review_rate"] == 1.0
    assert "path" not in str(report).lower()


def test_provider_evaluation_rejects_private_fields_and_directory_drift(
    tmp_path: Path,
) -> None:
    private = dataset()
    private["cases"][0]["student_name"] = "private"  # type: ignore[index]
    with pytest.raises(ValueError, match="fields"):
        validate_provider_dataset(private)

    write_png(tmp_path / "text-pdf-001.png")
    write_png(tmp_path / "scan-001.png")
    write_png(tmp_path / "unexpected.png")
    with pytest.raises(ValueError, match="exactly one"):
        evaluate_provider(dataset(), tmp_path, StubProvider())


def test_provider_evaluation_script_can_load_from_direct_entrypoint() -> None:
    root = Path(__file__).parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "formula_ocr_provider_evaluate.py"), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "image_dir" in completed.stdout
