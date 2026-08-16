import hashlib
import io
import math
import re
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, BinaryIO, Protocol

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from app.core.config import Settings
from app.recognition.question_numbers import normalize_question_number
from app.recognition.text_integrity import inspect_text_integrity
from app.storage.base import ObjectStorage


class RecognitionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PageArtifact:
    content: bytes
    width: int
    height: int
    content_type: str = "image/png"


@dataclass(frozen=True)
class ProviderBlock:
    block_type: str
    text: str | None
    latex: str | None
    confidence: float | None
    region: tuple[float, float, float, float]
    status: str = "recognized"
    source: str | None = None
    character_boxes: list[dict[str, int | float | str]] = field(default_factory=list)


@dataclass(frozen=True)
class TextFusionResult:
    blocks: list[ProviderBlock]
    source_conflict_count: int
    math_symbol_conflict_count: int
    missing_region_count: int
    source_agreement_ratio: float | None

    @property
    def adopted_blocks(self) -> list[ProviderBlock]:
        return [block for block in self.blocks if block.status in {"adopted", "manual_required"}]

    @property
    def metrics(self) -> dict[str, int | float | None]:
        return {
            "source_conflict_count": self.source_conflict_count,
            "math_symbol_conflict_count": self.math_symbol_conflict_count,
            "missing_region_count": self.missing_region_count,
            "source_agreement_ratio": self.source_agreement_ratio,
        }


_MATH_SENSITIVE = set("=<>+-*/^_|±×÷≤≥≠≈√∑∏∫∂∞∈∉⊂⊆∪∩→⇒⇔")
_SUPERSCRIPT_OR_SUBSCRIPT = re.compile(r"[\u00b2\u00b3\u00b9\u2070-\u209f]")


