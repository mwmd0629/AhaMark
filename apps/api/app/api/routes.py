import hashlib
import uuid
from typing import Annotated

from app.api.actor import Actor
from app.api.admin_accounts import router as admin_accounts_router
from app.api.ai_grading import router as ai_grading_router
from app.api.answer_recognition import router as answer_recognition_router
from app.api.assignment_answer_rubric import router as assignment_answer_rubric_router
from app.api.assignment_central_review import router as assignment_central_review_router
from app.api.assignment_generation import router as assignment_generation_router
from app.api.assignments import router as assignments_router
from app.api.auth import router as auth_router
from app.api.class_resources import router as class_resources_router
from app.api.codex_local import router as codex_local_router
from app.api.demo_reset import router as demo_reset_router
from app.api.domain import router as domain_router
from app.api.formula_recognition import router as formula_recognition_router
from app.api.grading import router as grading_router
from app.api.math_validation import router as math_validation_router
from app.api.processing import router as processing_router
from app.api.question_structure import router as question_structure_router
from app.api.recognition import router as recognition_router
from app.api.results import router as results_router
from app.api.rubric_templates import router as rubric_templates_router
from app.api.structured_rubrics import router as structured_rubrics_router
from app.api.student_learning import router as student_learning_router
from app.api.student_portal import router as student_portal_router
from app.api.student_review_requests import router as student_review_requests_router
from app.api.student_submissions import router as student_submissions_router
from app.api.submission_processing import router as submission_processing_router
from app.api.teacher_practice import router as teacher_practice_router
from app.api.textbook_libraries import router as textbook_libraries_router
from app.core.config import get_settings
from app.core.readiness import dependency_readiness
from app.db.session import get_db
from app.models import FileStatus, StoredFile
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()
router.include_router(auth_router)
router.include_router(admin_accounts_router)
router.include_router(codex_local_router)
router.include_router(class_resources_router)
router.include_router(domain_router)
router.include_router(demo_reset_router)
router.include_router(assignments_router)
router.include_router(assignment_generation_router)
router.include_router(assignment_answer_rubric_router)
router.include_router(assignment_central_review_router)
router.include_router(answer_recognition_router)
router.include_router(ai_grading_router)
router.include_router(recognition_router)
router.include_router(formula_recognition_router)
router.include_router(grading_router)
router.include_router(math_validation_router)
router.include_router(processing_router)
router.include_router(question_structure_router)
router.include_router(submission_processing_router)
router.include_router(structured_rubrics_router)
router.include_router(rubric_templates_router)
router.include_router(textbook_libraries_router)
router.include_router(results_router)
router.include_router(student_portal_router)
router.include_router(student_learning_router)
router.include_router(student_review_requests_router)
router.include_router(student_submissions_router)
router.include_router(teacher_practice_router)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ahamark-api", "version": "0.1.0"}


@router.get("/ready")
def ready(db: Annotated[Session, Depends(get_db)]) -> JSONResponse:
    result = dependency_readiness(db, get_settings())
    return JSONResponse(result, status_code=200 if result["ready"] else 503)


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
    try:
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
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(key)
        except Exception:
            pass
        raise HTTPException(503, "文件保存失败，未保留对象或数据库记录") from exc
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
    return {"url": storage.presigned_get(key, get_settings().signed_url_expiry_seconds)}


def owned_file(db: Session, actor: Actor, key: str) -> StoredFile:
    item = db.scalar(
        select(StoredFile).where(StoredFile.storage_key == key, StoredFile.owner_id == actor.id)
    )
    if item is None:
        raise HTTPException(404, "文件不存在")
    return item
