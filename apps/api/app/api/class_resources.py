import hashlib
import io
import uuid
from typing import Annotated, Any

from app.api.actor import Actor
from app.api.assignments import MIMES, owned, paper
from app.api.domain import ApiProblem, audit, owned_class
from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    ArchiveStatus,
    AssignmentClass,
    AssignmentStatus,
    ClassResource,
    ClassStudent,
    FileStatus,
    MembershipStatus,
    PaperPage,
    PaperVersion,
    StoredFile,
    Student,
    now_utc,
)
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["class-resources"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
RESOURCE_TYPES = {"exercise", "handout", "reference", "other"}


class AddResourcesInput(BaseModel):
    resource_ids: list[uuid.UUID]


def resource_json(row: ClassResource, stored: StoredFile) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "class_id": str(row.class_id),
        "title": row.title,
        "resource_type": row.resource_type,
        "page_count": row.page_count,
        "status": row.status,
        "student_visible": row.student_visible,
        "published_at": row.published_at,
        "file_name": stored.original_name,
        "content_type": stored.content_type,
        "size": stored.size,
        "checksum": stored.checksum,
        "created_at": row.created_at,
    }


@router.get("/classes/{class_id}/resources")
def list_class_resources(class_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    owned_class(db, actor.id, class_id)
    rows = db.execute(
        select(ClassResource, StoredFile)
        .join(StoredFile, StoredFile.id == ClassResource.stored_file_id)
        .where(
            ClassResource.class_id == class_id,
            ClassResource.owner_id == actor.id,
            ClassResource.status == "ready",
            StoredFile.status == FileStatus.ready,
        )
        .order_by(ClassResource.created_at.desc(), ClassResource.id)
    )
    return [resource_json(row, stored) for row, stored in rows]


@router.patch("/classes/{class_id}/resources/{resource_id}/publication")
def set_class_resource_publication(
    class_id: uuid.UUID,
    resource_id: uuid.UUID,
    data: dict[str, bool],
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    owned_class(db, actor.id, class_id)
    if set(data) != {"student_visible"}:
        raise ApiProblem(422, "CLASS_RESOURCE_PUBLICATION_INVALID", "发布参数无效")
    row = db.scalar(
        select(ClassResource)
        .where(
            ClassResource.id == resource_id,
            ClassResource.class_id == class_id,
            ClassResource.owner_id == actor.id,
            ClassResource.status == "ready",
        )
        .with_for_update()
    )
    if row is None:
        raise ApiProblem(404, "CLASS_RESOURCE_NOT_FOUND", "班级资料不存在")
    stored = db.get(StoredFile, row.stored_file_id)
    if stored is None or stored.status != FileStatus.ready:
        raise ApiProblem(409, "CLASS_RESOURCE_SOURCE_INVALID", "班级资料文件不可用")
    visible = bool(data["student_visible"])
    row.student_visible = visible
    row.published_at = now_utc() if visible else None
    row.published_by = actor.id if visible else None
    audit(
        db,
        actor.id,
        "class_resource.publish" if visible else "class_resource.unpublish",
        "class_resource",
        row.id,
        {"class_id": str(class_id), "student_visible": visible},
    )
    db.commit()
    return resource_json(row, stored)


def _student_resource(
    db: Session, actor_id: uuid.UUID, resource_id: uuid.UUID
) -> tuple[ClassResource, StoredFile]:
    row = db.execute(
        select(ClassResource, StoredFile)
        .join(StoredFile, StoredFile.id == ClassResource.stored_file_id)
        .join(ClassStudent, ClassStudent.class_id == ClassResource.class_id)
        .join(Student, Student.id == ClassStudent.student_id)
        .where(
            ClassResource.id == resource_id,
            ClassResource.student_visible.is_(True),
            ClassResource.status == "ready",
            StoredFile.status == FileStatus.ready,
            Student.user_id == actor_id,
            Student.status == ArchiveStatus.active,
            Student.owner_id == ClassResource.owner_id,
            ClassStudent.status == MembershipStatus.active,
        )
    ).first()
    if row is None:
        raise ApiProblem(404, "STUDENT_RESOURCE_NOT_FOUND", "资料不存在或尚未向学生发布")
    return row[0], row[1]


@router.get("/student/resources")
def list_student_resources(db: Db, actor: Actor) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ClassResource, StoredFile)
        .join(StoredFile, StoredFile.id == ClassResource.stored_file_id)
        .join(ClassStudent, ClassStudent.class_id == ClassResource.class_id)
        .join(Student, Student.id == ClassStudent.student_id)
        .where(
            ClassResource.student_visible.is_(True),
            ClassResource.status == "ready",
            StoredFile.status == FileStatus.ready,
            Student.user_id == actor.id,
            Student.status == ArchiveStatus.active,
            Student.owner_id == ClassResource.owner_id,
            ClassStudent.status == MembershipStatus.active,
        )
        .order_by(ClassResource.published_at.desc(), ClassResource.id.desc())
    ).all()
    unique: dict[uuid.UUID, dict[str, Any]] = {}
    for resource, stored in rows:
        unique.setdefault(resource.id, resource_json(resource, stored))
    return list(unique.values())