def _normalized_source_text(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _nonspace_source_text(text: str | None) -> str:
    return "".join((text or "").split()).casefold()


def _overlap_coverage(left: ProviderBlock, right: ProviderBlock) -> float:
    lx, ly, lw, lh = left.region
    rx, ry, rw, rh = right.region
    intersection = max(0.0, min(lx + lw, rx + rw) - max(lx, rx)) * max(
        0.0, min(ly + lh, ry + rh) - max(ly, ry)
    )
    return intersection / max(min(lw * lh, rw * rh), 1e-12)


def _math_signature(text: str | None) -> tuple[str, ...]:
    return tuple(
        character
        for character in _normalized_source_text(text)
        if character in _MATH_SENSITIVE
        or _SUPERSCRIPT_OR_SUBSCRIPT.fullmatch(character)
        or unicodedata.category(character) == "Sm"
        or "GREEK" in unicodedata.name(character, "")
    )


def _reliable_pdf_text(blocks: list[ProviderBlock]) -> bool:
    text = "".join(_nonspace_source_text(block.text) for block in blocks)
    return (
        len(text) >= 20
        and all(
            width > 0 and height > 0 and x >= 0 and y >= 0 and x + width <= 1 and y + height <= 1
            for block in blocks
            for x, y, width, height in [block.region]
        )
        and not any(
            inspect_text_integrity(block.text, field_path=f"pdf_text[{index}]")
            for index, block in enumerate(blocks)
        )
    )


def fuse_text_sources(
    pdf_blocks: list[ProviderBlock], ocr_blocks: list[ProviderBlock]
) -> TextFusionResult:
    """Preserve both sources while adopting only conservative, non-duplicated text."""

    reliable_pdf = _reliable_pdf_text(pdf_blocks)
    fused = [
        replace(block, status="adopted" if reliable_pdf else "unreliable_source")
        for block in pdf_blocks
    ]
    comparable_pdf_blocks = list(enumerate(fused)) if reliable_pdf else []
    agreements = 0
    conflicts = 0
    math_conflicts = 0
    missing = 0
    for ocr_block in ocr_blocks:
        overlaps = [
            (index, block)
            for index, block in comparable_pdf_blocks
            if _overlap_coverage(block, ocr_block) >= 0.5
        ]
        if not reliable_pdf or not overlaps:
            fused.append(replace(ocr_block, status="adopted"))
            if reliable_pdf:
                missing += 1
            continue
        best_index, best = max(
            overlaps,
            key=lambda indexed_block: _overlap_coverage(indexed_block[1], ocr_block),
        )
        if _normalized_source_text(best.text) == _normalized_source_text(ocr_block.text):
            agreements += 1
            fused.append(replace(ocr_block, status="source_agreement"))
            continue
        conflicts += 1
        whitespace_topology_conflict = (
            _nonspace_source_text(best.text) == _nonspace_source_text(ocr_block.text)
            and _normalized_source_text(best.text) != _normalized_source_text(ocr_block.text)
            and any(character.isdigit() for character in _nonspace_source_text(best.text))
        )
        is_math_conflict = whitespace_topology_conflict or (
            _math_signature(best.text) != _math_signature(ocr_block.text)
            and bool(_math_signature(best.text) or _math_signature(ocr_block.text))
        )
        if is_math_conflict:
            math_conflicts += 1
            fused[best_index] = replace(fused[best_index], status="manual_required")
        fused.append(replace(ocr_block, status="source_conflict"))
    compared = agreements + conflicts
    return TextFusionResult(
        blocks=fused,
        source_conflict_count=conflicts,
        math_symbol_conflict_count=math_conflicts,
        missing_region_count=missing,
        source_agreement_ratio=agreements / compared if compared else None,
    )


@dataclass(frozen=True)
class QuestionAnchor:
    block_id: uuid.UUID
    paper_page_id: uuid.UUID
    y: float


@dataclass(frozen=True)
class DerivedQuestionRegion:
    paper_page_id: uuid.UUID
    x: float
    y: float
    width: float
    height: float


def derive_question_regions(
    page_ids: list[uuid.UUID], anchors: list[QuestionAnchor]
) -> dict[uuid.UUID, list[DerivedQuestionRegion]]:
    """Partition pages between ordered anchors without inferring beyond the final anchor page."""
    page_order = {page_id: index for index, page_id in enumerate(page_ids)}
    valid = [anchor for anchor in anchors if anchor.paper_page_id in page_order]
    ordered = sorted(valid, key=lambda item: (page_order[item.paper_page_id], item.y))
    output: dict[uuid.UUID, list[DerivedQuestionRegion]] = {}
    for index, anchor in enumerate(ordered):
        start_page = page_order[anchor.paper_page_id]
        start_y = min(1.0, max(0.0, anchor.y))
        next_anchor = ordered[index + 1] if index + 1 < len(ordered) else None
        end_page = page_order[next_anchor.paper_page_id] if next_anchor is not None else start_page
        regions: list[DerivedQuestionRegion] = []
        if end_page == start_page:
            end_y = min(1.0, max(start_y, next_anchor.y)) if next_anchor is not None else 1.0
            if end_y > start_y:
                regions.append(
                    DerivedQuestionRegion(anchor.paper_page_id, 0.0, start_y, 1.0, end_y - start_y)
                )
        else:
            if start_y < 1.0:
                regions.append(
                    DerivedQuestionRegion(anchor.paper_page_id, 0.0, start_y, 1.0, 1.0 - start_y)
                )
            for page_index in range(start_page + 1, end_page):
                regions.append(DerivedQuestionRegion(page_ids[page_index], 0.0, 0.0, 1.0, 1.0))
            assert next_anchor is not None
            end_y = min(1.0, max(0.0, next_anchor.y))
            if end_y > 0.0:
                regions.append(
                    DerivedQuestionRegion(next_anchor.paper_page_id, 0.0, 0.0, 1.0, end_y)
                )
        output[anchor.block_id] = regions
    return output


def parse_hierarchical_question_number(text: str) -> str | None:
    return normalize_question_number(text)


def text_for_question_region(
    blocks: list[tuple[uuid.UUID, ProviderBlock]],
    regions: list[DerivedQuestionRegion],
    *,
    max_characters: int = 20000,
) -> str | None:
    """Join trustworthy reading-order text whose centre falls inside a question region."""
    selected: list[tuple[int, float, float, int, str, bool]] = []
    for block_order, (paper_page_id, block) in enumerate(blocks):
        text = " ".join((block.text or "").split())
        if (
            not text
            or block.source is None
            or block.status not in {"adopted", "manual_required", "recognized", "low_confidence"}
            or not (
                block.source.startswith("pdf_text:")
                or block.source.startswith("rapidocr:")
                or block.source.startswith("tesseract:")
            )
        ):
            continue
        x, y, width, height = block.region
        centre_x, centre_y = x + width / 2, y + height / 2
        for region_order, region in enumerate(regions):
            if (
                region.paper_page_id == paper_page_id
                and region.x <= centre_x <= region.x + region.width
                and region.y <= centre_y <= region.y + region.height
            ):
                selected.append(
                    (
                        region_order,
                        y,
                        x + block_order * 1e-9,
                        block_order,
                        text,
                        block.source.startswith("tesseract:"),
                    )
                )
                break
    if not selected:
        return None
    if all(item[5] for item in selected):
        ordered = sorted(selected, key=lambda item: (item[0], item[3]))
    else:
        ordered = sorted(selected, key=lambda item: (item[0], item[1], item[2]))
    joined = "\n".join(item[4] for item in ordered)
    return joined[:max_characters]


def _line_block(
    characters: list[str],
    positioned_characters: list[tuple[int, str, tuple[float, float, float, float]]],
    page_width: float,
    page_height: float,
) -> ProviderBlock | None:
    text = " ".join("".join(characters).split())
    boxes = [item[2] for item in positioned_characters]
    if not text or not boxes or page_width <= 0 or page_height <= 0:
        return None
    left = max(0.0, min(box[0] for box in boxes))
    bottom = max(0.0, min(box[1] for box in boxes))
    right = min(page_width, max(box[2] for box in boxes))
    top = min(page_height, max(box[3] for box in boxes))
    if right <= left or top <= bottom:
        return None
    return ProviderBlock(
        block_type=(
            "question_number" if parse_hierarchical_question_number(text) is not None else "text"
        ),
        text=text,
        latex=None,
        confidence=1.0,
        region=(
            left / page_width,
            (page_height - top) / page_height,
            (right - left) / page_width,
            (top - bottom) / page_height,
        ),
        source="pdf_text:pypdfium2",
        character_boxes=[
            {
                "source_index": source_index,
                "text": character,
                "x": box[0] / page_width,
                "y": (page_height - box[3]) / page_height,
                "width": (box[2] - box[0]) / page_width,
                "height": (box[3] - box[1]) / page_height,
            }
            for source_index, character, box in positioned_characters
        ],
    )


def extract_pdf_text_layer(content: bytes, source_page: int) -> list[ProviderBlock]:
    """Read an embedded PDF text layer without treating it as OCR output."""
    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]

        document = pdfium.PdfDocument(content)
        if source_page < 1 or source_page > len(document):
            raise RecognitionError("PAGE_RENDER_FAILED", "PDF 页面编号无效")
        page = document[source_page - 1]
        text_page = page.get_textpage()
        page_width, page_height = page.get_size()
        line_characters: list[str] = []
        positioned_characters: list[tuple[int, str, tuple[float, float, float, float]]] = []
        blocks: list[ProviderBlock] = []
        for index in range(text_page.count_chars()):
            character = text_page.get_text_range(index, 1)
            if character in {"\r", "\n"}:
                block = _line_block(line_characters, positioned_characters, page_width, page_height)
                if block is not None:
                    blocks.append(block)
                line_characters = []
                positioned_characters = []
                continue
            line_characters.append(character)
            try:
                box = text_page.get_charbox(index)
            except Exception:
                continue
            if box[2] > box[0] and box[3] > box[1]:
                positioned_characters.append((len(line_characters) - 1, character, box))
        block = _line_block(line_characters, positioned_characters, page_width, page_height)
        if block is not None:
            blocks.append(block)
    except RecognitionError:
        raise
    except Exception as exc:
        raise RecognitionError("PDF_TEXT_EXTRACTION_FAILED", "PDF 文字层无法读取") from exc
    return blocks


