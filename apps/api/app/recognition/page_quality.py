import io
from dataclasses import dataclass
from typing import Literal, cast

from PIL import Image, ImageFilter, ImageOps, ImageStat

from app.recognition.pipeline import PageArtifact

QUALITY_ALGORITHM_VERSION = "pil-page-quality-v1"
ANALYSIS_MAX_EDGE = 768
SKEW_MAX_EDGE = 640

QualityIssue = Literal[
    "low_resolution",
    "blur",
    "low_contrast",
    "shadow",
    "skew",
    "crop_risk",
]
QualityGrade = Literal["good", "review_required", "rescan_required"]


@dataclass(frozen=True)
class PageQualityMetrics:
    algorithm_version: str
    width: int
    height: int
    megapixels: float
    resolution_score: float
    edge_rms: float
    sharpness_score: float
    contrast_stddev: float
    contrast_score: float
    shadow_severity: float
    shadow_score: float
    skew_degrees: float
    skew_confidence: float
    skew_score: float
    ink_ratio: float
    blank_probability: float
    crop_risk_ratio: float
    crop_score: float
    quality_score: float
    issues: tuple[QualityIssue, ...]


@dataclass(frozen=True)
class PageQualityAssessment:
    grade: QualityGrade
    issues: tuple[QualityIssue, ...]
    manual_required: bool
    rescan_required: bool


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ramp(value: float, bad: float, good: float) -> float:
    if good <= bad:
        raise ValueError("quality ramp requires good > bad")
    return _clamp((value - bad) / (good - bad))


