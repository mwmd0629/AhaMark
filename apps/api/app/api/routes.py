import hashlib
import uuid
from typing import Annotated
from urllib.error import URLError
from urllib.request import urlopen

from app.api.actor import Actor
from app.api.assignments import router as assignments_router
from app.api.auth import router as auth_router
from app.api.domain import router as domain_router
from app.api.grading import router as grading_router
from app.api.recognition import router as recognition_router
from app.api.results import router as results_router
from app.core.config import get_settings
from app.db.session import get_db
from app.models import FileStatus, StoredFile
from app.recognition.pipeline import provider_from_settings
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select, text
from sqlalchemy.orm import Session

router = APIRouter()
router.include_router(auth_router)
router.include_router(domain_router)
router.include_router(assignments_router)
router.include_router(recognition_router)
router.include_router(grading_router)
router.include_router(results_router)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ahamark-api", "version": "0.1.0"}


@router.get("/ready")
def ready(db: Annotated[Session, Depends(get_db)]) -> dict[str, object]:
    settings = get_settings()
    capabilities: dict[str, dict[str, object]] = {}
    try:
        db.execute(text("SELECT 1"))
        capabilities["postgresql"] = {"status": "available"}
    except Exception as exc:
        capabilities["postgresql"] = {
            "status": "unavailable",
            "reason": type(exc).__name__,
        }
    try:
        from redis import Redis

        Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5).ping()
        capabilities["redis"] = {"status": "available"}
    except Exception as exc:
        capabilities["redis"] = {"status": "unavailable", "reason": type(exc).__name__}
    try:
        from workers.celery_app import celery_app

        replies = celery_app.control.inspect(timeout=0.75).ping()
        capabilities["celery_worker"] = {
            "status": "available" if replies else "unavailable",
            "workers": len(replies or {}),
        }
    except Exception as exc:
        capabilities["celery_worker"] = {
            "status": "unavailable",
            "reason": type(exc).__name__,
        }
    scheme = "https" if settings.minio_secure else "http"
    try:
        with urlopen(
            f"{scheme}://{settings.minio_endpoint}/minio/health/live", timeout=0.75
        ) as response:
            minio_ok = response.status == 200
        capabilities["minio"] = {"status": "available" if minio_ok else "degraded"}
    except (OSError, URLError) as exc:
        capabilities["minio"] = {"status": "unavailable", "reason": type(exc).__name__}
    provider = provider_from_settings(settings)
    ocr_available, ocr_reason = provider.available()
    capabilities["text_ocr"] = {
        "status": "available"
        if ocr_available and not provider.is_demo
        else ("degraded" if ocr_available else "unavailable"),
        "provider": provider.name,
        "version": provider.version,
        "demo": provider.is_demo,
        "reason": ocr_reason,
    }
    capabilities["formula_ocr"] = {
        "status": "unavailable",
        "reason": "未配置公式识别 Provider；数学字符仅作为普通文字并需人工复核",
    }
    required = ["postgresql", "redis", "celery_worker", "minio", "text_ocr"]
    status = (
        "available"
        if all(capabilities[name]["status"] == "available" for name in required)
        else "degraded"
    )
    return {"status": status, "capabilities": capabilities}


@router.post("/files", status_code=201)
async def upload_file(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    actor: Actor,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> dict[str, object]:
    s = get_settings()
    content = await file.read(s.max_upload_bytes + 1)
    if len(content) > s.max_upload_bytes:
        raise HTTPException(413, "文件超过大小限制")
    try:
        name = safe_filename(file.filename)
        inspection = inspect_upload(
            name,
            content,
            file.content_type,
            max_pdf_pages=s.recognition_max_pdf_pages,
            max_image_pixels=s.recognition_max_image_pixels,
        )
    except UnsafeFile as exc:
        raise HTTPException(
            422 if exc.code not in {"FILE_TYPE_INVALID"} else 415, exc.message
        ) from exc
    import io

    key = f"uploads/{actor.id}/{uuid.uuid4().hex}.{inspection.kind}"
    content_type = file.content_type or "application/octet-stream"
    meta = storage.put(key, io.BytesIO(content), len(content), content_type)
    stored = StoredFile(
        owner_id=actor.id,
        storage_key=key,
        original_name=name,
        content_type=content_type,
        size=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
        status=FileStatus.ready,
    )
    db.add(stored)
    db.commit()
    return {
        "key": meta.key,
        "id": str(stored.id),
        "name": name,
        "content_type": meta.content_type,
        "size": meta.size,
        "checksum": hashlib.sha256(content).hexdigest(),
    }


@router.get("/files/{key:path}/metadata")
def file_metadata(
    key: str,
    db: Annotated[Session, Depends(get_db)],
    actor: Actor,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> dict[str, object]:
    owned_file(db, actor, key)
    return storage.stat(key).__dict__


@router.delete("/files/{key:path}", status_code=204)
def delete_file(
    key: str,
    db: Annotated[Session, Depends(get_db)],
    actor: Actor,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> None:
    item = owned_file(db, actor, key)
    if not item.storage_key.startswith(f"uploads/{actor.id}/"):
        raise HTTPException(409, "业务文件必须从所属资源删除")
    storage.delete(key)
    db.delete(item)
    db.commit()


@router.post("/files/{key:path}/signed-url")
def signed_url(
    key: str,
    db: Annotated[Session, Depends(get_db)],
    actor: Actor,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> dict[str, str]:
    owned_file(db, actor, key)
    return {"url": storage.presigned_get(key)}


def owned_file(db: Session, actor: Actor, key: str) -> StoredFile:
    item = db.scalar(
        select(StoredFile).where(StoredFile.storage_key == key, StoredFile.owner_id == actor.id)
    )
    if item is None:
        raise HTTPException(404, "文件不存在")
    return item
