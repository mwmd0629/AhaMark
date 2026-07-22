import io
import zipfile

import pytest
from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models import Status, User
from app.security.files import UnsafeFile, inspect_docx, inspect_upload, safe_filename
from app.storage.base import ObjectMetadata
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image


class MemoryStorage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        pass

    def put(self, key: str, data: io.BytesIO, size: int, content_type: str) -> ObjectMetadata:
        self.data[key] = data.read()
        return ObjectMetadata(key, size, content_type)

    def stat(self, key: str) -> ObjectMetadata:
        return ObjectMetadata(key, len(self.data[key]), "image/png")

    def get(self, key: str) -> io.BytesIO:
        return io.BytesIO(self.data[key])

    def delete(self, key: str) -> None:
        del self.data[key]

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return f"https://signed.invalid/{key}?expires={expires_seconds}"


def png_bytes(size: tuple[int, int] = (2, 2)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, "PNG")
    return output.getvalue()


def create_user(email: str) -> None:
    with SessionLocal() as db:
        db.add(
            User(
                email=email,
                password_hash=hash_password("secure-pass-123"),
                display_name="合成教师",
                status=Status.active,
            )
        )
        db.commit()


def login(email: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": "secure-pass-123"})
    assert response.status_code == 200
    return client


def test_content_validation_rejects_spoofing_bombs_and_archive_traversal() -> None:
    with pytest.raises(UnsafeFile, match="PDF"):
        inspect_upload(
            "fake.pdf",
            b"not a pdf",
            "application/pdf",
            max_pdf_pages=10,
            max_image_pixels=100,
        )
    with pytest.raises(UnsafeFile) as pixels:
        inspect_upload(
            "large.png",
            png_bytes((11, 10)),
            "image/png",
            max_pdf_pages=10,
            max_image_pixels=100,
        )
    assert pixels.value.code == "IMAGE_TOO_MANY_PIXELS"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("[Content_Types].xml", "types")
        value.writestr("word/document.xml", "document")
        value.writestr("../escape.txt", "unsafe")
    with pytest.raises(UnsafeFile) as traversal:
        inspect_docx(archive.getvalue())
    assert traversal.value.code == "ARCHIVE_PATH_INVALID"
    assert safe_filename("../../safe.png") == "safe.png"


def test_generic_file_routes_require_owner_and_hide_other_teacher() -> None:
    storage = MemoryStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        create_user("owner@example.com")
        create_user("other@example.com")
        owner, other = login("owner@example.com"), login("other@example.com")
        csrf = owner.cookies.get("ahamark_csrf") or ""
        uploaded = owner.post(
            "/files",
            headers={"x-csrf-token": csrf},
            files={"file": ("page.png", png_bytes(), "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        key = uploaded.json()["key"]
        assert owner.get(f"/files/{key}/metadata").status_code == 200
        assert other.get(f"/files/{key}/metadata").status_code == 404
        other_csrf = other.cookies.get("ahamark_csrf") or ""
        assert (
            other.post(f"/files/{key}/signed-url", headers={"x-csrf-token": other_csrf}).status_code
            == 404
        )
        assert (
            other.delete(f"/files/{key}", headers={"x-csrf-token": other_csrf}).status_code == 404
        )
        signed = owner.post(f"/files/{key}/signed-url", headers={"x-csrf-token": csrf})
        assert signed.status_code == 200 and signed.json()["url"].startswith(
            "https://signed.invalid/"
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)