def _analysis_copy(image: Image.Image, max_edge: int) -> Image.Image:
    copy = ImageOps.grayscale(image)
    longest = max(copy.size)
    if longest > max_edge:
        scale = max_edge / longest
        copy = copy.resize(
            (max(1, round(copy.width * scale)), max(1, round(copy.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return copy


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return float(ordered[index])


def _resolution_score(width: int, height: int) -> float:
    minimum_edge = min(width, height)
    if minimum_edge < 320 or width * height < 100_000:
        return 0.0
    if minimum_edge < 480:
        return 0.25
    if minimum_edge < 700:
        return 0.5
    if minimum_edge < 900:
        return 0.75
    if minimum_edge < 1200:
        return 0.9
    return 1.0


def _ink_bbox(gray: Image.Image) -> tuple[Image.Image, float, float, float]:
    histogram = gray.histogram()
    pixel_count = max(1, gray.width * gray.height)
    ink_count = sum(histogram[:245])
    ink_ratio = ink_count / pixel_count
    blank_probability = _clamp((1.0 - ink_ratio - 0.97) / 0.03)
    mask = gray.point(lambda value: 255 if value < 245 else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return gray, ink_ratio, blank_probability, 0.0
    left, top, right, bottom = bbox
    margin = max(2, round(min(gray.size) * 0.01))
    content = gray.crop(
        (
            max(0, left - margin),
            max(0, top - margin),
            min(gray.width, right + margin),
            min(gray.height, bottom + margin),
        )
    )
    border_x = max(1, round(gray.width * 0.02))
    border_y = max(1, round(gray.height * 0.02))
    border_mask = Image.new("L", gray.size, 0)
    border_mask.paste(255, (0, 0, gray.width, border_y))
    border_mask.paste(255, (0, gray.height - border_y, gray.width, gray.height))
    border_mask.paste(255, (0, 0, border_x, gray.height))
    border_mask.paste(255, (gray.width - border_x, 0, gray.width, gray.height))
    border_ink = ImageStat.Stat(mask, mask=border_mask).sum[0] / 255
    crop_risk_ratio = border_ink / max(1, ink_count)
    return content, ink_ratio, blank_probability, crop_risk_ratio


def _shadow_severity(gray: Image.Image) -> float:
    filter_size = min(31, max(3, (min(gray.size) // 24) | 1))
    illumination = gray.filter(ImageFilter.MaxFilter(filter_size)).resize(
        (8, 8), Image.Resampling.BOX
    )
    values = [float(value) for value in cast(list[int], list(illumination.get_flattened_data()))]
    return _clamp((_percentile(values, 0.9) - _percentile(values, 0.1)) / 255)


def _projection_variance(mask: Image.Image, angle: int) -> float:
    rotated = mask.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=0)
    projection = rotated.resize((1, rotated.height), Image.Resampling.BOX)
    values = [float(value) for value in cast(list[int], list(projection.get_flattened_data()))]
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _measure_skew(gray: Image.Image, ink_ratio: float, contrast: float) -> tuple[float, float]:
    if ink_ratio < 0.002 or contrast < 8:
        return 0.0, 0.0
    sample = _analysis_copy(gray, SKEW_MAX_EDGE)
    mask = sample.point(lambda value: 255 if value < 200 else 0)
    scores = {angle: _projection_variance(mask, angle) for angle in range(-5, 6)}
    best_angle = max(scores, key=lambda angle: (scores[angle], -abs(angle)))
    best_score = scores[best_angle]
    baseline = scores[0]
    confidence = _clamp((best_score - baseline) / max(best_score, 1.0))
    if best_angle == 0:
        confidence = _clamp(
            (best_score - _percentile(list(scores.values()), 0.5)) / max(best_score, 1.0)
        )
    return float(-best_angle), confidence


def measure_page_quality(page: PageArtifact) -> PageQualityMetrics:
    image = Image.open(io.BytesIO(page.content))
    image.load()
    gray = _analysis_copy(image, ANALYSIS_MAX_EDGE)
    content, ink_ratio, blank_probability, crop_risk_ratio = _ink_bbox(gray)
    contrast_stddev = float(ImageStat.Stat(gray).stddev[0])
    edge_rms = float(ImageStat.Stat(content.filter(ImageFilter.FIND_EDGES)).rms[0])
    resolution_score = _resolution_score(page.width, page.height)
    is_blank = blank_probability >= 0.95
    normalized_edge_energy = edge_rms / max(contrast_stddev, 1.0)
    sharpness_score = 1.0 if is_blank else _ramp(normalized_edge_energy, 0.55, 0.95)
    contrast_score = 1.0 if is_blank else _ramp(contrast_stddev, 6.0, 20.0)
    shadow_severity = _shadow_severity(gray)
    shadow_score = 1.0 - _ramp(shadow_severity, 0.08, 0.28)
    skew_degrees, skew_confidence = _measure_skew(gray, ink_ratio, contrast_stddev)
    skew_magnitude = abs(skew_degrees) if skew_confidence >= 0.02 else 0.0
    skew_score = 1.0 - _ramp(skew_magnitude, 1.0, 5.0)
    crop_score = 1.0 - _ramp(crop_risk_ratio, 0.02, 0.20)
    issues: list[QualityIssue] = []
    if resolution_score < 0.5:
        issues.append("low_resolution")
    if not is_blank and sharpness_score < 0.5:
        issues.append("blur")
    if not is_blank and contrast_score < 0.5:
        issues.append("low_contrast")
    if shadow_score < 0.5:
        issues.append("shadow")
    if skew_magnitude >= 2.0:
        issues.append("skew")
    if crop_score < 0.5:
        issues.append("crop_risk")
    components = [resolution_score, shadow_score, crop_score]
    if not is_blank:
        components.extend([sharpness_score, contrast_score, skew_score])
    quality_score = _clamp(min(components))
    return PageQualityMetrics(
        algorithm_version=QUALITY_ALGORITHM_VERSION,
        width=page.width,
        height=page.height,
        megapixels=round(page.width * page.height / 1_000_000, 6),
        resolution_score=round(resolution_score, 6),
        edge_rms=round(edge_rms, 6),
        sharpness_score=round(sharpness_score, 6),
        contrast_stddev=round(contrast_stddev, 6),
        contrast_score=round(contrast_score, 6),
        shadow_severity=round(shadow_severity, 6),
        shadow_score=round(shadow_score, 6),
        skew_degrees=round(skew_degrees, 6),
        skew_confidence=round(skew_confidence, 6),
        skew_score=round(skew_score, 6),
        ink_ratio=round(ink_ratio, 6),
        blank_probability=round(blank_probability, 6),
        crop_risk_ratio=round(crop_risk_ratio, 6),
        crop_score=round(crop_score, 6),
        quality_score=round(quality_score, 6),
        issues=tuple(issues),
    )


def assess_page_quality(metrics: PageQualityMetrics, reliable_text: bool) -> PageQualityAssessment:
    tiny = metrics.resolution_score == 0.0
    severe_count = sum(
        (
            metrics.sharpness_score < 0.2 and metrics.blank_probability < 0.95,
            metrics.contrast_score < 0.2 and metrics.blank_probability < 0.95,
            metrics.shadow_score < 0.2,
            metrics.skew_score <= 0.25 and metrics.skew_confidence >= 0.02,
            metrics.crop_score < 0.2,
        )
    )
    rescan_required = not reliable_text and (tiny or severe_count >= 2)
    if rescan_required:
        grade: QualityGrade = "rescan_required"
    elif metrics.issues or metrics.quality_score < 0.7:
        grade = "review_required"
    else:
        grade = "good"
    return PageQualityAssessment(
        grade=grade,
        issues=metrics.issues,
        manual_required=grade != "good",
        rescan_required=rescan_required,
    )