@router.get("/student/resources/{resource_id}/download")
def download_student_resource(
    resource_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> Response:
    _resource, stored = _student_resource(db, actor.id, resource_id)
    content = storage.get(stored.storage_key).read()
    if hashlib.sha256(content).hexdigest() != stored.checksum:
        raise ApiProblem(409, "CLASS_RESOURCE_CHANGED", "资料内容校验失败")
    safe_name = stored.original_name.replace('"', "")
    return Response(
        content=content,
        media_type=stored.content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post("/classes/{class_id}/resources", status_code=201)
async def upload_class_resource(
    class_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    file: Annotated[UploadFile, File()],
    resource_type: Annotated[str, Form()] = "exercise",
    title: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    klass = owned_class(db, actor.id, class_id)
    if klass.status != ArchiveStatus.active:
        raise ApiProblem(409, "CLASS_NOT_ACTIVE", "只能向活动班级添加资料")
    if resource_type not in RESOURCE_TYPES:
        raise ApiProblem(422, "CLASS_RESOURCE_TYPE_INVALID", "资料类型无效")
    settings = get_settings()
    content = await file.read(settings.assignment_max_file_bytes + 1)
    if not content:
        raise ApiProblem(422, "FILE_EMPTY", "文件为空")
    if len(content) > settings.assignment_max_file_bytes:
        raise ApiProblem(413, "FILE_TOO_LARGE", "文件超过大小限制")
    try:
        name = safe_filename(file.filename)
        inspection = inspect_upload(
            name,
            content,
            file.content_type,
            max_pdf_pages=settings.recognition_max_pdf_pages,
            max_image_pixels=settings.recognition_max_image_pixels,
            allow_docx=False,
        )
    except UnsafeFile as exc:
        status = 415 if exc.code in {"FILE_TYPE_INVALID", "FILE_CONTENT_INVALID"} else 422
        raise ApiProblem(status, exc.code, exc.message) from exc
    cleaned_title = (title or name.rsplit(".", 1)[0]).strip()
    if not cleaned_title or len(cleaned_title) > 200:
        raise ApiProblem(422, "CLASS_RESOURCE_TITLE_INVALID", "资料名称不能为空且不超过 200 字")
    checksum = hashlib.sha256(content).hexdigest()
    duplicate = db.scalar(
        select(ClassResource.id)
        .join(StoredFile, StoredFile.id == ClassResource.stored_file_id)
        .where(
            ClassResource.class_id == class_id,
            ClassResource.status == "ready",
            StoredFile.checksum == checksum,
            StoredFile.status == FileStatus.ready,
        )
    )
    if duplicate is not None:
        raise ApiProblem(409, "CLASS_RESOURCE_DUPLICATE", "该资料已在本班资料库中")
    key = f"class-resources/{actor.id}/{class_id}/{uuid.uuid4()}.{inspection.kind}"
    stored = StoredFile(
        owner_id=actor.id,
        storage_key=key,
        original_name=name,
        content_type=MIMES[inspection.kind],
        size=len(content),
        checksum=checksum,
        status=FileStatus.pending,
    )
    db.add(stored)
    db.flush()
    try:
        storage.put(key, io.BytesIO(content), len(content), stored.content_type)
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(key)
        except Exception:
            pass
        raise ApiProblem(503, "STORAGE_UNAVAILABLE", "资料保存失败") from exc
    stored.status = FileStatus.ready
    resource = ClassResource(
        owner_id=actor.id,
        class_id=class_id,
        stored_file_id=stored.id,
        title=cleaned_title,
        resource_type=resource_type,
        page_count=inspection.page_count,
        status="ready",
        metadata_={"organization": "file_validated", "question_extraction": "not_run"},
    )
    db.add(resource)
    db.flush()
    audit(
        db,
        actor.id,
        "class_resource.upload",
        "class_resource",
        resource.id,
        {"class_id": str(class_id), "resource_type": resource_type, "pages": inspection.page_count},
    )
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(key)
        except Exception:
            pass
        raise ApiProblem(503, "CLASS_RESOURCE_SAVE_FAILED", "资料保存失败") from exc
    return resource_json(resource, stored)


@router.get("/assignments/{assignment_id}/available-class-resources")
def list_assignment_resources(
    assignment_id: uuid.UUID, db: Db, actor: Actor
) -> list[dict[str, Any]]:
    assignment = owned(db, actor.id, assignment_id)
    class_ids = list(
        db.scalars(
            select(AssignmentClass.class_id).where(AssignmentClass.assignment_id == assignment.id)
        )
    )
    if not class_ids:
        return []
    rows = db.execute(
        select(ClassResource, StoredFile)
        .join(StoredFile, StoredFile.id == ClassResource.stored_file_id)
        .where(
            ClassResource.class_id.in_(class_ids),
            ClassResource.owner_id == actor.id,
            ClassResource.status == "ready",
            StoredFile.status == FileStatus.ready,
        )
        .order_by(ClassResource.title, ClassResource.id)
    )
    return [resource_json(row, stored) for row, stored in rows]


@router.post("/assignments/{assignment_id}/class-resources")
def add_resources_to_assignment(
    assignment_id: uuid.UUID,
    data: AddResourcesInput,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    assignment = owned(db, actor.id, assignment_id, lock=True)
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "只能为草稿作业选择资料")
    resource_ids = list(dict.fromkeys(data.resource_ids))
    if not resource_ids or len(resource_ids) > get_settings().assignment_max_files:
        raise ApiProblem(422, "CLASS_RESOURCE_SELECTION_INVALID", "请选择有效数量的资料")
    class_ids = set(
        db.scalars(
            select(AssignmentClass.class_id).where(AssignmentClass.assignment_id == assignment.id)
        )
    )
    rows = list(
        db.execute(
            select(ClassResource, StoredFile)
            .join(StoredFile, StoredFile.id == ClassResource.stored_file_id)
            .where(
                ClassResource.id.in_(resource_ids),
                ClassResource.class_id.in_(class_ids),
                ClassResource.owner_id == actor.id,
                ClassResource.status == "ready",
                StoredFile.status == FileStatus.ready,
            )
        )
    )
    if len(rows) != len(resource_ids):
        raise ApiProblem(422, "CLASS_RESOURCE_NOT_AVAILABLE", "所选资料不属于当前作业班级")
    active_paper = paper(db, assignment)
    if active_paper is None:
        active_paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            created_by=actor.id,
            source_type="class_resource",
        )
        db.add(active_paper)
        db.flush()
        assignment.active_paper_version_id = active_paper.id
    existing_files = int(
        db.scalar(
            select(func.count(func.distinct(PaperPage.stored_file_id))).where(
                PaperPage.paper_version_id == active_paper.id
            )
        )
        or 0
    )
    if existing_files + len(rows) > get_settings().assignment_max_files:
        raise ApiProblem(422, "FILE_LIMIT", "作业文件数量将超过上限")
    existing_pages = int(
        db.scalar(
            select(func.count(PaperPage.id)).where(PaperPage.paper_version_id == active_paper.id)
        )
        or 0
    )
    created_files: list[StoredFile] = []
    created_keys: list[str] = []
    try:
        for resource, source in rows:
            content = storage.get(source.storage_key).read()
            if hashlib.sha256(content).hexdigest() != source.checksum:
                raise ApiProblem(409, "CLASS_RESOURCE_CHANGED", "资料内容校验失败，请重新上传")
            suffix = source.original_name.rsplit(".", 1)[-1].lower()
            key = (
                f"assignments/{actor.id}/{assignment.id}/{active_paper.id}/{uuid.uuid4()}.{suffix}"
            )
            storage.put(key, io.BytesIO(content), len(content), source.content_type)
            created_keys.append(key)
            copied = StoredFile(
                owner_id=actor.id,
                storage_key=key,
                original_name=source.original_name,
                content_type=source.content_type,
                size=source.size,
                checksum=source.checksum,
                status=FileStatus.ready,
            )
            db.add(copied)
            db.flush()
            created_files.append(copied)
            for page_index in range(resource.page_count):
                existing_pages += 1
                db.add(
                    PaperPage(
                        paper_version_id=active_paper.id,
                        stored_file_id=copied.id,
                        page_number=existing_pages,
                        source_page_number=page_index + 1,
                        status="ready",
                    )
                )
        audit(
            db,
            actor.id,
            "assignment.class_resources.add",
            "assignment",
            assignment.id,
            {"resource_ids": [str(value) for value in resource_ids]},
        )
        db.commit()
    except ApiProblem:
        db.rollback()
        for key in created_keys:
            try:
                storage.delete(key)
            except Exception:
                pass
        raise
    except Exception as exc:
        db.rollback()
        for key in created_keys:
            try:
                storage.delete(key)
            except Exception:
                pass
        raise ApiProblem(503, "CLASS_RESOURCE_COPY_FAILED", "资料加入作业失败") from exc
    return {
        "files_created": len(created_files),
        "pages_created": sum(resource.page_count for resource, _source in rows),
    }
