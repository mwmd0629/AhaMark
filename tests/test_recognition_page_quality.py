import io
import math
from dataclasses import asdict, replace

import pytest
from app.recognition.page_quality import (
    QUALITY_ALGORITHM_VERSION,
    assess_page_quality,
    measure_page_quality,
)
from app.recognition.pipeline import PageArtifact
from PIL import Image, ImageDraw, ImageFilter


def _artifact(image: Image.Image) -> PageArtifact:
    output = io.BytesIO()
    image.convert("RGB").save(output, "PNG")
    return PageArtifact(output.getvalue(), image.width, image.height)


def _text_page(
    *, width: int = 1200, height: int = 1600, foreground: int = 20, background: int = 255
) -> Image.Image:
    image = Image.new("L", (width, height), background)
    draw = ImageDraw.Draw(image)
    for row in range(10):
        y = 160 + row * 105
        draw.rectangle((120, y, 1000, y + 14), fill=foreground)
        draw.rectangle((120, y + 28, 720, y + 37), fill=foreground)
    return image


def test_clear_sparse_page_is_good_and_deterministic() -> None:
    artifact = _artifact(_text_page())
    first = measure_page_quality(artifact)
    second = measure_page_quality(artifact)

    assert first == second
    assert first.algorithm_version == QUALITY_ALGORITHM_VERSION
    assert "blur" not in first.issues
    assert "low_contrast" not in first.issues
    assert assess_page_quality(first, reliable_text=False).grade == "good"


def test_tiny_page_requires_rescan_without_reliable_text_but_not_with_it() -> None:
    metrics = measure_page_quality(_artifact(_text_page(width=240, height=300)))

    assert "low_resolution" in metrics.issues
    assert assess_page_quality(metrics, reliable_text=False).grade == "rescan_required"
    trusted = assess_page_quality(metrics, reliable_text=True)
    assert trusted.grade == "review_required"
    assert trusted.rescan_required is False


def test_blur_and_low_contrast_are_measured_independently() -> None:
    blurred = _text_page().filter(ImageFilter.GaussianBlur(6))
    blur_metrics = measure_page_quality(_artifact(blurred))
    contrast_metrics = measure_page_quality(_artifact(_text_page(foreground=225, background=245)))

    assert "blur" in blur_metrics.issues
    assert "low_contrast" in contrast_metrics.issues
    assert blur_metrics.sharpness_score < 0.5
    assert contrast_metrics.contrast_score < 0.5


def test_gradient_shadow_is_flagged() -> None:
    gradient = Image.linear_gradient("L").rotate(90, expand=True)
    image = gradient.resize((1200, 1600), Image.Resampling.BILINEAR).point(
        lambda value: 145 + round(value * 110 / 255)
    )
    metrics = measure_page_quality(_artifact(image))

    assert "shadow" in metrics.issues
    assert metrics.shadow_score < 0.5


@pytest.mark.parametrize("angle", [-3, 3])
def test_small_skew_is_detected_without_cardinal_rotation(angle: int) -> None:
    image = _text_page().rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    metrics = measure_page_quality(_artifact(image))

    assert "skew" in metrics.issues
    assert abs(abs(metrics.skew_degrees) - 3) <= 1
    assert abs(metrics.skew_degrees) < 45


def test_content_touching_edge_reports_crop_risk() -> None:
    image = _text_page()
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 100, 40, 1500), fill=0)
    metrics = measure_page_quality(_artifact(image))

    assert "crop_risk" in metrics.issues


def test_blank_page_is_not_rescan_required_from_edge_energy_alone() -> None:
    metrics = measure_page_quality(_artifact(Image.new("L", (1200, 1600), 255)))
    assessment = assess_page_quality(metrics, reliable_text=False)

    assert metrics.blank_probability >= 0.95
    assert "blur" not in metrics.issues
    assert "low_contrast" not in metrics.issues
    assert assessment.rescan_required is False


def test_two_independent_severe_defects_require_rescan() -> None:
    clear = measure_page_quality(_artifact(_text_page()))
    severe = replace(
        clear,
        sharpness_score=0.1,
        contrast_score=0.1,
        quality_score=0.1,
        issues=("blur", "low_contrast"),
    )

    assert assess_page_quality(severe, reliable_text=False).grade == "rescan_required"
    assert assess_page_quality(severe, reliable_text=True).grade == "review_required"


def test_resolution_and_grade_thresholds_are_explicit() -> None:
    tiny = measure_page_quality(_artifact(_text_page(width=319, height=400)))
    minimum_non_tiny = measure_page_quality(_artifact(_text_page(width=320, height=400)))
    clear = measure_page_quality(_artifact(_text_page()))

    assert tiny.resolution_score == 0
    assert minimum_non_tiny.resolution_score == 0.25
    assert assess_page_quality(replace(clear, quality_score=0.699999), False).grade == (
        "review_required"
    )
    assert assess_page_quality(replace(clear, quality_score=0.7), False).grade == "good"


def test_metrics_are_bounded_and_contain_no_image_or_text_payload() -> None:
    metrics = measure_page_quality(_artifact(_text_page()))
    payload = asdict(metrics)
    score_fields = [value for key, value in payload.items() if key.endswith("_score")]

    assert all(0 <= value <= 1 and not math.isnan(value) for value in score_fields)
    assert set(metrics.issues) <= {
        "low_resolution",
        "blur",
        "low_contrast",
        "shadow",
        "skew",
        "crop_risk",
    }
    assert "content" not in payload
    assert "text" not in payload
    assert "path" not in payload
