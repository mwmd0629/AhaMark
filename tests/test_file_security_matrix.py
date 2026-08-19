import io
import zipfile

import pytest
from app.security.files import (
    UnsafeFile,
    inspect_docx,
    inspect_pptx,
    inspect_upload,
    inspect_xlsx_archive,
    safe_filename,
)
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from reportlab.pdfgen import canvas


def pdf_bytes(pages: int = 1, *, encrypted: bool = False) -> bytes:
    output = io.BytesIO()
    if not encrypted and pages:
        document = canvas.Canvas(output, pagesize=(72, 72))
        for index in range(pages):
            document.drawString(5, 35, f"synthetic-{index}")
            document.showPage()
        document.save()
        return output.getvalue()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("synthetic")
    writer.write(output)
    return output.getvalue()


def image_bytes(kind: str = "PNG", size: tuple[int, int] = (4, 4)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, kind)
    return output.getvalue()


def office_bytes(kind: str, extras: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    required_by_kind = {
        "docx": {"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<document/>"},
        "xlsx": {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"},
        "pptx": {
            "[Content_Types].xml": b"<Types/>",
            "ppt/presentation.xml": b"<presentation/>",
        },
    }
    required = required_by_kind[kind]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in (required | (extras or {})).items():
            archive.writestr(name, value)
    return output.getvalue()


@pytest.mark.parametrize(
    ("name", "content", "mime", "code"),
    [
        ("empty.pdf", b"", "application/pdf", "FILE_CONTENT_INVALID"),
        ("fake.pdf", b"%PDF-not-real", "application/pdf", "PDF_INVALID"),
        ("cut.pdf", pdf_bytes()[:20], "application/pdf", "PDF_INVALID"),
        ("locked.pdf", pdf_bytes(encrypted=True), "application/pdf", "PDF_ENCRYPTED"),
        ("zero.pdf", pdf_bytes(0), "application/pdf", "PDF_INVALID"),
        ("many.pdf", pdf_bytes(3), "application/pdf", "PDF_TOO_MANY_PAGES"),
        ("page.png", image_bytes(), "image/jpeg", "FILE_TYPE_INVALID"),
        ("page.jpg", image_bytes(), "image/jpeg", "FILE_CONTENT_INVALID"),
        ("cut.png", image_bytes()[:20], "image/png", "IMAGE_INVALID"),
        ("large.png", image_bytes(size=(11, 10)), "image/png", "IMAGE_TOO_MANY_PIXELS"),
    ],
)
def test_pdf_and_image_fixture_matrix(name: str, content: bytes, mime: str, code: str) -> None:
    with pytest.raises(UnsafeFile) as caught:
        inspect_upload(
            name,
            content,
            mime,
            max_pdf_pages=2,
            max_image_pixels=100,
        )
    assert caught.value.code == code


def test_blank_pdf_page_is_rejected() -> None:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    with pytest.raises(UnsafeFile) as caught:
        inspect_upload(
            "blank.pdf",
            output.getvalue(),
            "application/pdf",
            max_pdf_pages=2,
            max_image_pixels=100,
        )
    assert caught.value.code == "PDF_EMPTY"


def test_pdf_active_content_is_rejected() -> None:
    source = PdfReader(io.BytesIO(pdf_bytes()))
    writer = PdfWriter()
    writer.clone_document_from_reader(source)
    writer.root_object[NameObject("/OpenAction")] = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('unsafe')"),
        }
    )
    output = io.BytesIO()
    writer.write(output)

    with pytest.raises(UnsafeFile) as caught:
        inspect_upload(
            "active.pdf",
            output.getvalue(),
            "application/pdf",
            max_pdf_pages=2,
            max_image_pixels=100,
        )
    assert caught.value.code == "PDF_ACTIVE_CONTENT_FORBIDDEN"