class DocumentConverter(Protocol):
    def convert(self, content: bytes, content_type: str, source_page: int) -> PageArtifact: ...


class ImagePreprocessor(Protocol):
    def process(self, page: PageArtifact, parameters: dict[str, object]) -> PageArtifact: ...


class RecognitionProvider(Protocol):
    name: str
    version: str
    is_demo: bool

    def available(self) -> tuple[bool, str | None]: ...
    def recognize(self, page: PageArtifact) -> list[ProviderBlock]: ...


def safe_provider_readiness(provider: RecognitionProvider) -> tuple[bool, str | None]:
    try:
        available, reason = provider.available()
        if not isinstance(available, bool) or (reason is not None and not isinstance(reason, str)):
            raise TypeError("invalid provider readiness response")
        return available, reason
    except Exception:
        return False, "文字识别器状态暂不可用"


class DefaultDocumentConverter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def convert(self, content: bytes, content_type: str, source_page: int) -> PageArtifact:
        if content_type == "application/pdf":
            try:
                import pypdfium2 as pdfium

                document = pdfium.PdfDocument(content)
                if len(document) > self.settings.recognition_max_pdf_pages:
                    raise RecognitionError("PAGE_TOO_LARGE", "PDF 页数超过识别限制")
                if source_page < 1 or source_page > len(document):
                    raise RecognitionError("PAGE_RENDER_FAILED", "PDF 页面编号无效")
                scale = self.settings.recognition_pdf_dpi / 72
                image = document[source_page - 1].render(scale=scale).to_pil()
            except RecognitionError:
                raise
            except Exception as exc:
                code = "PDF_ENCRYPTED" if "password" in str(exc).lower() else "PDF_INVALID"
                raise RecognitionError(code, "PDF 加密、损坏或无法渲染") from exc
        elif content_type.startswith("image/"):
            try:
                Image.MAX_IMAGE_PIXELS = self.settings.recognition_max_image_pixels
                image = Image.open(io.BytesIO(content))
                image.load()
                image = ImageOps.exif_transpose(image)
            except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
                raise RecognitionError("IMAGE_INVALID", "图片损坏或像素数量超过限制") from exc
        elif "wordprocessingml" in content_type:
            raise RecognitionError(
                "DOCX_CONVERTER_UNAVAILABLE", "服务器未安装 LibreOffice，请改传 PDF"
            )
        else:
            raise RecognitionError("PAGE_CONVERSION_FAILED", "不支持的页面输入格式")
        if image.width * image.height > self.settings.recognition_max_image_pixels:
            raise RecognitionError("PAGE_TOO_LARGE", "页面像素数量超过识别限制")
        if image.mode in {"RGBA", "LA", "P"}:
            base = Image.new("RGB", image.size, "white")
            rgba = image.convert("RGBA")
            base.paste(rgba, mask=rgba.getchannel("A"))
            image = base
        else:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, "PNG", optimize=True)
        return PageArtifact(output.getvalue(), image.width, image.height)


