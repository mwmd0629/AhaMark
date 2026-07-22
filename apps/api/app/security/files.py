import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader


@dataclass(frozen=True)
class FileInspection:
    kind: str
    page_count: int
    width: int | None = None
    height: int | None = None


class UnsafeFile(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def safe_filename(value: str | None, *, maximum: int = 255) -> str:
    name = PurePath((value or "upload").replace("\\", "/")).name
    if not name or name in {".", ".."}:
        raise UnsafeFile("FILE_NAME_INVALID", "文件名无效")
    if len(name) > maximum:
        raise UnsafeFile("FILE_NAME_TOO_LONG", f"文件名不能超过 {maximum} 个字符")
    if any(ord(char) < 32 or char == "\x7f" for char in name):
        raise UnsafeFile("FILE_NAME_INVALID", "文件名包含控制字符")
    return name


def _inspect_zip(content: bytes, *, required: set[str], kind: str) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        infos = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise UnsafeFile("OFFICE_INVALID", "Office 文件损坏或无法读取") from exc
    if len(infos) > 2000:
        archive.close()
        raise UnsafeFile("ARCHIVE_TOO_MANY_ENTRIES", "压缩包条目数超过限制")
    expanded = 0
    for info in infos:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            archive.close()
            raise UnsafeFile("ARCHIVE_PATH_INVALID", "压缩包包含不安全路径")
        expanded += info.file_size
        if expanded > 100 * 1024 * 1024:
            archive.close()
            raise UnsafeFile("ARCHIVE_EXPANDED_TOO_LARGE", "压缩包展开大小超过限制")
        if info.compress_size == 0 and info.file_size > 0:
            archive.close()
            raise UnsafeFile("ARCHIVE_RATIO_INVALID", "压缩包压缩比异常")
        if info.file_size > 10 * 1024 * 1024 and info.file_size / max(info.compress_size, 1) > 100:
            archive.close()
            raise UnsafeFile("ARCHIVE_RATIO_INVALID", "压缩包压缩比异常")
    names = {item.filename.replace("\\", "/") for item in infos}
    if not required.issubset(names):
        archive.close()
        raise UnsafeFile("OFFICE_INVALID", f"文件不是有效的 {kind.upper()}")
    return archive


def inspect_docx(content: bytes) -> FileInspection:
    archive = _inspect_zip(
        content,
        required={"[Content_Types].xml", "word/document.xml"},
        kind="docx",
    )
    try:
        names = {item.filename.lower() for item in archive.infolist()}
        if any(name.endswith("vbaproject.bin") for name in names):
            raise UnsafeFile("OFFICE_MACRO_FORBIDDEN", "不接受包含宏的 Office 文件")
        for info in archive.infolist():
            if info.filename.lower().endswith(".rels"):
                data = archive.read(info)
                if b'TargetMode="External"' in data or b"TargetMode='External'" in data:
                    raise UnsafeFile(
                        "OFFICE_EXTERNAL_LINK_FORBIDDEN", "不接受包含外部链接的 Office 文件"
                    )
    finally:
        archive.close()
    return FileInspection("docx", 1)


def inspect_xlsx_archive(content: bytes) -> None:
    archive = _inspect_zip(
        content,
        required={"[Content_Types].xml", "xl/workbook.xml"},
        kind="xlsx",
    )
    try:
        names = {item.filename.lower() for item in archive.infolist()}
        if any(name.endswith("vbaproject.bin") for name in names):
            raise UnsafeFile("OFFICE_MACRO_FORBIDDEN", "不接受包含宏的 Office 文件")
        for info in archive.infolist():
            if info.filename.lower().endswith(".rels"):
                data = archive.read(info)
                if b'TargetMode="External"' in data or b"TargetMode='External'" in data:
                    raise UnsafeFile(
                        "OFFICE_EXTERNAL_LINK_FORBIDDEN", "不接受包含外部链接的 Office 文件"
                    )
    finally:
        archive.close()


def inspect_upload(
    name: str,
    content: bytes,
    mime: str | None,
    *,
    max_pdf_pages: int,
    max_image_pixels: int,
    allow_docx: bool = False,
) -> FileInspection:
    filename = safe_filename(name)
    ext = PurePath(filename).suffix.lower().lstrip(".")
    expected = {
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }
    if allow_docx:
        expected["docx"] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext not in expected or mime != expected[ext]:
        raise UnsafeFile("FILE_TYPE_INVALID", "文件扩展名与 MIME 不匹配或类型不受支持")
    if ext == "pdf":
        if not content.startswith(b"%PDF-"):
            raise UnsafeFile("FILE_CONTENT_INVALID", "文件内容不是有效 PDF")
        try:
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise UnsafeFile("PDF_ENCRYPTED", "PDF 已加密，请先解除密码")
            pages = len(reader.pages)
        except UnsafeFile:
            raise
        except Exception as exc:
            raise UnsafeFile("PDF_INVALID", "PDF 损坏或无法读取") from exc
        if pages < 1:
            raise UnsafeFile("PDF_INVALID", "PDF 不包含页面")
        if pages > max_pdf_pages:
            raise UnsafeFile("PDF_TOO_MANY_PAGES", f"PDF 不能超过 {max_pdf_pages} 页")
        return FileInspection(ext, pages)
    if ext in {"png", "jpg", "jpeg"}:
        signature_ok = (
            content.startswith(b"\x89PNG\r\n\x1a\n")
            if ext == "png"
            else content.startswith(b"\xff\xd8\xff")
        )
        if not signature_ok:
            raise UnsafeFile("FILE_CONTENT_INVALID", "图片内容与格式不符")
        try:
            opened = Image.open(io.BytesIO(content))
            width, height = opened.size
            if width * height > max_image_pixels:
                raise UnsafeFile("IMAGE_TOO_MANY_PIXELS", "图片像素数超过限制")
            opened.verify()
            oriented = ImageOps.exif_transpose(Image.open(io.BytesIO(content)))
            width, height = oriented.size
        except UnsafeFile:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise UnsafeFile("IMAGE_INVALID", "图片损坏或无法读取") from exc
        return FileInspection(ext, 1, width, height)
    return inspect_docx(content)
