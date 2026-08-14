from __future__ import annotations

import io
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Protocol, cast

import httpx
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from app.core.config import Settings
from app.recognition.pipeline import PageArtifact, RecognitionError

FORMULA_EVAL_SCHEMA_VERSION = "formula-ocr-eval-v1"
_SAFE_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|[A-Za-z0-9]+|[^\s]")
_SPACING_COMMAND = re.compile(r"\\(?:[,;:!]|qquad\b|quad\b)")
_FORBIDDEN_MANIFEST_KEYS = {
    "filename",
    "file_path",
    "name",
    "source_hash",
    "source_path",
    "student_id",
    "student_name",
}


@dataclass(frozen=True)
class FormulaRegionArtifact:
    page: PageArtifact
    region: tuple[float, float, float, float]
    region_kind: str = "unknown"


@dataclass(frozen=True)
class FormulaCandidate:
    latex: str
    confidence: float | None
    provider: str
    provider_version: str
    warning_codes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FormulaImageQuality:
    warning_codes: tuple[str, ...] = field(default_factory=tuple)
    blocking_codes: tuple[str, ...] = field(default_factory=tuple)
    ruled_line_rows: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FormulaRecognitionOutcome:
    candidates: tuple[FormulaCandidate, ...]
    quality: FormulaImageQuality
    used_preprocessed_variant: bool
    preprocessing_agreed: bool | None


class FormulaRecognitionProvider(Protocol):
    name: str
    version: str

    def available(self) -> tuple[bool, str | None]: ...

    def recognize(self, artifact: FormulaRegionArtifact) -> list[FormulaCandidate]: ...


class UnavailableFormulaProvider:
    name = "unavailable"
    version = "none"

    def available(self) -> tuple[bool, str | None]:
        return False, "未配置可运行的数学公式识别器"

    def recognize(self, artifact: FormulaRegionArtifact) -> list[FormulaCandidate]:
        del artifact
        raise RecognitionError("FORMULA_PROVIDER_UNAVAILABLE", self.available()[1] or "不可用")