class PillowPreprocessor:
    """Conservative enhancement; parameters are persisted by the caller."""

    def process(self, page: PageArtifact, parameters: dict[str, object]) -> PageArtifact:
        image = Image.open(io.BytesIO(page.content)).convert("RGB")
        raw_rotation = parameters.get("rotation", 0)
        rotation = raw_rotation if isinstance(raw_rotation, int) else 0
        if rotation:
            image = image.rotate(-rotation, expand=True, fillcolor="white")
        crop = parameters.get("crop")
        if isinstance(crop, dict):
            x = float(crop.get("x", 0))
            y = float(crop.get("y", 0))
            w = float(crop.get("width", 1))
            h = float(crop.get("height", 1))
            image = image.crop(
                (x * image.width, y * image.height, (x + w) * image.width, (y + h) * image.height)
            )
        if parameters.get("denoise", True):
            image = image.filter(ImageFilter.MedianFilter(3))
        if parameters.get("contrast", True):
            image = ImageEnhance.Contrast(image).enhance(1.08)
        if parameters.get("grayscale", False):
            image = ImageOps.grayscale(image).convert("RGB")
        output = io.BytesIO()
        image.save(output, "PNG", optimize=True)
        return PageArtifact(output.getvalue(), image.width, image.height)


class UnavailableProvider:
    name = "unavailable"
    version = "none"
    is_demo = False

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason or "未配置可运行的文字 OCR；普通转换与预处理仍可使用"

    def available(self) -> tuple[bool, str | None]:
        return False, self.reason

    def recognize(self, page: PageArtifact) -> list[ProviderBlock]:
        raise RecognitionError("RECOGNITION_PROVIDER_UNAVAILABLE", self.available()[1] or "不可用")


