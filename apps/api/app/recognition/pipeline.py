import hashlib
import io
import uuid
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, BinaryIO, Protocol, cast

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from app.core.config import Settings
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
                import pypdfium2 as pdfium  # type: ignore[import-untyped]

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