def test_valid_pdf_and_images_matrix() -> None:
    assert (
        inspect_upload(
            "one.pdf", pdf_bytes(), "application/pdf", max_pdf_pages=2, max_image_pixels=100
        ).page_count
        == 1
    )
    assert (
        inspect_upload(
            "two.pdf", pdf_bytes(2), "application/pdf", max_pdf_pages=2, max_image_pixels=100
        ).page_count
        == 2
    )
    assert (
        inspect_upload(
            "image.png", image_bytes(), "image/png", max_pdf_pages=2, max_image_pixels=100
        ).kind
        == "png"
    )
    assert (
        inspect_upload(
            "image.jpeg",
            image_bytes("JPEG"),
            "image/jpeg",
            max_pdf_pages=2,
            max_image_pixels=100,
        ).kind
        == "jpeg"
    )


@pytest.mark.parametrize(
    ("kind", "extras", "code"),
    [
        ("docx", {"../escape": b"x"}, "ARCHIVE_PATH_INVALID"),
        ("docx", {"word/vbaProject.bin": b"x"}, "OFFICE_MACRO_FORBIDDEN"),
        (
            "docx",
            {
                "word/_rels/document.xml.rels": (
                    b'<Relationships><Relationship TargetMode="External"/></Relationships>'
                )
            },
            "OFFICE_EXTERNAL_LINK_FORBIDDEN",
        ),
        ("docx", {"word/broken.xml": b"<broken"}, "OFFICE_XML_INVALID"),
        ("xlsx", {"xl/vbaProject.bin": b"x"}, "OFFICE_MACRO_FORBIDDEN"),
        (
            "xlsx",
            {
                "xl/_rels/workbook.xml.rels": (
                    b"<Relationships><Relationship TargetMode='External'/></Relationships>"
                )
            },
            "OFFICE_EXTERNAL_LINK_FORBIDDEN",
        ),
        (
            "docx",
            {
                "word/_rels/document.xml.rels": (
                    b'<Relationships><Relationship targetMode=" external "/></Relationships>'
                )
            },
            "OFFICE_EXTERNAL_LINK_FORBIDDEN",
        ),
        (
            "docx",
            {"word/embeddings/payload.bin": b"unsafe"},
            "OFFICE_ACTIVE_CONTENT_FORBIDDEN",
        ),
        ("xlsx", {"xl/broken.xml": b"<broken"}, "OFFICE_XML_INVALID"),
    ],
)
def test_office_fixture_matrix(kind: str, extras: dict[str, bytes], code: str) -> None:
    with pytest.raises(UnsafeFile) as caught:
        if kind == "docx":
            inspect_docx(office_bytes(kind, extras))
        else:
            inspect_xlsx_archive(office_bytes(kind, extras))
    assert caught.value.code == code


def test_office_missing_core_fake_zip_and_filename_matrix() -> None:
    assert inspect_docx(office_bytes("docx")).kind == "docx"
    assert inspect_pptx(office_bytes("pptx")).kind == "pptx"
    inspect_xlsx_archive(office_bytes("xlsx"))
    fake = io.BytesIO()
    with zipfile.ZipFile(fake, "w") as archive:
        archive.writestr("unrelated", "x")
    for inspect in (inspect_docx, inspect_xlsx_archive):
        with pytest.raises(UnsafeFile) as caught:
            inspect(fake.getvalue())
        assert caught.value.code == "OFFICE_INVALID"
    assert safe_filename("../../synthetic.png") == "synthetic.png"
    with pytest.raises(UnsafeFile, match="控制字符"):
        safe_filename("bad\x00.png")
    with pytest.raises(UnsafeFile) as long_name:
        safe_filename("a" * 256)
    assert long_name.value.code == "FILE_NAME_TOO_LONG"


def test_office_entry_count_and_compression_ratio_limits() -> None:
    too_many = io.BytesIO()
    with zipfile.ZipFile(too_many, "w") as archive:
        for index in range(2001):
            archive.writestr(f"entry-{index}", b"")
    with pytest.raises(UnsafeFile) as entries:
        inspect_docx(too_many.getvalue())
    assert entries.value.code == "ARCHIVE_TOO_MANY_ENTRIES"

    ratio = office_bytes("docx", {"word/large.bin": b"0" * (11 * 1024 * 1024)})
    with pytest.raises(UnsafeFile) as compressed:
        inspect_docx(ratio)
    assert compressed.value.code == "ARCHIVE_RATIO_INVALID"