class FakeProvider:
    name = "fake"
    version = "1"
    is_demo = True

    def available(self) -> tuple[bool, str | None]:
        return True, "仅供自动化测试，禁止用于真实试卷"

    def recognize(self, page: PageArtifact) -> list[ProviderBlock]:
        return [ProviderBlock("question_number", "1. 测试题", None, 0.95, (0.08, 0.10, 0.84, 0.12))]


RAPIDOCR_MAX_INPUT_BYTES = 20 * 1024 * 1024
RAPIDOCR_MAX_INPUT_PIXELS = 40_000_000
RAPIDOCR_MAX_BLOCKS = 2_000
RAPIDOCR_MAX_TEXT_CHARS = 4_000
RAPIDOCR_MAX_TOTAL_TEXT_CHARS = 200_000


def _output_invalid(message: str) -> RecognitionError:
    return RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", message)


def validate_rapidocr_input(page: PageArtifact) -> None:
    if type(page.content) is not bytes or not page.content:
        raise RecognitionError("OCR_PROVIDER_INPUT_INVALID", "OCR 输入必须是非空 bytes")
    if len(page.content) > RAPIDOCR_MAX_INPUT_BYTES:
        raise RecognitionError("OCR_PROVIDER_INPUT_INVALID", "OCR 输入字节数超过限制")
    if page.content_type not in {"image/png", "image/jpeg"}:
        raise RecognitionError("OCR_PROVIDER_INPUT_INVALID", "OCR 输入图片格式不受支持")
    if (
        isinstance(page.width, bool)
        or isinstance(page.height, bool)
        or not isinstance(page.width, int)
        or not isinstance(page.height, int)
        or page.width <= 0
        or page.height <= 0
        or page.width * page.height > RAPIDOCR_MAX_INPUT_PIXELS
    ):
        raise RecognitionError("OCR_PROVIDER_INPUT_INVALID", "OCR 输入尺寸无效或超过限制")


def _raw_sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _output_invalid(f"RapidOCR {label} 必须是序列")
    return value


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _output_invalid(f"RapidOCR {label} 必须是有限数值")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise _output_invalid(f"RapidOCR {label} 必须是有限数值")
    return normalized


def _has_unsafe_unicode(value: str) -> bool:
    for character in value:
        codepoint = ord(character)
        if (
            codepoint == 0x061C
            or 0x200E <= codepoint <= 0x200F
            or 0x202A <= codepoint <= 0x202E
            or 0x2066 <= codepoint <= 0x206F
            or 0xD800 <= codepoint <= 0xDFFF
            or 0xFDD0 <= codepoint <= 0xFDEF
            or codepoint & 0xFFFF in {0xFFFE, 0xFFFF}
        ):
            return True
    return False


