import hashlib
import io
import re
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from importlib.metadata import PackageNotFoundError, version
from typing import Any, BinaryIO, Protocol, cast

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
    selected: list[tuple[int, float, float, str]] = []
    for block_order, (paper_page_id, block) in enumerate(blocks):
        text = " ".join((block.text or "").split())
        if (
            not text
            or block.source is None
            or block.status not in {"adopted", "manual_required", "recognized", "low_confidence"}
            or not (block.source.startswith("pdf_text:") or block.source.startswith("rapidocr:"))
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
                selected.append((region_order, y, x + block_order * 1e-9, text))
                break
    if not selected:
        return None
    joined = "\n".join(item[3] for item in sorted(selected))
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

    def available(self) -> tuple[bool, str | None]:
        return False, "未配置可运行的文字 OCR；普通转换与预处理仍可使用"

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


class RapidOcrProvider:
    """Local printed-text OCR. It never claims formula/LaTeX recognition."""

    name = "rapidocr"
    is_demo = False

    def __init__(self) -> None:
        try:
            self.version = version("rapidocr")
        except PackageNotFoundError:
            self.version = "unavailable"
        self._engine: Any | None = None

    def available(self) -> tuple[bool, str | None]:
        try:
            import onnxruntime  # noqa: F401
            from rapidocr import RapidOCR  # noqa: F401
        except ImportError as exc:
            return False, f"RapidOCR 运行依赖不可用：{exc.name or type(exc).__name__}"
        return True, "本地 ONNX 文字 OCR；不支持可靠手写或公式识别"

    def recognize(self, page: PageArtifact) -> list[ProviderBlock]:
        available, reason = self.available()
        if not available:
            raise RecognitionError("RECOGNITION_PROVIDER_UNAVAILABLE", reason or "RapidOCR 不可用")
        try:
            if self._engine is None:
                from rapidocr import RapidOCR

                self._engine = RapidOCR()
            output = cast(Any, self._engine(page.content))
            boxes = output.boxes
            texts = output.txts
            scores = output.scores
        except Exception as exc:
            raise RecognitionError("OCR_FAILED", "RapidOCR 无法识别该图片") from exc
        if boxes is None or texts is None or scores is None:
            return []
        blocks: list[ProviderBlock] = []
        for box, text, score in zip(boxes, texts, scores, strict=True):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            left, right = max(0.0, min(xs)), min(float(page.width), max(xs))
            top, bottom = max(0.0, min(ys)), min(float(page.height), max(ys))
            blocks.append(
                ProviderBlock(
                    block_type="text",
                    text=str(text),
                    latex=None,
                    confidence=float(score),
                    region=(
                        left / page.width,
                        top / page.height,
                        max(0.0, right - left) / page.width,
                        max(0.0, bottom - top) / page.height,
                    ),
                    status=("low_confidence" if float(score) < 0.70 else "recognized"),
                )
            )
        return blocks


def provider_from_settings(settings: Settings) -> RecognitionProvider:
    if settings.recognition_provider == "fake" and settings.app_env.lower() != "production":
        return FakeProvider()
    if settings.recognition_provider == "rapidocr":
        return RapidOcrProvider()
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