def test_submission_batch_storage_failure_removes_all_written_objects() -> None:
    from app.main import app
    from app.models import StoredFile, Submission, SubmissionPage
    from app.storage.dependencies import get_storage
    from sqlalchemy import func, select
    from test_submission_workflow import client, png, workflow

    db, _old_storage, batch_id, _submission_id, _question_id = workflow()

    class FailingStorage:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}
            self.put_count = 0

        def put(self, key: str, data: io.BytesIO, size: int, content_type: str) -> object:
            self.put_count += 1
            self.objects[key] = data.read()
            if self.put_count == 2:
                raise OSError("synthetic second write failure")
            return object()

        def delete(self, key: str) -> None:
            self.objects.pop(key, None)

    storage = FailingStorage()
    before = {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (StoredFile, Submission, SubmissionPage)
    }
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        response = client.post(
            f"/api/grading-batches/{batch_id}/files",
            files=[
                ("files", ("0001-new-a.png", png("black"), "image/png")),
                ("files", ("0001-new-b.png", png("navy"), "image/png")),
            ],
        )
        assert response.status_code == 503
        assert storage.objects == {}
        after = {
            model: db.scalar(select(func.count()).select_from(model))
            for model in (StoredFile, Submission, SubmissionPage)
        }
        assert after == before
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


@pytest.mark.parametrize("invalid_index", [0, 1, 2])
def test_submission_batch_prevalidation_writes_nothing_for_any_invalid_position(
    invalid_index: int,
) -> None:
    from app.main import app
    from app.models import StoredFile, Submission, SubmissionPage
    from app.storage.dependencies import get_storage
    from sqlalchemy import func, select
    from test_assignments import FakeStorage
    from test_submission_workflow import client, png, workflow

    db, _old_storage, batch_id, _submission_id, _question_id = workflow()
    storage = FakeStorage()
    before = {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (StoredFile, Submission, SubmissionPage)
    }
    files = [
        ("files", (f"0001-{index}.png", png(color), "image/png"))
        for index, color in enumerate(("black", "navy", "gray"))
    ]
    files[invalid_index] = (
        "files",
        (f"0001-invalid-{invalid_index}.png", b"not-an-image", "image/png"),
    )
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        response = client.post(f"/api/grading-batches/{batch_id}/files", files=files)
        assert response.status_code == 415
        assert storage.objects == {}
        after = {
            model: db.scalar(select(func.count()).select_from(model))
            for model in (StoredFile, Submission, SubmissionPage)
        }
        assert after == before
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_submission_batch_rejects_duplicate_checksum_before_storage() -> None:
    from app.main import app
    from app.storage.dependencies import get_storage
    from test_assignments import FakeStorage
    from test_submission_workflow import client, png, workflow

    db, _old_storage, batch_id, _submission_id, _question_id = workflow()
    storage = FakeStorage()
    duplicate = png("black")
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        response = client.post(
            f"/api/grading-batches/{batch_id}/files",
            files=[
                ("files", ("0001-duplicate-a.png", duplicate, "image/png")),
                ("files", ("0001-duplicate-b.png", duplicate, "image/png")),
            ],
        )
        assert response.status_code == 409
        assert storage.objects == {}
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_csv_formula_injection_prefix_matrix() -> None:
    from test_assignments import active_class, actor_and_db
    from test_classes import client

    actor, db = actor_and_db()
    school_class = active_class(db, actor.id, "安全导入班级")
    rows = [
        "姓名,学号",
        "=cmd,1001",
        "+cmd,1002",
        "-cmd,1003",
        "@cmd,1004",
        "\tcmd,1005",
    ]
    try:
        response = client.post(
            f"/api/classes/{school_class.id}/imports",
            files={"file": ("synthetic.csv", "\n".join(rows).encode(), "text/csv")},
        )
        assert response.status_code == 201
        assert response.json()["invalid_rows"] == 5
        assert all(
            item["errors"][0]["code"] == "FORMULA_INJECTION" for item in response.json()["rows"]
        )
    finally:
        db.close()