def parse_rapidocr_output(output: object, page: PageArtifact) -> list[ProviderBlock]:
    try:
        raw_boxes = output.boxes  # type: ignore[attr-defined]
        raw_texts = output.txts  # type: ignore[attr-defined]
        raw_scores = output.scores  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise _output_invalid("RapidOCR 输出缺少 boxes/txts/scores") from exc
    if raw_boxes is None and raw_texts is None and raw_scores is None:
        return []
    if raw_boxes is None or raw_texts is None or raw_scores is None:
        raise _output_invalid("RapidOCR 输出字段不能部分为空")
    boxes = _raw_sequence(raw_boxes, "boxes")
    texts = _raw_sequence(raw_texts, "txts")
    scores = _raw_sequence(raw_scores, "scores")
    if len(boxes) != len(texts) or len(texts) != len(scores):
        raise _output_invalid("RapidOCR 输出字段长度不一致")
    if len(boxes) > RAPIDOCR_MAX_BLOCKS:
        raise _output_invalid("RapidOCR 输出块数量超过限制")

    blocks: list[ProviderBlock] = []
    total_text_chars = 0
    for index, (raw_box, raw_text, raw_score) in enumerate(zip(boxes, texts, scores, strict=True)):
        if (
            not isinstance(raw_text, str)
            or not raw_text.strip()
            or len(raw_text) > RAPIDOCR_MAX_TEXT_CHARS
            or _has_unsafe_unicode(raw_text)
        ):
            raise _output_invalid(f"RapidOCR 文本块 {index} 类型无效或过长")
        total_text_chars += len(raw_text)
        if total_text_chars > RAPIDOCR_MAX_TOTAL_TEXT_CHARS:
            raise _output_invalid("RapidOCR 输出文本总长度超过限制")
        score = _finite_number(raw_score, f"scores[{index}]")
        if not 0 <= score <= 1:
            raise _output_invalid(f"RapidOCR scores[{index}] 必须位于 0..1")
        box = _raw_sequence(raw_box, f"boxes[{index}]")
        if len(box) != 4:
            raise _output_invalid(f"RapidOCR boxes[{index}] 必须包含四个点")
        points: list[tuple[float, float]] = []
        for point_index, raw_point in enumerate(box):
            point = _raw_sequence(raw_point, f"boxes[{index}][{point_index}]")
            if len(point) != 2:
                raise _output_invalid(f"RapidOCR boxes[{index}][{point_index}] 必须包含 x/y")
            x = _finite_number(point[0], f"boxes[{index}][{point_index}].x")
            y = _finite_number(point[1], f"boxes[{index}][{point_index}].y")
            if not 0 <= x <= page.width or not 0 <= y <= page.height:
                raise _output_invalid(f"RapidOCR boxes[{index}] 超出页面范围")
            points.append((x, y))
        left = min(point[0] for point in points)
        right = max(point[0] for point in points)
        top = min(point[1] for point in points)
        bottom = max(point[1] for point in points)
        if right <= left or bottom <= top:
            raise _output_invalid(f"RapidOCR boxes[{index}] 必须为正面积")
        blocks.append(
            ProviderBlock(
                block_type="text",
                text=raw_text,
                latex=None,
                confidence=score,
                region=(
                    left / page.width,
                    top / page.height,
                    (right - left) / page.width,
                    (bottom - top) / page.height,
                ),
                status="low_confidence" if score < 0.70 else "recognized",
            )
        )
    return sorted(
        blocks,
        key=lambda block: (
            block.region[1],
            block.region[0],
            block.region[2],
            block.region[3],
            block.text or "",
            -(block.confidence or 0.0),
        ),
    )


