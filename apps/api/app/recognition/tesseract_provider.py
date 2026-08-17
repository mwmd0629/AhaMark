"""Explicit, offline Tesseract provider for ordinary printed text."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import subprocess
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.recognition.pipeline import PageArtifact, ProviderBlock, RecognitionError

TESSERACT_MAX_INPUT_BYTES = 20 * 1024 * 1024
TESSERACT_MAX_INPUT_PIXELS = 40_000_000
TESSERACT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
TESSERACT_MAX_ROWS = 10_000
TESSERACT_MAX_BLOCKS = 2_000
TESSERACT_MAX_TEXT_CHARS = 4_000
TESSERACT_MAX_TOTAL_TEXT_CHARS = 200_000
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}")
_UNSAFE_TEXT = re.compile(
    "[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\u206a-\u206f\ud800-\udfff]"
)
_TSV_FIELDS = (
    "level",
    "page_num",
    "block_num",
    "par_num",
    "line_num",
    "word_num",
    "left",
    "top",
    "width",
    "height",
    "conf",
    "text",
)


@dataclass(frozen=True)
class TesseractProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


CommandRunner = Callable[[Sequence[str], bytes, float], TesseractProcessResult]


@dataclass(frozen=True)
class _ValidatedFile:
    path: Path
    identity: tuple[int, int, int, int]


def _default_runner(argv: Sequence[str], content: bytes, timeout: float) -> TesseractProcessResult:
    completed = subprocess.run(
        list(argv),
        input=content,
        capture_output=True,
        check=False,
        shell=False,
        timeout=timeout,
    )
    return TesseractProcessResult(completed.returncode, completed.stdout, completed.stderr)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise RecognitionError(
            "RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 本地文件无法读取"
        ) from exc
    return digest.hexdigest()


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    metadata = path.lstat()
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _validated_file(path_value: str, expected_sha256: str, label: str) -> _ValidatedFile:
    if not _SHA256.fullmatch(expected_sha256):
        raise RecognitionError(
            "RECOGNITION_PROVIDER_UNAVAILABLE", f"Tesseract {label} 哈希配置无效"
        )
    raw_path = Path(path_value)
    if not raw_path.is_absolute() or raw_path.is_symlink():
        raise RecognitionError("RECOGNITION_PROVIDER_UNAVAILABLE", f"Tesseract {label} 路径无效")
    try:
        path = raw_path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise RecognitionError(
            "RECOGNITION_PROVIDER_UNAVAILABLE", f"Tesseract {label} 文件不可用"
        ) from exc
    if not path.is_file() or metadata.st_nlink != 1:
        raise RecognitionError(
            "RECOGNITION_PROVIDER_UNAVAILABLE", f"Tesseract {label} 文件类型无效"
        )
    if _sha256_file(path) != expected_sha256:
        raise RecognitionError("RECOGNITION_PROVIDER_UNAVAILABLE", f"Tesseract {label} 哈希不匹配")
    return _ValidatedFile(path=path, identity=_file_identity(path))


def _ensure_files_unchanged(files: Sequence[_ValidatedFile]) -> None:
    try:
        unchanged = all(
            not item.path.is_symlink()
            and item.path.is_file()
            and item.path.lstat().st_nlink == 1
            and _file_identity(item.path) == item.identity
            for item in files
        )
    except OSError as exc:
        raise RecognitionError(
            "RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 本地文件无法复核"
        ) from exc
    if not unchanged:
        raise RecognitionError("RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 本地文件已发生变化")


def _unsafe_text(value: str) -> bool:
    if _UNSAFE_TEXT.search(value):
        return True
    return any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        or 0xFDD0 <= ord(character) <= 0xFDEF
        or ord(character) & 0xFFFF in {0xFFFE, 0xFFFF}
        for character in value
    )


def _integer(value: str, field: str) -> int:
    if not re.fullmatch(r"-?[0-9]+", value):
        raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", f"Tesseract {field} 无效")
    return int(value)


def _confidence(value: str) -> float:
    try:
        confidence = float(value)
    except ValueError as exc:
        raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract conf 无效") from exc
    if not math.isfinite(confidence) or not 0 <= confidence <= 100:
        raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract conf 超出范围")
    return confidence / 100


def parse_tesseract_tsv(
    raw_output: bytes, page: PageArtifact, *, source_version: str
) -> list[ProviderBlock]:
    """Parse bounded Tesseract TSV without trusting subprocess output."""

    if not isinstance(raw_output, bytes) or len(raw_output) > TESSERACT_MAX_OUTPUT_BYTES:
        raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract 输出大小无效")
    try:
        text_output = raw_output.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract 输出不是 UTF-8") from exc
    # Tesseract TSV does not use RFC CSV quoting. OCR text may legitimately
    # contain a double quote, which must not absorb following tabs or rows.
    reader = csv.DictReader(io.StringIO(text_output), delimiter="\t", quoting=csv.QUOTE_NONE)
    if tuple(reader.fieldnames or ()) != _TSV_FIELDS:
        raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract TSV 字段无效")

    ordered_blocks: list[tuple[tuple[int, int, int, int, int], ProviderBlock]] = []
    total_text_chars = 0
    for row_index, row in enumerate(reader, start=1):
        if (
            row_index > TESSERACT_MAX_ROWS
            or None in row
            or any(value is None for value in row.values())
        ):
            raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract TSV 行结构无效")
        level = _integer(row["level"], f"level[{row_index}]")
        if level != 5:
            continue
        page_number = _integer(row["page_num"], f"page_num[{row_index}]")
        block_number = _integer(row["block_num"], f"block_num[{row_index}]")
        paragraph_number = _integer(row["par_num"], f"par_num[{row_index}]")
        line_number = _integer(row["line_num"], f"line_num[{row_index}]")
        word_number = _integer(row["word_num"], f"word_num[{row_index}]")
        if (
            page_number < 1
            or block_number < 0
            or paragraph_number < 0
            or line_number < 0
            or word_number < 1
        ):
            raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract 阅读顺序无效")
        raw_text = row["text"]
        if not raw_text.strip():
            continue
        if len(raw_text) > TESSERACT_MAX_TEXT_CHARS or _unsafe_text(raw_text):
            raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract 文本块无效")
        total_text_chars += len(raw_text)
        if total_text_chars > TESSERACT_MAX_TOTAL_TEXT_CHARS:
            raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract 文本总量超过限制")
        left = _integer(row["left"], f"left[{row_index}]")
        top = _integer(row["top"], f"top[{row_index}]")
        width = _integer(row["width"], f"width[{row_index}]")
        height = _integer(row["height"], f"height[{row_index}]")
        if (
            left < 0
            or top < 0
            or width <= 0
            or height <= 0
            or left + width > page.width
            or top + height > page.height
        ):
            raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract 区域超出页面")
        score = _confidence(row["conf"])
        ordered_blocks.append(
            (
                (
                    page_number,
                    block_number,
                    paragraph_number,
                    line_number,
                    word_number,
                ),
                ProviderBlock(
                    block_type="text",
                    text=raw_text,
                    latex=None,
                    confidence=score,
                    region=(
                        left / page.width,
                        top / page.height,
                        width / page.width,
                        height / page.height,
                    ),
                    status="low_confidence" if score < 0.70 else "recognized",
                    source=f"tesseract:{source_version}",
                ),
            )
        )
        if len(ordered_blocks) > TESSERACT_MAX_BLOCKS:
            raise RecognitionError("OCR_PROVIDER_OUTPUT_INVALID", "Tesseract 文本块超过限制")
    ordered_blocks.sort(key=lambda item: item[0])
    return [block for _order, block in ordered_blocks]


class TesseractProvider:
    """Offline Tesseract CLI provider; never produces formula or LaTeX output."""

    name = "tesseract"
    is_demo = False

    def __init__(
        self,
        *,
        binary_path: str,
        data_root: str,
        license_path: str,
        expected_version: str,
        binary_sha256: str,
        chi_sim_sha256: str,
        eng_sha256: str,
        license_sha256: str,
        timeout_seconds: float = 20.0,
        runner: CommandRunner = _default_runner,
    ) -> None:
        self.version = expected_version
        self.timeout_seconds = timeout_seconds
        self._runner = runner
        self._configuration_error: RecognitionError | None = None
        self._binary = Path(binary_path)
        self._data_root = Path(data_root)
        self._validated_files: tuple[_ValidatedFile, ...] = ()
        try:
            if not _VERSION.fullmatch(expected_version) or not 0 < timeout_seconds <= 120:
                raise RecognitionError(
                    "RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 版本或超时配置无效"
                )
            if not self._data_root.is_absolute() or self._data_root.is_symlink():
                raise RecognitionError(
                    "RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 语言包目录无效"
                )
            binary = _validated_file(binary_path, binary_sha256, "binary")
            self._binary = binary.path
            self._data_root = self._data_root.resolve(strict=True)
            if not self._data_root.is_dir():
                raise RecognitionError(
                    "RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 语言包目录无效"
                )
            chi_sim = _validated_file(
                str(self._data_root / "chi_sim.traineddata"), chi_sim_sha256, "chi_sim"
            )
            eng = _validated_file(str(self._data_root / "eng.traineddata"), eng_sha256, "eng")
            license_file = _validated_file(license_path, license_sha256, "license")
            try:
                license_text = license_file.path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeError) as exc:
                raise RecognitionError(
                    "RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 许可证材料不可读"
                ) from exc
            if "Apache-2.0" not in license_text and not (
                "Apache License" in license_text and "Version 2.0" in license_text
            ):
                raise RecognitionError(
                    "RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 许可证材料无效"
                )
            self._validated_files = (binary, chi_sim, eng, license_file)
        except (OSError, RecognitionError) as exc:
            self._configuration_error = (
                exc
                if isinstance(exc, RecognitionError)
                else RecognitionError(
                    "RECOGNITION_PROVIDER_UNAVAILABLE", "Tesseract 本地配置不可用"
                )
            )

    def available(self) -> tuple[bool, str | None]:
        if self._configuration_error is not None:
            return False, "Tesseract 本地运行材料未通过校验"
        try:
            _ensure_files_unchanged(self._validated_files)
            result = self._runner((str(self._binary), "--version"), b"", 5.0)
            combined = result.stdout + b"\n" + result.stderr
            match = re.search(rb"(?im)^tesseract\s+([^\s]+)", combined)
            if result.returncode != 0 or match is None:
                return False, "Tesseract 本地运行时不可用"
            installed_version = match.group(1).decode("ascii", errors="strict")
            if installed_version != self.version:
                return False, "Tesseract 本地运行时版本不匹配"
        except Exception:
            return False, "Tesseract 本地运行时不可用"
        return True, None

    def recognize(self, page: PageArtifact) -> list[ProviderBlock]:
        if (
            not isinstance(page.content, bytes)
            or not page.content
            or len(page.content) > TESSERACT_MAX_INPUT_BYTES
            or page.content_type not in {"image/png", "image/jpeg"}
            or isinstance(page.width, bool)
            or isinstance(page.height, bool)
            or page.width <= 0
            or page.height <= 0
            or page.width * page.height > TESSERACT_MAX_INPUT_PIXELS
        ):
            raise RecognitionError("OCR_PROVIDER_INPUT_INVALID", "Tesseract 页面输入无效")
        available, reason = self.available()
        if not available:
            raise RecognitionError(
                "RECOGNITION_PROVIDER_UNAVAILABLE", reason or "Tesseract 本地运行时不可用"
            )
        argv = (
            str(self._binary),
            "stdin",
            "stdout",
            "--tessdata-dir",
            str(self._data_root),
            "-l",
            "chi_sim+eng",
            "--oem",
            "1",
            "--psm",
            "6",
            "tsv",
        )
        try:
            result = self._runner(argv, page.content, self.timeout_seconds)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RecognitionError("OCR_INFERENCE_FAILED", "Tesseract 本地识别失败") from exc
        if result.returncode != 0:
            raise RecognitionError("OCR_INFERENCE_FAILED", "Tesseract 本地识别失败")
        return parse_tesseract_tsv(result.stdout, page, source_version=self.version)