def assess_formula_image_quality(artifact: FormulaRegionArtifact) -> FormulaImageQuality:
    """Conservative image-only gate; ambiguity is surfaced, never auto-resolved."""
    try:
        image = Image.open(io.BytesIO(artifact.page.content)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise RecognitionError("FORMULA_IMAGE_INVALID", "公式区域图片无法读取") from exc
    gray = ImageOps.grayscale(image)
    stats = ImageStat.Stat(gray)
    contrast = float(stats.stddev[0])
    histogram = gray.histogram()
    dark_pixels = sum(histogram[:220])
    foreground_ratio = dark_pixels / max(gray.width * gray.height, 1)
    edge_stats = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
    edge_variance = float(edge_stats.var[0])
    ruled_rows: list[int] = []
    if gray.width >= 40:
        for row in range(gray.height):
            dark = sum(
                1 for column in range(gray.width) if cast(int, gray.getpixel((column, row))) < 210
            )
            if dark / gray.width >= 0.72:
                ruled_rows.append(row)
    warnings: set[str] = set()
    blockers: set[str] = set()
    if gray.width < 32 or gray.height < 18:
        blockers.add("FORMULA_CROP_TOO_SMALL")
    if foreground_ratio < 0.0015:
        blockers.add("FORMULA_CROP_BLANK_OR_TOO_FAINT")
    elif contrast < 12:
        warnings.add("FORMULA_LOW_CONTRAST")
    if edge_variance < 45 and foreground_ratio < 0.025:
        warnings.add("FORMULA_POSSIBLY_BLURRED")
    if ruled_rows:
        warnings.add("RULED_PAPER_LINE_AMBIGUOUS")
    if _has_dense_occluding_component(gray):
        blockers.add("FORMULA_SEVERE_OVERWRITING_OR_OCCLUSION")
    return FormulaImageQuality(tuple(sorted(warnings)), tuple(sorted(blockers)), tuple(ruled_rows))


def _has_dense_occluding_component(gray: Image.Image) -> bool:
    """Detect only very large dense black blobs; never attempt to erase them."""
    width, height = gray.size
    if width < 48 or height < 24:
        return False
    dark = {
        (column, row)
        for row in range(height)
        for column in range(width)
        if cast(int, gray.getpixel((column, row))) < 75
    }
    visited: set[tuple[int, int]] = set()
    for start in dark:
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        component: list[tuple[int, int]] = []
        while stack:
            column, row = stack.pop()
            component.append((column, row))
            for neighbor_column in range(column - 1, column + 2):
                for neighbor_row in range(row - 1, row + 2):
                    neighbor = (neighbor_column, neighbor_row)
                    if neighbor in dark and neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
        if len(component) < 250:
            continue
        columns = [point[0] for point in component]
        rows = [point[1] for point in component]
        box_width = max(columns) - min(columns) + 1
        box_height = max(rows) - min(rows) + 1
        fill_ratio = len(component) / (box_width * box_height)
        if box_width / width >= 0.15 and box_height / height >= 0.35 and fill_ratio >= 0.45:
            return True
    return False


def _preprocess_formula_image(
    artifact: FormulaRegionArtifact, quality: FormulaImageQuality
) -> FormulaRegionArtifact | None:
    if "FORMULA_LOW_CONTRAST" not in quality.warning_codes:
        return None
    image = Image.open(io.BytesIO(artifact.page.content)).convert("RGB")
    processed = ImageOps.autocontrast(image, cutoff=1)
    processed = ImageEnhance.Contrast(processed).enhance(1.35)
    output = io.BytesIO()
    processed.save(output, "PNG", optimize=True)
    content = output.getvalue()
    if content == artifact.page.content:
        return None
    return FormulaRegionArtifact(
        PageArtifact(content, processed.width, processed.height),
        artifact.region,
        artifact.region_kind,
    )


def recognize_formula_safely(
    provider: FormulaRecognitionProvider, artifact: FormulaRegionArtifact
) -> FormulaRecognitionOutcome:
    quality = assess_formula_image_quality(artifact)
    if quality.blocking_codes:
        message = (
            "公式区域疑似存在严重涂改或遮挡，请教师重新框选或标记无法识别"
            if "FORMULA_SEVERE_OVERWRITING_OR_OCCLUSION" in quality.blocking_codes
            else "公式截图质量不足，请教师重新框选或标记无法识别"
        )
        raise RecognitionError(
            "FORMULA_IMAGE_QUALITY_BLOCKED",
            message,
        )
    original = provider.recognize(artifact)
    variant = _preprocess_formula_image(artifact, quality)
    if variant is None:
        enriched = tuple(
            FormulaCandidate(
                item.latex,
                item.confidence,
                item.provider,
                item.provider_version,
                tuple(sorted(set(item.warning_codes).union(quality.warning_codes))),
            )
            for item in original
        )
        return FormulaRecognitionOutcome(enriched, quality, False, None)
    processed = provider.recognize(variant)
    original_top = select_top_candidate(original)
    processed_top = select_top_candidate(processed)
    agreed = bool(
        original_top
        and processed_top
        and normalize_latex(original_top.latex) == normalize_latex(processed_top.latex)
    )
    shared_warnings = set(quality.warning_codes)
    if not agreed:
        shared_warnings.add("PREPROCESSING_RESULT_CONFLICT")
    combined: list[FormulaCandidate] = []
    seen: set[str] = set()
    for candidate in [*original, *processed]:
        normalized = normalize_latex(candidate.latex)
        if normalized in seen:
            continue
        seen.add(normalized)
        combined.append(
            FormulaCandidate(
                candidate.latex,
                candidate.confidence,
                candidate.provider,
                candidate.provider_version,
                tuple(sorted(set(candidate.warning_codes).union(shared_warnings))),
            )
        )
    return FormulaRecognitionOutcome(tuple(combined), quality, True, agreed)


class FakeFormulaProvider:
    """Deterministic test-only provider; it must never represent model quality."""

    name = "fake"
    version = "formula-test-only-v1"

    def available(self) -> tuple[bool, str | None]:
        return True, "仅供自动化测试，禁止用于真实作业"

    def recognize(self, artifact: FormulaRegionArtifact) -> list[FormulaCandidate]:
        del artifact
        return [
            FormulaCandidate(r"\frac{1}{x^2}", 0.98, self.name, self.version),
            FormulaCandidate(r"\frac{1}{x^3}", 0.61, self.name, self.version),
        ]


def _provider_endpoint(settings: Settings) -> str:
    value = settings.formula_recognition_base_url
    if not value:
        raise RecognitionError("FORMULA_PROVIDER_UNAVAILABLE", "公式识别服务地址未配置")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RecognitionError("FORMULA_PROVIDER_ENDPOINT_REJECTED", "公式识别服务地址不安全")
    if settings.app_env.lower() == "production" and parsed.scheme != "https":
        raise RecognitionError(
            "FORMULA_PROVIDER_ENDPOINT_REJECTED", "生产公式识别服务必须使用 HTTPS"
        )
    allowed = {host.rstrip(".").lower() for host in settings.formula_recognition_allowed_hosts}
    if parsed.hostname.rstrip(".").lower() not in allowed:
        raise RecognitionError("FORMULA_PROVIDER_ENDPOINT_REJECTED", "公式识别服务主机不在白名单")
    return value.rstrip("/") + "/v1/formulas/recognize"


class HttpFormulaProvider:
    name = "http"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.version = settings.formula_recognition_config_version

    def available(self) -> tuple[bool, str | None]:
        try:
            _provider_endpoint(self.settings)
        except RecognitionError as exc:
            return False, str(exc)
        if self.settings.formula_recognition_api_key is None:
            return False, "公式识别服务凭据未配置"
        return True, None

    def recognize(self, artifact: FormulaRegionArtifact) -> list[FormulaCandidate]:
        available, reason = self.available()
        if not available:
            raise RecognitionError("FORMULA_PROVIDER_UNAVAILABLE", reason or "公式识别不可用")
        if len(artifact.page.content) > self.settings.formula_recognition_max_image_bytes:
            raise RecognitionError("FORMULA_IMAGE_TOO_LARGE", "公式区域图片超过识别大小限制")
        if (
            artifact.page.width * artifact.page.height
            > self.settings.formula_recognition_max_pixels
        ):
            raise RecognitionError("FORMULA_IMAGE_TOO_LARGE", "公式区域像素数量超过识别限制")
        token = self.settings.formula_recognition_api_key
        assert token is not None
        try:
            with httpx.Client(
                timeout=self.settings.formula_recognition_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    _provider_endpoint(self.settings),
                    headers={"Authorization": f"Bearer {token.get_secret_value()}"},
                    data={"region_kind": artifact.region_kind},
                    files={"file": ("formula.png", artifact.page.content, "image/png")},
                )
        except httpx.TimeoutException as exc:
            raise RecognitionError("FORMULA_PROVIDER_TIMEOUT", "公式识别服务响应超时") from exc
        except httpx.HTTPError as exc:
            raise RecognitionError("FORMULA_PROVIDER_UNAVAILABLE", "公式识别服务无法连接") from exc
        if response.status_code >= 400:
            raise RecognitionError("FORMULA_PROVIDER_REJECTED", "公式识别服务拒绝了请求")
        if len(response.content) > 256 * 1024:
            raise RecognitionError("FORMULA_OUTPUT_INVALID", "公式识别响应超过大小限制")
        try:
            payload = response.json()
            raw_candidates = payload["candidates"]
            provider_name = payload["provider"]
            provider_version = payload["provider_version"]
            if (
                not isinstance(raw_candidates, list)
                or not isinstance(provider_name, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", provider_name)
                or not isinstance(provider_version, str)
                or not provider_version.strip()
            ):
                raise ValueError
            if (
                not raw_candidates
                or len(raw_candidates) > self.settings.formula_recognition_max_candidates
            ):
                raise ValueError
            candidates = [
                self._candidate(item, provider_name, provider_version) for item in raw_candidates
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise RecognitionError("FORMULA_OUTPUT_INVALID", "公式识别结果格式无效") from exc
        return candidates

    def _candidate(
        self, raw: object, provider_name: str, provider_version: str
    ) -> FormulaCandidate:
        if not isinstance(raw, dict):
            raise ValueError
        latex = raw.get("latex")
        confidence = raw.get("confidence")
        warnings = raw.get("warning_codes", [])
        if not isinstance(latex, str) or not latex.strip() or len(latex) > 20_000:
            raise ValueError
        if confidence is not None and (
            not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1
        ):
            raise ValueError
        if not isinstance(warnings, list) or not all(
            isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", item)
            for item in warnings
        ):
            raise ValueError
        return FormulaCandidate(
            latex.strip(),
            float(confidence) if confidence is not None else None,
            provider_name,
            provider_version[:80],
            tuple(sorted(set(warnings))),
        )


def crop_formula_region(
    page: PageArtifact,
    region: tuple[float, float, float, float],
    *,
    max_pixels: int,
    max_bytes: int,
) -> FormulaRegionArtifact:
    x, y, width, height = region
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise RecognitionError("FORMULA_REGION_INVALID", "公式区域坐标无效")
    try:
        image = Image.open(io.BytesIO(page.content)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise RecognitionError("FORMULA_IMAGE_INVALID", "公式来源图片无法读取") from exc
    left = max(0, round(x * image.width))
    top = max(0, round(y * image.height))
    right = min(image.width, round((x + width) * image.width))
    bottom = min(image.height, round((y + height) * image.height))
    if right - left < 2 or bottom - top < 2:
        raise RecognitionError("FORMULA_REGION_INVALID", "公式区域过小")
    crop = image.crop((left, top, right, bottom))
    if crop.width * crop.height > max_pixels:
        raise RecognitionError("FORMULA_IMAGE_TOO_LARGE", "公式区域像素数量超过识别限制")
    output = io.BytesIO()
    crop.save(output, "PNG", optimize=True)
    content = output.getvalue()
    if len(content) > max_bytes:
        raise RecognitionError("FORMULA_IMAGE_TOO_LARGE", "公式区域图片超过识别大小限制")
    artifact = PageArtifact(content, crop.width, crop.height)
    return FormulaRegionArtifact(artifact, region)


def formula_provider_from_settings(settings: Settings) -> FormulaRecognitionProvider:
    name = settings.formula_recognition_provider.lower()
    if name == "fake" and settings.app_env.lower() == "test":
        return FakeFormulaProvider()
    if name == "http":
        return HttpFormulaProvider(settings)
    return UnavailableFormulaProvider()


def normalize_latex(value: str) -> str:
    """Normalize harmless presentation differences, never mathematical meaning."""
    normalized = value.strip().replace("\r", "").replace("\n", " ")
    normalized = normalized.replace("\\left", "").replace("\\right", "")
    normalized = _SPACING_COMMAND.sub("", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def latex_tokens(value: str) -> list[str]:
    return _TOKEN.findall(normalize_latex(value))


def token_edit_distance(expected: str, actual: str) -> int:
    left, right = latex_tokens(expected), latex_tokens(actual)
    previous = list(range(len(right) + 1))
    for left_index, left_token in enumerate(left, 1):
        current = [left_index]
        for right_index, right_token in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_token != right_token),
                )
            )
        previous = current
    return previous[-1]


def token_similarity(expected: str, actual: str) -> float:
    denominator = max(len(latex_tokens(expected)), len(latex_tokens(actual)), 1)
    return max(0.0, 1.0 - token_edit_distance(expected, actual) / denominator)


def select_top_candidate(candidates: list[FormulaCandidate]) -> FormulaCandidate | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: item.confidence if item.confidence is not None else -1.0,
    )


def _find_forbidden_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        found = _FORBIDDEN_MANIFEST_KEYS.intersection(value)
        return found.union(
            *(_find_forbidden_keys(item) for item in value.values()),
        )
    if isinstance(value, list):
        return set().union(*(_find_forbidden_keys(item) for item in value))
    return set()


def validate_eval_dataset(dataset: object) -> list[dict[str, object]]:
    if (
        not isinstance(dataset, dict)
        or dataset.get("schema_version") != FORMULA_EVAL_SCHEMA_VERSION
    ):
        raise ValueError("invalid formula evaluation schema")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("formula evaluation cases must be a non-empty list")
    seen: set[str] = set()
    validated: list[dict[str, object]] = []
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            raise ValueError("formula evaluation case must be an object")
        forbidden = _find_forbidden_keys(raw_case)
        if forbidden:
            fields = sorted(forbidden)
            raise ValueError(f"formula evaluation case contains private source fields: {fields}")
        case_id = raw_case.get("id")
        if not isinstance(case_id, str) or not _SAFE_CASE_ID.fullmatch(case_id):
            raise ValueError("formula evaluation case id must be a sanitized identifier")
        if case_id in seen:
            raise ValueError("formula evaluation case ids must be unique")
        seen.add(case_id)
        if raw_case.get("modality") not in {"text_pdf", "scan", "photo", "synthetic"}:
            raise ValueError("unsupported formula evaluation modality")
        if not isinstance(raw_case.get("expected_latex"), str):
            raise ValueError("expected_latex must be a string")
        predictions = raw_case.get("predictions")
        if not isinstance(predictions, list):
            raise ValueError("predictions must be a list")
        validated.append(raw_case)
    return validated