class RapidOcrProvider:
    """Local printed-text OCR. It never claims formula/LaTeX recognition."""

    name = "rapidocr"
    is_demo = False

    def __init__(
        self,
        *,
        engine_factory: Callable[[], Any] | None = None,
        version: str = "unavailable",
    ) -> None:
        self.version = version if engine_factory is not None else "unavailable"
        self._engine_factory = engine_factory
        self._engine: Any | None = None

    def available(self) -> tuple[bool, str | None]:
        if self._engine_factory is None:
            return False, "RapidOCR 真实运行时尚未授权；不会导入或下载模型"
        return True, "仅测试注入的本地 OCR engine；不支持可靠手写或公式识别"

    def recognize(self, page: PageArtifact) -> list[ProviderBlock]:
        validate_rapidocr_input(page)
        available, reason = self.available()
        if not available:
            raise RecognitionError("RECOGNITION_PROVIDER_UNAVAILABLE", reason or "RapidOCR 不可用")
        try:
            if self._engine is None:
                assert self._engine_factory is not None
                self._engine = self._engine_factory()
            output = self._engine(page.content)
        except Exception as exc:
            raise RecognitionError("OCR_INFERENCE_FAILED", "本地文字 OCR 推理失败") from exc
        return parse_rapidocr_output(output, page)


def provider_from_settings(settings: Settings) -> RecognitionProvider:
    if settings.recognition_provider == "fake" and settings.app_env.lower() != "production":
        return FakeProvider()
    if settings.recognition_provider == "rapidocr":
        if (
            not settings.recognition_rapidocr_runtime_enabled
            or settings.recognition_rapidocr_model_download_allowed
        ):
            return UnavailableProvider("RapidOCR 运行时未授权，且禁止在服务进程中下载模型")
        return RapidOcrProvider()
    if settings.recognition_provider == "tesseract":
        if not settings.recognition_tesseract_runtime_enabled:
            return UnavailableProvider("Tesseract 本地运行时尚未配置")
        from app.recognition.tesseract_provider import TesseractProvider

        required = (
            settings.recognition_tesseract_binary_path,
            settings.recognition_tesseract_data_root,
            settings.recognition_tesseract_license_path,
            settings.recognition_tesseract_expected_version,
            settings.recognition_tesseract_binary_sha256,
            settings.recognition_tesseract_chi_sim_sha256,
            settings.recognition_tesseract_eng_sha256,
            settings.recognition_tesseract_license_sha256,
        )
        if any(value is None for value in required):
            return UnavailableProvider("Tesseract 本地运行材料配置不完整")
        return TesseractProvider(
            binary_path=settings.recognition_tesseract_binary_path or "",
            data_root=settings.recognition_tesseract_data_root or "",
            license_path=settings.recognition_tesseract_license_path or "",
            expected_version=settings.recognition_tesseract_expected_version or "",
            binary_sha256=settings.recognition_tesseract_binary_sha256 or "",
            chi_sim_sha256=settings.recognition_tesseract_chi_sim_sha256 or "",
            eng_sha256=settings.recognition_tesseract_eng_sha256 or "",
            license_sha256=settings.recognition_tesseract_license_sha256 or "",
            timeout_seconds=settings.recognition_tesseract_timeout_seconds,
        )
    return UnavailableProvider()


def store_artifact(storage: ObjectStorage, key: str, artifact: PageArtifact) -> str:
    storage.put(key, io.BytesIO(artifact.content), len(artifact.content), artifact.content_type)
    return hashlib.sha256(artifact.content).hexdigest()


def derivative_key(owner_id: uuid.UUID, job_id: uuid.UUID, page_id: uuid.UUID, kind: str) -> str:
    return f"recognition/{owner_id}/{job_id}/{page_id}/{kind}-{uuid.uuid4().hex}.png"


def read_all(stream: BinaryIO) -> bytes:
    try:
        return stream.read()
    finally:
        stream.close()
