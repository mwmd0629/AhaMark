from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.auth import hash_password
from app.api.domain import ApiProblem, audit
from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    Assignment,
    AssignmentClass,
    AssignmentStatus,
    ClassStudent,
    FileStatus,
    GradeRelease,
    GradeReleaseItem,
    GradingBatch,
    MembershipStatus,
    Role,
    SchoolClass,
    ScoreRevision,
    Status,
    StoredFile,
    Student,
    StudentAccountLink,
    StudentAnswer,
    StudentLearningAnalysis,
    StudentTeacherReviewRequest,
    Submission,
    SubmissionPage,
    SubmissionScoreSnapshot,
    TeacherReview,
    TeachingResource,
    User,
    UserRole,
    UserSession,
    WrongQuestionAIJob,
    WrongQuestionMessage,
    WrongQuestionThread,
    now_utc,
)
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.security.identity import (
    normalize_email,
    normalize_login_name,
    normalize_recovery_email,
)
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from app.student_learning.jobs import student_learning_source_hash
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _active_profiles(db: Session, user_id: uuid.UUID) -> list[tuple[StudentAccountLink, Student]]:
    return [
        (row[0], row[1])
        for row in db.execute(
            select(StudentAccountLink, Student)
            .join(Student, Student.id == StudentAccountLink.student_id)
            .where(
                StudentAccountLink.user_id == user_id,
                StudentAccountLink.status == "active",
                Student.status == "active",
            )
        ).all()
    ]


def _profile_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    return {student.id for _, student in _active_profiles(db, user_id)}


def _student_for_class(
    db: Session, user_id: uuid.UUID, class_id: uuid.UUID
) -> tuple[Student, SchoolClass]:
    rows = db.execute(
        select(Student, SchoolClass)
        .join(StudentAccountLink, StudentAccountLink.student_id == Student.id)
        .join(ClassStudent, ClassStudent.student_id == Student.id)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(
            StudentAccountLink.user_id == user_id,
            StudentAccountLink.status == "active",
            Student.status == "active",
            ClassStudent.class_id == class_id,
            ClassStudent.status == MembershipStatus.active,
            SchoolClass.status == "active",
            SchoolClass.owner_id == Student.owner_id,
        )
    ).all()
    if not rows:
        raise ApiProblem(403, "STUDENT_CLASS_ACCESS_DENIED", "当前学生不属于该班级")
    if len(rows) != 1:
        raise ApiProblem(409, "STUDENT_CLASS_IDENTITY_AMBIGUOUS", "该班级存在重复学生身份绑定")
    return rows[0][0], rows[0][1]


def _owned_student(db: Session, teacher_id: uuid.UUID, student_id: uuid.UUID) -> Student:
    student = db.scalar(
        select(Student).where(Student.id == student_id, Student.owner_id == teacher_id)
    )
    if student is None:
        raise ApiProblem(404, "STUDENT_NOT_FOUND", "学生不存在")
    return student


class StudentAccountInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    recovery_email: str | None = Field(
        None,
        max_length=320,
        validation_alias=AliasChoices("recovery_email", "email"),
    )
    display_name: str | None = Field(None, min_length=1, max_length=120)
    temporary_password: str | None = Field(None, min_length=8, max_length=256)

    @field_validator("recovery_email", mode="before")
    @classmethod
    def valid_email(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str):
            raise ValueError("安全邮箱格式无效")
        return normalize_recovery_email(value)

    @property
    def email(self) -> str | None:
        """Compatibility accessor for older internal callers."""

        return self.recovery_email


@router.post("/students/{student_id}/account-link", status_code=201, tags=["student-admin"])
def create_student_account_link(
    student_id: uuid.UUID, data: StudentAccountInput, db: Db, actor: Actor
) -> dict[str, Any]:
    student = _owned_student(db, actor.id, student_id)
    try:
        login_name = normalize_login_name(student.student_number)
    except ValueError as exc:
        raise ApiProblem(422, "STUDENT_LOGIN_ID_INVALID", str(exc)) from exc
    login_name_owner = db.scalar(select(User).where(User.login_name == login_name))
    if (
        student.email
        and data.recovery_email is not None
        and normalize_email(student.email) != data.recovery_email
    ):
        raise ApiProblem(
            409,
            "STUDENT_EMAIL_MISMATCH",
            "安全邮箱必须与学生档案邮箱一致；请先由教师确认并更新档案",
        )
    existing_link = db.scalar(
        select(StudentAccountLink).where(StudentAccountLink.student_id == student.id)
    )
    if existing_link is not None:
        linked_user = db.get(User, existing_link.user_id)
        if linked_user is not None and linked_user.email == data.recovery_email:
            if login_name_owner is not None and login_name_owner.id != linked_user.id:
                raise ApiProblem(
                    409,
                    "STUDENT_LOGIN_ID_CONFLICT",
                    "该学号已被其他学生账号使用，请先核对或修改学号",
                )
            linked_user.login_name = login_name
            if existing_link.status != "active":
                existing_link.status, existing_link.revoked_at = "active", None
                audit(
                    db,
                    actor.id,
                    "student_account.reactivate",
                    "student",
                    student.id,
                    {"user_id": str(linked_user.id)},
                )
            db.commit()
            return _account_link_view(db, existing_link, created_user=False)
        raise ApiProblem(409, "STUDENT_ALREADY_LINKED", "该学生档案已绑定其他账号")

    if login_name_owner is not None:
        raise ApiProblem(
            409,
            "STUDENT_LOGIN_ID_CONFLICT",
            "该学号已被其他学生账号使用，请先核对或修改学号",
        )
    user = (
        db.scalar(select(User).where(User.email == data.recovery_email))
        if data.recovery_email is not None
        else None
    )
    created_user = user is None
    if user is None:
        if data.temporary_password is None:
            raise ApiProblem(422, "TEMPORARY_PASSWORD_REQUIRED", "创建新学生账号必须设置临时密码")
        user = User(
            email=data.recovery_email,
            login_name=login_name,
            display_name=(data.display_name or student.name).strip(),
            password_hash=hash_password(data.temporary_password),
            must_change_password=True,
            status=Status.active,
        )
        db.add(user)
        db.flush()
    else:
        if data.temporary_password is not None:
            raise ApiProblem(409, "EXISTING_PASSWORD_IMMUTABLE", "不能由教师重置既有账号密码")
        if not student.email:
            raise ApiProblem(
                409,
                "EXISTING_ACCOUNT_REQUIRES_VERIFIED_STUDENT_EMAIL",
                "绑定已有账号前必须先由管理员核验并登记学生邮箱",
            )
        if user.status != Status.active or user.id == actor.id:
            raise ApiProblem(409, "USER_NOT_LINKABLE", "该账号不能绑定为学生账号")
        role_names = set(
            db.scalars(
                select(Role.name)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user.id)
            ).all()
        )
        if role_names != {"student"}:
            raise ApiProblem(
                409,
                "EXISTING_ACCOUNT_NOT_STUDENT_ONLY",
                "只有管理员已预置且仅具有学生角色的账号可以绑定",
            )
        owns_teacher_data = db.scalar(
            select(SchoolClass.id).where(SchoolClass.owner_id == user.id).limit(1)
        )
        if owns_teacher_data:
            raise ApiProblem(409, "TEACHER_ACCOUNT_NOT_LINKABLE", "教师账号不能绑定为学生账号")
        other_link = db.scalar(
            select(StudentAccountLink).where(StudentAccountLink.user_id == user.id)
        )
        if other_link is not None:
            raise ApiProblem(409, "USER_ALREADY_LINKED", "该账号已绑定其他学生档案")
        if user.login_name is not None and user.login_name != login_name:
            raise ApiProblem(409, "USER_LOGIN_ID_IMMUTABLE", "该预置账号已使用其他登录账号")
        user.login_name = login_name

    student_role = db.scalar(select(Role).where(Role.name == "student"))
    if student_role is None:
        student_role = Role(name="student", description="学生端账号")
        db.add(student_role)
        db.flush()
    if db.get(UserRole, (user.id, student_role.id)) is None:
        db.add(UserRole(user_id=user.id, role_id=student_role.id))
    link = StudentAccountLink(user_id=user.id, student_id=student.id, linked_by=actor.id)
    db.add(link)
    db.flush()
    audit(db, actor.id, "student_account.link", "student", student.id, {"user_id": str(user.id)})
    db.commit()
    db.refresh(link)
    return _account_link_view(db, link, created_user=created_user)


def _account_link_view(
    db: Session, link: StudentAccountLink, *, created_user: bool
) -> dict[str, Any]:
    user, student = db.get(User, link.user_id), db.get(Student, link.student_id)
    return {
        "id": str(link.id),
        "user_id": str(link.user_id),
        "student_id": str(link.student_id),
        "email": user.email if user else None,
        "recovery_email": user.email if user else None,
        "login_name": user.login_name if user else None,
        "recovery_email_verified": bool(
            user and user.email is not None and user.email_verified_at is not None
        ),
        "student_name": student.name if student else None,
        "status": link.status,
        "created_user": created_user,
        "must_change_password": user.must_change_password if user else None,
        "created_at": link.created_at,
    }


@router.delete("/students/{student_id}/account-link", status_code=204, tags=["student-admin"])
def revoke_student_account_link(student_id: uuid.UUID, db: Db, actor: Actor) -> None:
    student = _owned_student(db, actor.id, student_id)
    candidate = db.scalar(
        select(StudentAccountLink).where(StudentAccountLink.student_id == student.id)
    )
    if candidate is None:
        raise ApiProblem(404, "STUDENT_ACCOUNT_LINK_NOT_FOUND", "学生账号绑定不存在")
    db.scalar(select(User.id).where(User.id == candidate.user_id).with_for_update())
    link = db.scalar(
        select(StudentAccountLink).where(StudentAccountLink.id == candidate.id).with_for_update()
    )
    if link is None:
        raise ApiProblem(404, "STUDENT_ACCOUNT_LINK_NOT_FOUND", "学生账号绑定不存在")
    link.status, link.revoked_at = "revoked", now_utc()
    threads = db.scalars(
        select(WrongQuestionThread).where(
            WrongQuestionThread.user_id == link.user_id,
            WrongQuestionThread.student_id == student.id,
        )
    ).all()
    thread_ids = [thread.id for thread in threads]
    for thread in threads:
        thread.status = "closed"
    if thread_ids:
        jobs = db.scalars(
            select(WrongQuestionAIJob).where(
                WrongQuestionAIJob.thread_id.in_(thread_ids),
                WrongQuestionAIJob.status.in_(["queued", "running"]),
            )
        ).all()
        for job in jobs:
            job.status = "cancelled"
            job.error_code = "STUDENT_ACCOUNT_REVOKED"
            job.error_message = "The linked student account was revoked."
            job.completed_at = now_utc()
    analyses = db.scalars(
        select(StudentLearningAnalysis).where(
            StudentLearningAnalysis.user_id == link.user_id,
            StudentLearningAnalysis.student_id == student.id,
            StudentLearningAnalysis.status.in_(["queued", "running"]),
        )
    ).all()
    for analysis in analyses:
        analysis.status = "failed"
        analysis.error_code = "STUDENT_ACCOUNT_REVOKED"
    sessions = db.scalars(
        select(UserSession).where(
            UserSession.user_id == link.user_id,
            UserSession.revoked_at.is_(None),
        )
    ).all()
    for session in sessions:
        session.revoked_at = now_utc()
    audit(db, actor.id, "student_account.revoke", "student", student.id, {})
    db.commit()


class TeachingResourceInput(BaseModel):
    class_id: uuid.UUID
    assignment_id: uuid.UUID | None = None
    resource_type: Literal["ppt", "handout", "reference", "web", "other"]
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    stored_file_id: uuid.UUID | None = None
    external_url: str | None = Field(None, max_length=2048)
    sort_order: int = Field(0, ge=-10000, le=10000)

    @model_validator(mode="after")
    def one_target(self) -> TeachingResourceInput:
        if (self.stored_file_id is None) == (self.external_url is None):
            raise ValueError("stored_file_id 与 external_url 必须且只能填写一个")
        if self.external_url:
            value = self.external_url.strip()
            if not value.startswith("https://"):
                raise ValueError("external_url 仅支持 HTTPS")
            self.external_url = value
        self.title = self.title.strip()
        return self


class TeachingResourcePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    resource_type: Literal["ppt", "handout", "reference", "web", "other"] | None = None
    sort_order: int | None = Field(None, ge=-10000, le=10000)


def _owned_class(db: Session, owner_id: uuid.UUID, class_id: uuid.UUID) -> SchoolClass:
    item = db.scalar(
        select(SchoolClass).where(SchoolClass.id == class_id, SchoolClass.owner_id == owner_id)
    )
    if item is None:
        raise ApiProblem(404, "CLASS_NOT_FOUND", "班级不存在")
    return item


def _owned_resource(db: Session, owner_id: uuid.UUID, resource_id: uuid.UUID) -> TeachingResource:
    item = db.scalar(
        select(TeachingResource).where(
            TeachingResource.id == resource_id, TeachingResource.owner_id == owner_id
        )
    )
    if item is None:
        raise ApiProblem(404, "TEACHING_RESOURCE_NOT_FOUND", "教学资源不存在")
    return item


def _validate_resource_target(
    db: Session, actor_id: uuid.UUID, data: TeachingResourceInput
) -> None:
    _owned_class(db, actor_id, data.class_id)
    if data.assignment_id:
        linked = db.scalar(
            select(Assignment.id)
            .join(AssignmentClass, AssignmentClass.assignment_id == Assignment.id)
            .where(
                Assignment.id == data.assignment_id,
                Assignment.owner_id == actor_id,
                AssignmentClass.class_id == data.class_id,
            )
        )
        if linked is None:
            raise ApiProblem(422, "RESOURCE_ASSIGNMENT_CLASS_MISMATCH", "作业未分配到指定班级")
    if data.stored_file_id:
        file = db.scalar(
            select(StoredFile)
            .where(
                StoredFile.id == data.stored_file_id,
                StoredFile.owner_id == actor_id,
                StoredFile.status == FileStatus.ready,
                StoredFile.storage_key.startswith(f"teaching-resources/{actor_id}/"),
            )
            .with_for_update()
        )
        if file is None:
            raise ApiProblem(422, "RESOURCE_FILE_NOT_AVAILABLE", "资源文件不存在或无权使用")
        already_used = db.scalar(
            select(TeachingResource.id)
            .where(TeachingResource.stored_file_id == data.stored_file_id)
            .limit(1)
        )
        if already_used is not None:
            raise ApiProblem(409, "RESOURCE_FILE_ALREADY_USED", "资源文件已经创建过教学资源")


def _resource_view(db: Session, item: TeachingResource) -> dict[str, Any]:
    file = db.get(StoredFile, item.stored_file_id) if item.stored_file_id else None
    school_class = db.get(SchoolClass, item.class_id)
    return {
        "id": str(item.id),
        "class_id": str(item.class_id),
        "class_name": school_class.name if school_class else None,
        "subject": school_class.subject if school_class else None,
        "assignment_id": str(item.assignment_id) if item.assignment_id else None,
        "resource_type": item.resource_type,
        "title": item.title,
        "description": item.description,
        "stored_file_id": str(item.stored_file_id) if item.stored_file_id else None,
        "file_name": file.original_name if file else None,
        "content_type": file.content_type if file else None,
        "external_url": item.external_url,
        "status": item.status,
        "sort_order": item.sort_order,
        "published_at": item.published_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _unreferenced_teaching_uploads(db: Session, owner_id: uuid.UUID) -> list[StoredFile]:
    return list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.owner_id == owner_id,
                StoredFile.status == FileStatus.ready,
                StoredFile.storage_key.startswith(f"teaching-resources/{owner_id}/"),
                ~select(TeachingResource.id)
                .where(TeachingResource.stored_file_id == StoredFile.id)
                .exists(),
            )
        ).all()
    )


@router.post("/teaching-resources/files", status_code=201, tags=["teaching-resources"])
async def upload_teaching_resource_file(
    file: Annotated[UploadFile, File()],
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    settings = get_settings()
    db.scalar(select(User.id).where(User.id == actor.id).with_for_update())
    stale_before = now_utc() - timedelta(hours=1)
    stale_files = [
        item
        for item in _unreferenced_teaching_uploads(db, actor.id)
        if _utc(item.created_at) < stale_before
    ]
    for item in stale_files[:20]:
        try:
            storage.delete(item.storage_key)
        except Exception:
            continue
        item.status = FileStatus.deleted
    if len(_unreferenced_teaching_uploads(db, actor.id)) >= 20:
        db.commit()
        raise ApiProblem(
            429,
            "RESOURCE_UPLOAD_QUOTA_EXCEEDED",
            "待创建的教学资源文件过多，请稍后重试或联系管理员清理",
        )
    db.commit()
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise ApiProblem(413, "RESOURCE_FILE_TOO_LARGE", "教学资源文件超过大小限制")
    try:
        name = safe_filename(file.filename)
        inspection = inspect_upload(
            name,
            content,
            file.content_type,
            max_pdf_pages=settings.recognition_max_pdf_pages,
            max_image_pixels=settings.recognition_max_image_pixels,
            allow_docx=True,
            allow_pptx=True,
        )
    except UnsafeFile as exc:
        raise ApiProblem(
            422 if exc.code != "FILE_TYPE_INVALID" else 415,
            exc.code,
            exc.message,
        ) from exc
    key = f"teaching-resources/{actor.id}/{uuid.uuid4().hex}.{inspection.kind}"
    content_type = file.content_type or "application/octet-stream"
    try:
        metadata = storage.put(key, io.BytesIO(content), len(content), content_type)
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
        db.refresh(stored)
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(key)
        except Exception:
            pass
        raise ApiProblem(503, "RESOURCE_FILE_SAVE_FAILED", "教学资源文件保存失败") from exc
    return {
        "key": metadata.key,
        "id": str(stored.id),
        "name": name,
        "content_type": metadata.content_type,
        "size": metadata.size,
        "checksum": stored.checksum,
    }


@router.delete(
    "/teaching-resources/files/{file_id}",
    status_code=204,
    tags=["teaching-resources"],
)
def delete_teaching_resource_upload(
    file_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> None:
    stored = db.scalar(
        select(StoredFile)
        .where(
            StoredFile.id == file_id,
            StoredFile.owner_id == actor.id,
            StoredFile.storage_key.startswith(f"teaching-resources/{actor.id}/"),
        )
        .with_for_update()
    )
    if stored is None:
        raise ApiProblem(404, "RESOURCE_FILE_NOT_FOUND", "待创建的资源文件不存在")
    if stored.status == FileStatus.pending:
        raise ApiProblem(409, "RESOURCE_FILE_DELETE_IN_PROGRESS", "资源文件正在删除")
    if stored.status != FileStatus.ready:
        raise ApiProblem(404, "RESOURCE_FILE_NOT_FOUND", "待创建的资源文件不存在")
    referenced = db.scalar(
        select(TeachingResource.id).where(TeachingResource.stored_file_id == stored.id).limit(1)
    )
    if referenced is not None:
        raise ApiProblem(409, "RESOURCE_FILE_ALREADY_USED", "资源文件已被教学资源引用")
    stored.status = FileStatus.pending
    db.commit()
    try:
        storage.delete(stored.storage_key)
    except Exception as exc:
        with db.begin():
            db.execute(
                update(StoredFile)
                .where(
                    StoredFile.id == file_id,
                    StoredFile.status == FileStatus.pending,
                )
                .values(status=FileStatus.ready)
                .execution_options(synchronize_session=False)
            )
        raise ApiProblem(503, "RESOURCE_FILE_DELETE_FAILED", "资源文件删除失败") from exc
    with db.begin():
        db.execute(
            update(StoredFile)
            .where(
                StoredFile.id == file_id,
                StoredFile.status == FileStatus.pending,
            )
            .values(status=FileStatus.deleted)
            .execution_options(synchronize_session=False)
        )


@router.get("/teaching-resources", tags=["teaching-resources"])
def list_teaching_resources(
    db: Db,
    actor: Actor,
    class_id: uuid.UUID | None = None,
    status: Literal["draft", "published", "archived"] | None = None,
) -> list[dict[str, Any]]:
    query = select(TeachingResource).where(TeachingResource.owner_id == actor.id)
    if class_id:
        query = query.where(TeachingResource.class_id == class_id)
    if status:
        query = query.where(TeachingResource.status == status)
    else:
        query = query.where(TeachingResource.status != "archived")
    items = db.scalars(
        query.order_by(TeachingResource.sort_order, TeachingResource.created_at.desc())
    ).all()
    return [_resource_view(db, item) for item in items]


@router.post("/teaching-resources", status_code=201, tags=["teaching-resources"])
def create_teaching_resource(data: TeachingResourceInput, db: Db, actor: Actor) -> dict[str, Any]:
    # Share the teacher row mutex with upload/abandoned-file cleanup before
    # locking the StoredFile. This prevents cleanup from deleting a file while
    # it is being attached to a new resource.
    db.scalar(select(User.id).where(User.id == actor.id).with_for_update())
    _validate_resource_target(db, actor.id, data)
    item = TeachingResource(owner_id=actor.id, created_by=actor.id, **data.model_dump())
    db.add(item)
    try:
        db.flush()
        audit(db, actor.id, "teaching_resource.create", "teaching_resource", item.id, {})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiProblem(409, "RESOURCE_FILE_ALREADY_USED", "资源文件已经创建过教学资源") from exc
    db.refresh(item)
    return _resource_view(db, item)


@router.get("/teaching-resources/{resource_id}", tags=["teaching-resources"])
def get_teaching_resource(resource_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return _resource_view(db, _owned_resource(db, actor.id, resource_id))


@router.patch("/teaching-resources/{resource_id}", tags=["teaching-resources"])
def update_teaching_resource(
    resource_id: uuid.UUID, data: TeachingResourcePatch, db: Db, actor: Actor
) -> dict[str, Any]:
    item = _owned_resource(db, actor.id, resource_id)
    if item.status == "archived":
        raise ApiProblem(409, "RESOURCE_ARCHIVED", "已归档资源不能修改")
    changes = data.model_dump(exclude_unset=True)
    if "title" in changes:
        changes["title"] = changes["title"].strip()
    for field, value in changes.items():
        setattr(item, field, value)
    audit(
        db,
        actor.id,
        "teaching_resource.update",
        "teaching_resource",
        item.id,
        {"fields": sorted(changes)},
    )
    db.commit()
    return _resource_view(db, item)


@router.post("/teaching-resources/{resource_id}/publish", tags=["teaching-resources"])
def publish_teaching_resource(resource_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = _owned_resource(db, actor.id, resource_id)
    if item.status == "archived":
        raise ApiProblem(409, "RESOURCE_ARCHIVED", "已归档资源不能发布")
    item.status, item.published_at = "published", now_utc()
    audit(db, actor.id, "teaching_resource.publish", "teaching_resource", item.id, {})
    db.commit()
    return _resource_view(db, item)


@router.post("/teaching-resources/{resource_id}/unpublish", tags=["teaching-resources"])
def unpublish_teaching_resource(resource_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = _owned_resource(db, actor.id, resource_id)
    if item.status != "published":
        raise ApiProblem(409, "RESOURCE_NOT_PUBLISHED", "资源当前未发布")
    item.status, item.published_at = "draft", None
    audit(db, actor.id, "teaching_resource.unpublish", "teaching_resource", item.id, {})
    db.commit()
    return _resource_view(db, item)


@router.delete("/teaching-resources/{resource_id}", status_code=204, tags=["teaching-resources"])
def delete_teaching_resource(resource_id: uuid.UUID, db: Db, actor: Actor) -> None:
    item = db.scalar(
        select(TeachingResource)
        .where(
            TeachingResource.id == resource_id,
            TeachingResource.owner_id == actor.id,
        )
        .with_for_update()
    )
    if item is None:
        raise ApiProblem(404, "TEACHING_RESOURCE_NOT_FOUND", "教学资源不存在")
    if item.status == "archived":
        return
    previous_status = item.status
    item.status, item.archived_at = "archived", now_utc()
    audit(
        db,
        actor.id,
        "teaching_resource.archive",
        "teaching_resource",
        item.id,
        {"previous_status": previous_status},
    )
    db.commit()


@router.get("/student/me", tags=["student"])
def student_me(db: Db, actor: Actor) -> dict[str, Any]:
    profiles = _active_profiles(db, actor.id)
    if not profiles:
        raise ApiProblem(403, "STUDENT_ACCOUNT_NOT_LINKED", "账号尚未绑定学生档案")
    user = db.get(User, actor.id)
    return {
        "user_id": str(actor.id),
        "email": user.email if user else None,
        "recovery_email": user.email if user else None,
        "login_name": user.login_name if user else None,
        "recovery_email_verified": bool(
            user and user.email is not None and user.email_verified_at is not None
        ),
        "profiles": [
            {
                "student_id": str(student.id),
                "student_number": student.student_number,
                "name": student.name,
                "teacher_id": str(student.owner_id),
            }
            for _, student in profiles
        ],
    }


@router.post("/student/submission-files", status_code=201, tags=["student"])
async def upload_student_submission_file(
    file: Annotated[UploadFile, File()],
    assignment_id: Annotated[uuid.UUID, Form()],
    class_id: Annotated[uuid.UUID, Form()],
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    if not _active_profiles(db, actor.id):
        raise ApiProblem(403, "STUDENT_ACCOUNT_NOT_LINKED", "账号尚未绑定有效学生档案")
    _student, school_class = _student_for_class(db, actor.id, class_id)
    assignment = db.scalar(
        select(Assignment)
        .join(AssignmentClass, AssignmentClass.assignment_id == Assignment.id)
        .where(
            Assignment.id == assignment_id,
            AssignmentClass.class_id == class_id,
            Assignment.owner_id == school_class.owner_id,
            Assignment.status == AssignmentStatus.published,
        )
    )
    if assignment is None:
        raise ApiProblem(404, "PUBLISHED_ASSIGNMENT_NOT_FOUND", "已发布作业不存在或未分配到该班")
    if assignment.due_at and _utc(assignment.due_at) < now_utc():
        raise ApiProblem(409, "ASSIGNMENT_DUE", "作业已截止，不能继续上传")
    # Serialize quota checks for this account. This prevents concurrent uploads
    # from racing past the database-backed limits.
    db.scalar(select(User).where(User.id == actor.id).with_for_update())
    settings = get_settings()
    unattached_count = (
        db.scalar(
            select(func.count())
            .select_from(StoredFile)
            .where(
                StoredFile.owner_id == actor.id,
                StoredFile.status == FileStatus.ready,
                StoredFile.storage_key.startswith(f"student-submissions/{actor.id}/"),
                ~select(SubmissionPage.id)
                .where(SubmissionPage.stored_file_id == StoredFile.id)
                .exists(),
            )
        )
        or 0
    )
    if unattached_count >= settings.student_upload_max_unattached_files:
        raise ApiProblem(
            429,
            "STUDENT_UPLOAD_QUOTA_EXCEEDED",
            "待提交文件数量已达上限，请删除未使用文件或联系管理员",
        )
    hourly_count = (
        db.scalar(
            select(func.count())
            .select_from(StoredFile)
            .where(
                StoredFile.owner_id == actor.id,
                StoredFile.storage_key.startswith(f"student-submissions/{actor.id}/"),
                StoredFile.created_at >= now_utc() - timedelta(hours=1),
            )
        )
        or 0
    )
    if hourly_count >= settings.student_upload_max_files_per_hour:
        raise ApiProblem(429, "STUDENT_UPLOAD_RATE_LIMITED", "每小时上传文件数已达上限")

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise ApiProblem(413, "SUBMISSION_FILE_TOO_LARGE", "作业文件超过大小限制")
    try:
        name = safe_filename(file.filename)
        inspection = inspect_upload(
            name,
            content,
            file.content_type,
            max_pdf_pages=settings.recognition_max_pdf_pages,
            max_image_pixels=settings.recognition_max_image_pixels,
        )
    except UnsafeFile as exc:
        raise ApiProblem(
            422 if exc.code != "FILE_TYPE_INVALID" else 415,
            exc.code,
            exc.message,
        ) from exc
    key = f"student-submissions/{actor.id}/{uuid.uuid4().hex}.{inspection.kind}"
    content_type = file.content_type or "application/octet-stream"
    try:
        metadata = storage.put(key, io.BytesIO(content), len(content), content_type)
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
        db.refresh(stored)
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(key)
        except Exception:
            pass
        raise ApiProblem(503, "STUDENT_FILE_SAVE_FAILED", "作业文件保存失败") from exc
    return {
        "key": metadata.key,
        "id": str(stored.id),
        "name": name,
        "content_type": metadata.content_type,
        "size": metadata.size,
        "checksum": stored.checksum,
    }


@router.delete("/student/submission-files/{file_id}", status_code=204, tags=["student"])
def delete_student_submission_file(
    file_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> None:
    if not _active_profiles(db, actor.id):
        raise ApiProblem(403, "STUDENT_ACCOUNT_NOT_LINKED", "账号尚未绑定有效学生档案")
    stored = db.scalar(
        select(StoredFile)
        .where(
            StoredFile.id == file_id,
            StoredFile.owner_id == actor.id,
            StoredFile.storage_key.startswith(f"student-submissions/{actor.id}/"),
        )
        .with_for_update()
    )
    if stored is None:
        raise ApiProblem(404, "STUDENT_FILE_NOT_FOUND", "待提交文件不存在")
    if stored.status == FileStatus.pending:
        raise ApiProblem(409, "STUDENT_FILE_DELETE_IN_PROGRESS", "待提交文件正在删除")
    if stored.status != FileStatus.ready:
        raise ApiProblem(404, "STUDENT_FILE_NOT_FOUND", "待提交文件不存在")
    in_use = db.scalar(
        select(SubmissionPage.id).where(SubmissionPage.stored_file_id == stored.id).limit(1)
    )
    if in_use:
        raise ApiProblem(409, "STUDENT_FILE_ALREADY_SUBMITTED", "已提交文件不能删除")
    stored.status = FileStatus.pending
    db.commit()
    try:
        storage.delete(stored.storage_key)
    except Exception as exc:
        with db.begin():
            db.execute(
                update(StoredFile)
                .where(
                    StoredFile.id == file_id,
                    StoredFile.status == FileStatus.pending,
                )
                .values(status=FileStatus.ready)
                .execution_options(synchronize_session=False)
            )
        raise ApiProblem(503, "STUDENT_FILE_DELETE_FAILED", "待提交文件删除失败") from exc
    with db.begin():
        db.execute(
            update(StoredFile)
            .where(
                StoredFile.id == file_id,
                StoredFile.status == FileStatus.pending,
            )
            .values(status=FileStatus.deleted)
            .execution_options(synchronize_session=False)
        )


@router.get("/student/assignments", tags=["student"])
def student_assignments(db: Db, actor: Actor) -> dict[str, Any]:
    rows = db.execute(
        select(Assignment, SchoolClass, Student)
        .join(AssignmentClass, AssignmentClass.assignment_id == Assignment.id)
        .join(SchoolClass, SchoolClass.id == AssignmentClass.class_id)
        .join(ClassStudent, ClassStudent.class_id == SchoolClass.id)
        .join(Student, Student.id == ClassStudent.student_id)
        .join(StudentAccountLink, StudentAccountLink.student_id == Student.id)
        .where(
            StudentAccountLink.user_id == actor.id,
            StudentAccountLink.status == "active",
            Student.status == "active",
            ClassStudent.status == MembershipStatus.active,
            SchoolClass.status == "active",
            Assignment.status == AssignmentStatus.published,
            Assignment.owner_id == SchoolClass.owner_id,
            Student.owner_id == SchoolClass.owner_id,
        )
        .order_by(Assignment.due_at, Assignment.created_at.desc())
    ).all()
    items = []
    for assignment, school_class, student in rows:
        latest = db.scalar(
            select(Submission)
            .where(
                Submission.assignment_id == assignment.id,
                Submission.class_id == school_class.id,
                Submission.student_id == student.id,
                Submission.submitted_by_user_id == actor.id,
            )
            .order_by(Submission.attempt_number.desc())
        )
        items.append(
            {
                "id": str(assignment.id),
                "class_id": str(school_class.id),
                "class_name": school_class.name,
                "student_id": str(student.id),
                "title": assignment.title,
                "subject": assignment.subject,
                "description": assignment.description,
                "instructions": assignment.instructions,
                "total_score": str(assignment.total_score)
                if assignment.total_score is not None
                else None,
                "due_at": assignment.due_at,
                "published_at": assignment.published_at,
                "max_files": 20,
                "allowed_file_types": [".pdf", ".png", ".jpg", ".jpeg"],
                "submission": _submission_view(latest) if latest else None,
            }
        )
    return {"items": items, "total": len(items)}


class StudentSubmissionInput(BaseModel):
    class_id: uuid.UUID
    stored_file_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("stored_file_ids")
    @classmethod
    def unique_files(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("stored_file_ids 不能重复")
        return value


def _submission_view(
    item: Submission, *, stored_file_ids: set[uuid.UUID] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(item.id),
        "assignment_id": str(item.assignment_id),
        "class_id": str(item.class_id),
        "student_id": str(item.student_id) if item.student_id else None,
        "attempt_number": item.attempt_number,
        "status": item.status,
        "source": item.source,
        "submitted_at": item.submitted_at,
        "created_at": item.created_at,
    }
    if stored_file_ids is not None:
        payload["stored_file_ids"] = sorted(str(value) for value in stored_file_ids)
    return payload


def _submission_stored_file_ids(db: Session, submission_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(SubmissionPage.stored_file_id).where(
                SubmissionPage.submission_id == submission_id,
                SubmissionPage.stored_file_id.is_not(None),
            )
        ).all()
    )


def _idempotent_submission(
    db: Session,
    item: Submission,
    *,
    assignment_id: uuid.UUID,
    class_id: uuid.UUID,
    student_id: uuid.UUID,
    stored_file_ids: list[uuid.UUID],
) -> dict[str, Any]:
    if (
        item.assignment_id != assignment_id
        or item.class_id != class_id
        or item.student_id != student_id
    ):
        raise ApiProblem(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他作业提交")
    actual_file_ids = _submission_stored_file_ids(db, item.id)
    payload = _submission_view(item, stored_file_ids=actual_file_ids)
    payload["idempotent_replay"] = actual_file_ids != set(stored_file_ids)
    return payload


@router.post("/student/assignments/{assignment_id}/submissions", status_code=201, tags=["student"])
def submit_student_assignment(
    assignment_id: uuid.UUID,
    data: StudentSubmissionInput,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    # Serialize against account-link revocation before honoring an old
    # idempotency key. A revoked account must never be able to replay and read
    # identifiers from an earlier submission.
    db.scalar(select(User.id).where(User.id == actor.id).with_for_update())
    student, school_class = _student_for_class(db, actor.id, data.class_id)
    existing = db.scalar(
        select(Submission).where(
            Submission.submitted_by_user_id == actor.id,
            Submission.student_idempotency_key == data.idempotency_key,
        )
    )
    if existing:
        return _idempotent_submission(
            db,
            existing,
            assignment_id=assignment_id,
            class_id=data.class_id,
            student_id=student.id,
            stored_file_ids=data.stored_file_ids,
        )

    assignment = db.scalar(
        select(Assignment)
        .join(AssignmentClass, AssignmentClass.assignment_id == Assignment.id)
        .where(
            Assignment.id == assignment_id,
            AssignmentClass.class_id == data.class_id,
            Assignment.owner_id == school_class.owner_id,
            Assignment.status == AssignmentStatus.published,
        )
        .with_for_update()
    )
    if assignment is None:
        raise ApiProblem(404, "PUBLISHED_ASSIGNMENT_NOT_FOUND", "已发布作业不存在或未分配到该班")
    # The assignment row lock serializes first-batch creation and attempt numbering.
    # Recheck the idempotency key after acquiring it so concurrent retries return
    # the winning submission instead of surfacing a database uniqueness error.
    concurrent_existing = db.scalar(
        select(Submission).where(
            Submission.submitted_by_user_id == actor.id,
            Submission.student_idempotency_key == data.idempotency_key,
        )
    )
    if concurrent_existing:
        return _idempotent_submission(
            db,
            concurrent_existing,
            assignment_id=assignment_id,
            class_id=data.class_id,
            student_id=student.id,
            stored_file_ids=data.stored_file_ids,
        )
    if assignment.due_at and _utc(assignment.due_at) < now_utc():
        raise ApiProblem(409, "ASSIGNMENT_DUE", "作业已截止，不能继续提交")
    files = db.scalars(
        select(StoredFile)
        .where(StoredFile.id.in_(data.stored_file_ids))
        .order_by(StoredFile.id)
        .with_for_update()
    ).all()
    if len(files) != len(data.stored_file_ids) or any(
        file.owner_id != actor.id
        or file.status != FileStatus.ready
        or not file.storage_key.startswith(f"student-submissions/{actor.id}/")
        for file in files
    ):
        raise ApiProblem(
            422, "SUBMISSION_FILE_NOT_AVAILABLE", "文件不存在、尚未就绪或不属于当前学生账号"
        )
    already_attached = db.scalar(
        select(SubmissionPage.id)
        .where(SubmissionPage.stored_file_id.in_(data.stored_file_ids))
        .limit(1)
    )
    if already_attached:
        raise ApiProblem(409, "SUBMISSION_FILE_ALREADY_USED", "文件已经用于其他作业提交")

    batch = db.scalar(
        select(GradingBatch)
        .where(
            GradingBatch.owner_id == assignment.owner_id,
            GradingBatch.assignment_id == assignment.id,
            GradingBatch.class_id == data.class_id,
            GradingBatch.status != "archived",
        )
        .order_by(GradingBatch.created_at.desc())
    )
    if batch is None:
        batch = GradingBatch(
            owner_id=assignment.owner_id,
            assignment_id=assignment.id,
            class_id=data.class_id,
            name="学生端提交",
            status="collecting",
        )
        db.add(batch)
        db.flush()
    attempt = (
        db.scalar(
            select(func.max(Submission.attempt_number)).where(
                Submission.grading_batch_id == batch.id, Submission.student_id == student.id
            )
        )
        or 0
    ) + 1
    submission = Submission(
        owner_id=assignment.owner_id,
        grading_batch_id=batch.id,
        assignment_id=assignment.id,
        class_id=data.class_id,
        student_id=student.id,
        submitted_by_user_id=actor.id,
        student_idempotency_key=data.idempotency_key,
        attempt_number=attempt,
        status="uploaded",
        source="student_portal",
        submitted_at=now_utc(),
    )
    db.add(submission)
    db.flush()
    settings = get_settings()
    page_number = 1
    by_id = {file.id: file for file in files}
    for file_id in data.stored_file_ids:
        file = by_id[file_id]
        try:
            content = storage.get(file.storage_key).read(settings.max_upload_bytes + 1)
            if len(content) > settings.max_upload_bytes:
                raise ApiProblem(413, "SUBMISSION_FILE_TOO_LARGE", "提交文件超过大小限制")
            inspection = inspect_upload(
                file.original_name,
                content,
                file.content_type,
                max_pdf_pages=settings.recognition_max_pdf_pages,
                max_image_pixels=settings.recognition_max_image_pixels,
            )
        except UnsafeFile as exc:
            raise ApiProblem(422, exc.code, exc.message) from exc
        except ApiProblem:
            raise
        except Exception as exc:
            raise ApiProblem(503, "SUBMISSION_FILE_READ_FAILED", "提交文件暂时无法读取") from exc
        for source_page in range(1, inspection.page_count + 1):
            db.add(
                SubmissionPage(
                    submission_id=submission.id,
                    stored_file_id=file_id,
                    page_number=page_number,
                    source_page_number=source_page,
                    status="ready",
                )
            )
            page_number += 1
    batch.submission_count += 1
    audit(
        db,
        actor.id,
        "student_submission.create",
        "submission",
        submission.id,
        {"student_id": str(student.id), "file_count": len(files)},
    )
    db.commit()
    db.refresh(submission)
    return _submission_view(submission, stored_file_ids=set(data.stored_file_ids))


def _released_result_rows(db: Session, actor_id: uuid.UUID) -> list[tuple[Any, ...]]:
    student_ids = _profile_ids(db, actor_id)
    if not student_ids:
        raise ApiProblem(403, "STUDENT_ACCOUNT_NOT_LINKED", "账号尚未绑定学生档案")
    rows = db.execute(
        select(GradeReleaseItem, GradeRelease, SubmissionScoreSnapshot, Submission, Assignment)
        .join(GradeRelease, GradeRelease.id == GradeReleaseItem.grade_release_id)
        .join(
            SubmissionScoreSnapshot,
            SubmissionScoreSnapshot.id == GradeReleaseItem.score_snapshot_id,
        )
        .join(Submission, Submission.id == GradeReleaseItem.submission_id)
        .join(Assignment, Assignment.id == GradeRelease.assignment_id)
        .where(
            GradeReleaseItem.student_id.in_(student_ids),
            GradeReleaseItem.status == "included",
            GradeRelease.status == "released",
            GradeRelease.release_mode != "internal_only",
            SubmissionScoreSnapshot.status == "complete",
            Submission.student_id == GradeReleaseItem.student_id,
            Submission.status == "finalized",
            Submission.finalized_at.is_not(None),
            Submission.id == SubmissionScoreSnapshot.submission_id,
            Submission.assignment_id == GradeRelease.assignment_id,
            Submission.class_id == GradeRelease.class_id,
            SubmissionScoreSnapshot.assignment_id == GradeRelease.assignment_id,
            SubmissionScoreSnapshot.student_id == GradeReleaseItem.student_id,
            SubmissionScoreSnapshot.generated_by == GradeRelease.owner_id,
        )
        .order_by(GradeRelease.version.desc(), GradeRelease.released_at.desc())
    ).all()
    latest: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], tuple[Any, ...]] = {}
    for row in rows:
        item, release = row[0], row[1]
        latest.setdefault((release.assignment_id, release.class_id, item.student_id), tuple(row))
    return list(latest.values())


def _visible_details(snapshot: SubmissionScoreSnapshot, mode: str) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for raw in snapshot.details or []:
        item = {
            "question_id": raw.get("question_id"),
            "question_number": raw.get("question_number"),
            "question_type": raw.get("question_type"),
            "student_answer_id": raw.get("student_answer_id"),
        }
        if mode != "feedback_only":
            item["score"] = raw.get("score")
            item["max_score"] = raw.get("max_score")
        if mode != "score_only":
            item.update(
                feedback=raw.get("final_feedback", raw.get("feedback")),
                error_type=raw.get("final_error_type", raw.get("error_type")),
                knowledge_point_ids=raw.get("knowledge_point_ids", []),
            )
        details.append(item)
    return details


@router.get("/student/results", tags=["student"])
def student_results(db: Db, actor: Actor) -> dict[str, Any]:
    items = []
    for release_item, release, snapshot, submission, assignment in _released_result_rows(
        db, actor.id
    ):
        items.append(
            {
                "grade_release_id": str(release.id),
                "grade_release_version": release.version,
                "release_mode": release.release_mode,
                "released_at": release.released_at,
                "assignment_id": str(assignment.id),
                "assignment_title": assignment.title,
                "class_id": str(release.class_id),
                "student_id": str(release_item.student_id),
                "submission_id": str(submission.id),
                "score_snapshot_id": str(snapshot.id),
                "score_snapshot_version": snapshot.version,
                "total_score": str(snapshot.total_score)
                if release.release_mode != "feedback_only"
                else None,
                "max_score": (
                    str(snapshot.max_score) if release.release_mode != "feedback_only" else None
                ),
                "details": _visible_details(snapshot, release.release_mode),
            }
        )
    return {"items": items, "total": len(items)}


def _wrong_items(db: Session, actor_id: uuid.UUID) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for release_item, release, snapshot, _submission, assignment in _released_result_rows(
        db, actor_id
    ):
        for raw in snapshot.details or []:
            try:
                score, maximum = Decimal(str(raw.get("score"))), Decimal(str(raw.get("max_score")))
                answer_id = uuid.UUID(str(raw.get("student_answer_id")))
                question_id = uuid.UUID(str(raw.get("question_id")))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if score >= maximum:
                continue
            thread = db.scalar(
                select(WrongQuestionThread).where(
                    WrongQuestionThread.user_id == actor_id,
                    WrongQuestionThread.student_answer_id == answer_id,
                    WrongQuestionThread.score_snapshot_id == snapshot.id,
                )
            )
            review_request = (
                db.scalar(
                    select(StudentTeacherReviewRequest).where(
                        StudentTeacherReviewRequest.thread_id == thread.id
                    )
                )
                if thread
                else None
            )
            item = {
                "student_answer_id": str(answer_id),
                "student_id": str(release_item.student_id),
                "assignment_id": str(assignment.id),
                "assignment_title": assignment.title,
                "question_id": str(question_id),
                "question_number": raw.get("question_number"),
                "question_content": raw.get("question_text"),
                "student_answer": raw.get("student_answer_text"),
                "feedback": raw.get("final_feedback", raw.get("feedback"))
                if release.release_mode != "score_only"
                else None,
                "error_type": raw.get("final_error_type", raw.get("error_type"))
                if release.release_mode != "score_only"
                else None,
                "score_snapshot_id": str(snapshot.id),
                "grade_release_id": str(release.id),
                "thread_id": str(thread.id) if thread else None,
                "thread_status": thread.status if thread else None,
                "review_request_id": str(review_request.id) if review_request else None,
                "review_status": review_request.status if review_request else None,
                "review_decision": review_request.decision if review_request else None,
                "teacher_response": review_request.teacher_response if review_request else None,
            }
            if release.release_mode != "feedback_only":
                item["score"] = str(score)
                item["max_score"] = str(maximum)
            output.append(item)
    return output


@router.get("/student/wrong-questions", tags=["student"])
def student_wrong_questions(db: Db, actor: Actor) -> dict[str, Any]:
    items = _wrong_items(db, actor.id)
    return {"items": items, "total": len(items)}


def _teacher_wrong_items(db: Session, teacher_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            GradeReleaseItem,
            GradeRelease,
            SubmissionScoreSnapshot,
            Submission,
            Assignment,
            SchoolClass,
            Student,
        )
        .join(GradeRelease, GradeRelease.id == GradeReleaseItem.grade_release_id)
        .join(
            SubmissionScoreSnapshot,
            SubmissionScoreSnapshot.id == GradeReleaseItem.score_snapshot_id,
        )
        .join(Submission, Submission.id == GradeReleaseItem.submission_id)
        .join(Assignment, Assignment.id == GradeRelease.assignment_id)
        .join(SchoolClass, SchoolClass.id == GradeRelease.class_id)
        .join(Student, Student.id == GradeReleaseItem.student_id)
        .where(
            GradeRelease.owner_id == teacher_id,
            Assignment.owner_id == teacher_id,
            SchoolClass.owner_id == teacher_id,
            Student.owner_id == teacher_id,
            GradeRelease.status == "released",
            GradeReleaseItem.status == "included",
            SubmissionScoreSnapshot.status == "complete",
            Submission.status == "finalized",
            Submission.finalized_at.is_not(None),
            Submission.owner_id == teacher_id,
            Submission.student_id == GradeReleaseItem.student_id,
            Submission.id == SubmissionScoreSnapshot.submission_id,
            Submission.assignment_id == GradeRelease.assignment_id,
            Submission.class_id == GradeRelease.class_id,
            SubmissionScoreSnapshot.assignment_id == GradeRelease.assignment_id,
            SubmissionScoreSnapshot.student_id == GradeReleaseItem.student_id,
            SubmissionScoreSnapshot.generated_by == teacher_id,
        )
        .order_by(
            GradeRelease.version.desc(),
            GradeRelease.released_at.desc(),
            Assignment.title,
            Student.student_number,
        )
    ).all()
    latest: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], tuple[Any, ...]] = {}
    for row in rows:
        release_item, release = row[0], row[1]
        latest.setdefault(
            (release.assignment_id, release.class_id, release_item.student_id), tuple(row)
        )

    items: list[dict[str, Any]] = []
    for (
        _release_item,
        release,
        snapshot,
        submission,
        assignment,
        school_class,
        student,
    ) in latest.values():
        for raw in snapshot.details or []:
            try:
                score = Decimal(str(raw.get("score")))
                maximum = Decimal(str(raw.get("max_score")))
                answer_id = uuid.UUID(str(raw.get("student_answer_id")))
                question_id = uuid.UUID(str(raw.get("question_id")))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if score >= maximum:
                continue
            knowledge_points = raw.get("knowledge_point_ids", [])
            if not isinstance(knowledge_points, list):
                knowledge_points = []
            items.append(
                {
                    "id": f"{snapshot.id}:{answer_id}",
                    "student_answer_id": str(answer_id),
                    "student_id": str(student.id),
                    "student_name": student.name,
                    "student_number": student.student_number,
                    "class_id": str(school_class.id),
                    "class_name": school_class.name,
                    "assignment_id": str(assignment.id),
                    "assignment_title": assignment.title,
                    "submission_id": str(submission.id),
                    "grading_batch_id": str(submission.grading_batch_id),
                    "question_id": str(question_id),
                    "question_number": raw.get("question_number"),
                    "question_type": raw.get("question_type"),
                    "question_content": raw.get("question_text"),
                    "student_answer": raw.get("student_answer_text"),
                    "score": str(score),
                    "max_score": str(maximum),
                    "feedback": raw.get("final_feedback", raw.get("feedback")),
                    "error_type": raw.get("final_error_type", raw.get("error_type")),
                    "knowledge_point_ids": [str(value) for value in knowledge_points],
                    "score_snapshot_id": str(snapshot.id),
                    "score_snapshot_version": snapshot.version,
                    "grade_release_id": str(release.id),
                    "grade_release_version": release.version,
                    "release_mode": release.release_mode,
                    "released_at": release.released_at,
                    "_answer_uuid": answer_id,
                    "_snapshot_uuid": snapshot.id,
                }
            )

    if items:
        threads = db.scalars(
            select(WrongQuestionThread).where(
                WrongQuestionThread.student_answer_id.in_({item["_answer_uuid"] for item in items}),
                WrongQuestionThread.score_snapshot_id.in_(
                    {item["_snapshot_uuid"] for item in items}
                ),
            )
        ).all()
    else:
        threads = []
    thread_by_context = {
        (thread.student_answer_id, thread.score_snapshot_id): thread for thread in threads
    }
    review_by_thread = {
        review.thread_id: review
        for review in (
            db.scalars(
                select(StudentTeacherReviewRequest).where(
                    StudentTeacherReviewRequest.teacher_id == teacher_id,
                    StudentTeacherReviewRequest.thread_id.in_({thread.id for thread in threads}),
                )
            ).all()
            if threads
            else []
        )
    }
    for item in items:
        answer_uuid = item.pop("_answer_uuid")
        snapshot_uuid = item.pop("_snapshot_uuid")
        thread = thread_by_context.get((answer_uuid, snapshot_uuid))
        review = review_by_thread.get(thread.id) if thread else None
        item.update(
            thread_id=str(thread.id) if thread else None,
            thread_status=thread.status if thread else None,
            review_request_id=str(review.id) if review else None,
            review_status=review.status if review else None,
            review_decision=review.decision if review else None,
        )
    return items


@router.get("/teacher/wrong-questions", tags=["teacher-practice"])
def list_teacher_wrong_questions(
    db: Db,
    actor: Actor,
    class_id: Annotated[uuid.UUID | None, Query()] = None,
    assignment_id: Annotated[uuid.UUID | None, Query()] = None,
    review_state: Annotated[Literal["all", "not_requested", "open", "closed"], Query()] = "all",
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    all_items = _teacher_wrong_items(db, actor.id)
    classes = {
        item["class_id"]: {"id": item["class_id"], "name": item["class_name"]} for item in all_items
    }
    assignments: dict[str, dict[str, Any]] = {}
    for item in all_items:
        facet = assignments.setdefault(
            item["assignment_id"],
            {
                "id": item["assignment_id"],
                "title": item["assignment_title"],
                "class_ids": set(),
            },
        )
        facet["class_ids"].add(item["class_id"])

    normalized_search = search.strip().casefold() if search else ""
    filtered: list[dict[str, Any]] = []
    for item in all_items:
        if class_id is not None and item["class_id"] != str(class_id):
            continue
        if assignment_id is not None and item["assignment_id"] != str(assignment_id):
            continue
        review_status = item.get("review_status")
        closed = review_status in {"resolved", "rejected"}
        if review_state == "not_requested" and review_status is not None:
            continue
        if review_state == "open" and (review_status is None or closed):
            continue
        if review_state == "closed" and not closed:
            continue
        if normalized_search:
            searchable = " ".join(
                str(value or "")
                for value in (
                    item["student_name"],
                    item["student_number"],
                    item["assignment_title"],
                    item["question_number"],
                    item["question_content"],
                    item["error_type"],
                    " ".join(item["knowledge_point_ids"]),
                )
            ).casefold()
            if normalized_search not in searchable:
                continue
        filtered.append(item)

    total = len(filtered)
    start = (page - 1) * page_size
    visible = filtered[start : start + page_size]
    return {
        "items": visible,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "summary": {
            "total_wrong_questions": total,
            "affected_students": len({item["student_id"] for item in filtered}),
            "knowledge_point_count": len(
                {point for item in filtered for point in item["knowledge_point_ids"] if point}
            ),
            "pending_review_count": sum(
                item.get("review_status") not in {None, "resolved", "rejected"} for item in filtered
            ),
        },
        "facets": {
            "classes": sorted(classes.values(), key=lambda item: (item["name"], item["id"])),
            "assignments": sorted(
                (
                    {
                        **item,
                        "class_ids": sorted(item["class_ids"]),
                    }
                    for item in assignments.values()
                ),
                key=lambda item: (item["title"], item["id"]),
            ),
        },
    }


def _accessible_wrong_item(
    db: Session, actor_id: uuid.UUID, answer_id: uuid.UUID
) -> dict[str, Any]:
    item = next(
        (row for row in _wrong_items(db, actor_id) if row["student_answer_id"] == str(answer_id)),
        None,
    )
    if item is None:
        raise ApiProblem(404, "RELEASED_WRONG_QUESTION_NOT_FOUND", "已发布错题不存在")
    return item


def _owned_thread(db: Session, user_id: uuid.UUID, thread_id: uuid.UUID) -> WrongQuestionThread:
    thread = db.scalar(
        select(WrongQuestionThread)
        .join(
            StudentAccountLink,
            (StudentAccountLink.user_id == WrongQuestionThread.user_id)
            & (StudentAccountLink.student_id == WrongQuestionThread.student_id),
        )
        .join(Student, Student.id == WrongQuestionThread.student_id)
        .where(
            WrongQuestionThread.id == thread_id,
            WrongQuestionThread.user_id == user_id,
            StudentAccountLink.status == "active",
            Student.status == "active",
        )
    )
    if thread is None:
        raise ApiProblem(404, "WRONG_QUESTION_THREAD_NOT_FOUND", "错题对话不存在")
    current_item = next(
        (
            item
            for item in _wrong_items(db, user_id)
            if item["student_answer_id"] == str(thread.student_answer_id)
            and item["score_snapshot_id"] == str(thread.score_snapshot_id)
        ),
        None,
    )
    if current_item is None:
        raise ApiProblem(409, "WRONG_QUESTION_THREAD_STALE", "该错题已有更新的发布版本")
    return thread


@router.post("/student/wrong-questions/{answer_id}/threads", status_code=201, tags=["student"])
def create_wrong_question_thread(answer_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = _accessible_wrong_item(db, actor.id, answer_id)
    # Serialize thread creation for this account so double-clicks and retries
    # converge on the unique thread instead of surfacing a database conflict.
    db.scalar(select(User.id).where(User.id == actor.id).with_for_update())
    item = _accessible_wrong_item(db, actor.id, answer_id)
    existing = db.scalar(
        select(WrongQuestionThread).where(
            WrongQuestionThread.user_id == actor.id,
            WrongQuestionThread.student_answer_id == answer_id,
            WrongQuestionThread.score_snapshot_id == uuid.UUID(item["score_snapshot_id"]),
        )
    )
    if existing:
        return _thread_view(existing)
    thread = WrongQuestionThread(
        user_id=actor.id,
        student_id=uuid.UUID(item["student_id"]),
        student_answer_id=answer_id,
        score_snapshot_id=uuid.UUID(item["score_snapshot_id"]),
    )
    db.add(thread)
    db.flush()
    audit(db, actor.id, "wrong_question_thread.create", "wrong_question_thread", thread.id, {})
    db.commit()
    db.refresh(thread)
    return _thread_view(thread)


def _thread_view(thread: WrongQuestionThread) -> dict[str, Any]:
    return {
        "id": str(thread.id),
        "student_id": str(thread.student_id),
        "student_answer_id": str(thread.student_answer_id),
        "score_snapshot_id": str(thread.score_snapshot_id),
        "status": thread.status,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
    }


def _message_view(message: WrongQuestionMessage) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "structured_payload": message.structured_payload or {},
        "created_at": message.created_at,
    }


@router.get("/student/wrong-question-threads/{thread_id}/messages", tags=["student"])
def list_wrong_question_messages(thread_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    _owned_thread(db, actor.id, thread_id)
    messages = db.scalars(
        select(WrongQuestionMessage)
        .where(WrongQuestionMessage.thread_id == thread_id)
        .order_by(WrongQuestionMessage.created_at, WrongQuestionMessage.id)
    ).all()
    return {"items": [_message_view(item) for item in messages], "total": len(messages)}


class WrongQuestionMessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content 不能为空")
        return value


@router.post(
    "/student/wrong-question-threads/{thread_id}/messages", status_code=202, tags=["student"]
)
def create_wrong_question_message(
    thread_id: uuid.UUID, data: WrongQuestionMessageInput, db: Db, actor: Actor
) -> dict[str, Any]:
    thread = _owned_thread(db, actor.id, thread_id)
    db.scalar(select(User.id).where(User.id == actor.id).with_for_update())
    thread = _owned_thread(db, actor.id, thread_id)
    thread = (
        db.scalar(
            select(WrongQuestionThread).where(WrongQuestionThread.id == thread.id).with_for_update()
        )
        or thread
    )
    if thread.status != "open":
        raise ApiProblem(409, "WRONG_QUESTION_THREAD_CLOSED", "错题对话当前不能继续提问")
    hourly_count = (
        db.scalar(
            select(func.count())
            .select_from(WrongQuestionAIJob)
            .join(WrongQuestionThread, WrongQuestionThread.id == WrongQuestionAIJob.thread_id)
            .where(
                WrongQuestionThread.user_id == actor.id,
                WrongQuestionAIJob.created_at >= now_utc() - timedelta(hours=1),
            )
        )
        or 0
    )
    if hourly_count >= get_settings().ai_tutor_max_questions_per_hour:
        raise ApiProblem(429, "AI_TUTOR_RATE_LIMITED", "AI 追问次数已达每小时上限")
    running = db.scalar(
        select(WrongQuestionAIJob.id).where(
            WrongQuestionAIJob.thread_id == thread.id,
            WrongQuestionAIJob.status.in_(["queued", "running"]),
        )
    )
    if running:
        raise ApiProblem(409, "WRONG_QUESTION_AI_JOB_RUNNING", "上一条 AI 回复仍在生成")
    generation = (
        db.scalar(
            select(func.max(WrongQuestionAIJob.generation)).where(
                WrongQuestionAIJob.thread_id == thread.id
            )
        )
        or 0
    ) + 1
    message = WrongQuestionMessage(thread_id=thread.id, role="student", content=data.content)
    db.add(message)
    db.flush()
    input_hash = hashlib.sha256(
        json.dumps(
            {
                "thread_id": str(thread.id),
                "score_snapshot_id": str(thread.score_snapshot_id),
                "generation": generation,
                "content": data.content,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    job = WrongQuestionAIJob(
        thread_id=thread.id,
        user_message_id=message.id,
        generation=generation,
        input_hash=input_hash,
    )
    db.add(job)
    db.flush()
    audit(
        db,
        actor.id,
        "wrong_question.message",
        "wrong_question_thread",
        thread.id,
        {"job_id": str(job.id)},
    )
    db.commit()

    if get_settings().app_env.lower() != "test":
        try:
            from workers.celery_app import celery_app

            celery_app.send_task("ahamark.wrong_question_ai.run", args=[str(job.id)])
        except Exception:
            job.status = "failed"
            job.error_code = "AI_WORKER_UNAVAILABLE"
            job.error_message = "AI task broker was unavailable during dispatch."
            job.retryable = True
            job.completed_at = now_utc()
            db.commit()
    return {
        "message": _message_view(message),
        "job": {"id": str(job.id), "status": job.status, "generation": job.generation},
    }


@router.get("/student/ai-jobs/{job_id}", tags=["student"])
def get_student_ai_job(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    job = db.scalar(
        select(WrongQuestionAIJob)
        .join(WrongQuestionThread, WrongQuestionThread.id == WrongQuestionAIJob.thread_id)
        .where(WrongQuestionAIJob.id == job_id, WrongQuestionThread.user_id == actor.id)
    )
    if job is None:
        raise ApiProblem(404, "STUDENT_AI_JOB_NOT_FOUND", "AI 任务不存在")
    _owned_thread(db, actor.id, job.thread_id)
    reply = db.scalar(
        select(WrongQuestionMessage)
        .where(
            WrongQuestionMessage.thread_id == job.thread_id,
            WrongQuestionMessage.role == "assistant",
            WrongQuestionMessage.provider_request_id == job.provider_request_id,
        )
        .order_by(WrongQuestionMessage.created_at.desc())
    )
    return {
        "id": str(job.id),
        "thread_id": str(job.thread_id),
        "generation": job.generation,
        "status": job.status,
        "retryable": job.retryable,
        "error_code": job.error_code,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "reply": _message_view(reply) if reply else None,
    }


class TeacherReviewRequestInput(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


@router.post(
    "/student/wrong-question-threads/{thread_id}/teacher-review", status_code=201, tags=["student"]
)
def submit_teacher_review_request(
    thread_id: uuid.UUID, data: TeacherReviewRequestInput, db: Db, actor: Actor
) -> dict[str, Any]:
    db.scalar(select(User.id).where(User.id == actor.id).with_for_update())
    thread = _owned_thread(db, actor.id, thread_id)
    thread = (
        db.scalar(
            select(WrongQuestionThread).where(WrongQuestionThread.id == thread.id).with_for_update()
        )
        or thread
    )
    existing = db.scalar(
        select(StudentTeacherReviewRequest).where(
            StudentTeacherReviewRequest.thread_id == thread.id
        )
    )
    if existing:
        if existing.status in {"pending", "in_review", "waiting_student"}:
            return _review_request_view(db, existing, student_view=True)
        raise ApiProblem(409, "TEACHER_REVIEW_ALREADY_RESOLVED", "该对话的人工复核已经处理")
    student = db.get(Student, thread.student_id)
    if student is None:
        raise ApiProblem(409, "STUDENT_PROFILE_MISSING", "学生档案不存在")
    messages = db.scalars(
        select(WrongQuestionMessage)
        .where(WrongQuestionMessage.thread_id == thread.id)
        .order_by(WrongQuestionMessage.created_at.desc())
        .limit(12)
    ).all()
    summary = "\n".join(
        f"{message.role}: {message.content[:500]}" for message in reversed(messages)
    )
    request = StudentTeacherReviewRequest(
        thread_id=thread.id,
        requester_user_id=actor.id,
        student_id=thread.student_id,
        teacher_id=student.owner_id,
        student_answer_id=thread.student_answer_id,
        score_snapshot_id=thread.score_snapshot_id,
        student_question=data.question.strip(),
        conversation_summary=summary or None,
    )
    db.add(request)
    jobs = db.scalars(
        select(WrongQuestionAIJob)
        .where(
            WrongQuestionAIJob.thread_id == thread.id,
            WrongQuestionAIJob.status.in_(["queued", "running"]),
        )
        .with_for_update()
    ).all()
    for job in jobs:
        job.status = "cancelled"
        job.error_code = "TEACHER_REVIEW_REQUESTED"
        job.completed_at = now_utc()
    thread.status = "teacher_review"
    db.flush()
    audit(db, actor.id, "teacher_review_request.create", "teacher_review_request", request.id, {})
    db.commit()
    db.refresh(request)
    return _review_request_view(db, request, student_view=True)


class TeacherReviewAdditionalInformationInput(BaseModel):
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content 不能为空")
        return value


@router.post(
    "/student/teacher-review-requests/{request_id}/additional-information",
    tags=["student"],
)
def add_teacher_review_information(
    request_id: uuid.UUID,
    data: TeacherReviewAdditionalInformationInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    db.scalar(select(User.id).where(User.id == actor.id).with_for_update())
    review_request = db.scalar(
        select(StudentTeacherReviewRequest)
        .where(
            StudentTeacherReviewRequest.id == request_id,
            StudentTeacherReviewRequest.requester_user_id == actor.id,
        )
        .with_for_update()
    )
    if review_request is None:
        raise ApiProblem(404, "TEACHER_REVIEW_REQUEST_NOT_FOUND", "人工复核请求不存在")
    if review_request.status != "waiting_student":
        raise ApiProblem(409, "TEACHER_REVIEW_NOT_WAITING_STUDENT", "当前无需补充复核信息")
    thread = _owned_thread(db, actor.id, review_request.thread_id)
    message = WrongQuestionMessage(
        thread_id=thread.id,
        role="student",
        content=data.content,
        structured_payload={"kind": "teacher_review_additional_information"},
    )
    db.add(message)
    db.flush()
    messages = db.scalars(
        select(WrongQuestionMessage)
        .where(WrongQuestionMessage.thread_id == thread.id)
        .order_by(WrongQuestionMessage.created_at.desc(), WrongQuestionMessage.id.desc())
        .limit(12)
    ).all()
    review_request.student_question = (
        f"{review_request.student_question}\n\n学生补充：{data.content}"
    )[-12000:]
    review_request.conversation_summary = "\n".join(
        f"{item.role}: {item.content[:500]}" for item in reversed(messages)
    )
    review_request.status = "pending"
    review_request.decision = None
    review_request.resolved_at = None
    thread.status = "teacher_review"
    audit(
        db,
        actor.id,
        "teacher_review_request.add_information",
        "teacher_review_request",
        review_request.id,
        {},
    )
    db.commit()
    db.refresh(review_request)
    return _review_request_view(db, review_request, student_view=True)


def _review_request_view(
    db: Session,
    item: StudentTeacherReviewRequest,
    *,
    student_view: bool = False,
) -> dict[str, Any]:
    student = db.get(Student, item.student_id)
    snapshot = db.get(SubmissionScoreSnapshot, item.score_snapshot_id)
    submission = db.get(Submission, snapshot.submission_id) if snapshot else None
    assignment = db.get(Assignment, snapshot.assignment_id) if snapshot else None
    detail = next(
        (
            raw
            for raw in (snapshot.details if snapshot else []) or []
            if str(raw.get("student_answer_id")) == str(item.student_answer_id)
        ),
        {},
    )
    release_mode: str | None = None
    if student_view:
        release_mode = db.scalar(
            select(GradeRelease.release_mode)
            .join(GradeReleaseItem, GradeReleaseItem.grade_release_id == GradeRelease.id)
            .where(
                GradeReleaseItem.score_snapshot_id == item.score_snapshot_id,
                GradeReleaseItem.student_id == item.student_id,
                GradeReleaseItem.status == "included",
                GradeRelease.status == "released",
                GradeRelease.release_mode != "internal_only",
            )
            .order_by(GradeRelease.version.desc())
            .limit(1)
        )
        if release_mode is None:
            raise ApiProblem(
                404,
                "PUBLISHED_REVIEW_CONTEXT_NOT_FOUND",
                "复核请求对应的发布内容当前不可见",
            )
    payload: dict[str, Any] = {
        "id": str(item.id),
        "thread_id": str(item.thread_id),
        "student_id": str(item.student_id),
        "student_name": student.name if student else None,
        "student_answer_id": str(item.student_answer_id),
        "student_answer": detail.get("student_answer_text"),
        "score_snapshot_id": str(item.score_snapshot_id),
        "score_snapshot_version": snapshot.version if snapshot else None,
        "submission_id": str(submission.id) if submission else None,
        "grading_batch_id": str(submission.grading_batch_id) if submission else None,
        "assignment_id": str(assignment.id) if assignment else None,
        "assignment_title": assignment.title if assignment else None,
        "question_id": detail.get("question_id"),
        "question_number": detail.get("question_number"),
        "question": detail.get("question_text"),
        "status": item.status,
        "student_question": item.student_question,
        "conversation_summary": item.conversation_summary,
        "decision": item.decision,
        "teacher_response": item.teacher_response,
        "score_revision_id": str(item.score_revision_id) if item.score_revision_id else None,
        "created_at": item.created_at,
        "submitted_at": item.submitted_at,
        "resolved_at": item.resolved_at,
    }
    if not student_view or release_mode != "feedback_only":
        payload["published_score"] = detail.get("score")
        payload["published_max_score"] = detail.get("max_score")
    if not student_view or release_mode != "score_only":
        payload["published_feedback"] = detail.get("final_feedback", detail.get("feedback"))
        payload["published_error_type"] = detail.get("final_error_type", detail.get("error_type"))
    return payload


@router.get("/student/teacher-review-requests", tags=["student"])
def list_student_teacher_review_requests(db: Db, actor: Actor) -> dict[str, Any]:
    student_ids = _profile_ids(db, actor.id)
    if not student_ids:
        raise ApiProblem(403, "STUDENT_ACCOUNT_NOT_LINKED", "账号尚未绑定学生档案")
    items = db.scalars(
        select(StudentTeacherReviewRequest)
        .where(
            StudentTeacherReviewRequest.requester_user_id == actor.id,
            StudentTeacherReviewRequest.student_id.in_(student_ids),
        )
        .order_by(StudentTeacherReviewRequest.submitted_at.desc())
    ).all()
    return {
        "items": [_review_request_view(db, item, student_view=True) for item in items],
        "total": len(items),
    }


@router.get("/teacher/review-requests", tags=["teacher-review-requests"])
def list_teacher_review_requests(
    db: Db,
    actor: Actor,
    status: Literal["pending", "in_review", "waiting_student", "resolved", "rejected"]
    | None = None,
) -> dict[str, Any]:
    query = select(StudentTeacherReviewRequest).where(
        StudentTeacherReviewRequest.teacher_id == actor.id
    )
    if status:
        query = query.where(StudentTeacherReviewRequest.status == status)
    items = db.scalars(query.order_by(StudentTeacherReviewRequest.submitted_at.desc())).all()
    return {
        "items": [_review_request_view(db, item) for item in items],
        "total": len(items),
    }


class TeacherReviewDecisionInput(BaseModel):
    action: Literal["uphold", "change_score", "needs_information", "reject"]
    teacher_response: str = Field(min_length=1, max_length=4000)
    final_score: Decimal | None = Field(None, ge=0)
    final_feedback: str | None = Field(None, max_length=4000)
    final_error_type: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def score_required(self) -> TeacherReviewDecisionInput:
        if self.action == "change_score" and self.final_score is None:
            raise ValueError("change_score 必须提供 final_score")
        if self.action != "change_score" and self.final_score is not None:
            raise ValueError("只有 change_score 可以提供 final_score")
        return self


@router.patch("/teacher/review-requests/{request_id}", tags=["teacher-review-requests"])
def decide_teacher_review_request(
    request_id: uuid.UUID, data: TeacherReviewDecisionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    request = db.scalar(
        select(StudentTeacherReviewRequest)
        .where(
            StudentTeacherReviewRequest.id == request_id,
            StudentTeacherReviewRequest.teacher_id == actor.id,
        )
        .with_for_update()
    )
    if request is None:
        raise ApiProblem(404, "TEACHER_REVIEW_REQUEST_NOT_FOUND", "人工复核请求不存在")
    if request.status in {"resolved", "rejected"}:
        raise ApiProblem(409, "TEACHER_REVIEW_REQUEST_RESOLVED", "人工复核请求已经处理")

    if data.action == "needs_information":
        request.status, request.decision = "waiting_student", data.action
    elif data.action == "change_score":
        new_score = data.final_score
        if new_score is None:  # Kept explicit for type safety beyond request validation.
            raise ApiProblem(422, "FINAL_SCORE_REQUIRED", "改分必须提供最终分数")
        snapshot = db.get(SubmissionScoreSnapshot, request.score_snapshot_id)
        submission = db.get(Submission, snapshot.submission_id) if snapshot else None
        snapshot_detail = next(
            (
                raw
                for raw in (snapshot.details if snapshot else []) or []
                if str(raw.get("student_answer_id")) == str(request.student_answer_id)
            ),
            None,
        )
        try:
            expected_review_id = uuid.UUID(str((snapshot_detail or {}).get("teacher_review_id")))
            expected_score = Decimal(str((snapshot_detail or {}).get("score")))
        except (InvalidOperation, TypeError, ValueError):
            raise ApiProblem(
                409,
                "TEACHER_REVIEW_REQUEST_STALE",
                "复核请求对应的已发布成绩版本已失效，请基于最新版本重新复核",
            ) from None
        if (
            snapshot is None
            or submission is None
            or submission.owner_id != actor.id
            or snapshot.student_id != request.student_id
        ):
            raise ApiProblem(409, "TEACHER_REVIEW_REQUEST_STALE", "复核请求成绩上下文已失效")
        latest_released_snapshot_id = db.scalar(
            select(GradeReleaseItem.score_snapshot_id)
            .join(GradeRelease, GradeRelease.id == GradeReleaseItem.grade_release_id)
            .where(
                GradeRelease.owner_id == actor.id,
                GradeRelease.assignment_id == snapshot.assignment_id,
                GradeRelease.class_id == submission.class_id,
                GradeRelease.status == "released",
                GradeRelease.release_mode != "internal_only",
                GradeReleaseItem.student_id == request.student_id,
                GradeReleaseItem.status == "included",
            )
            .order_by(GradeRelease.version.desc(), GradeRelease.released_at.desc())
            .limit(1)
        )
        if latest_released_snapshot_id != request.score_snapshot_id:
            raise ApiProblem(
                409,
                "TEACHER_REVIEW_REQUEST_STALE",
                "该学生已有更新的已发布成绩版本，请基于最新版本重新复核",
            )
        answer = db.scalar(
            select(StudentAnswer)
            .join(Submission, Submission.id == StudentAnswer.submission_id)
            .where(
                StudentAnswer.id == request.student_answer_id,
                Submission.owner_id == actor.id,
            )
            .with_for_update()
        )
        if answer is None:
            raise ApiProblem(409, "TEACHER_REVIEW_REQUEST_STALE", "学生答案已不存在")
        review = db.scalar(
            select(TeacherReview)
            .where(
                TeacherReview.id == expected_review_id,
                TeacherReview.student_answer_id == request.student_answer_id,
            )
            .with_for_update()
        )
        if review is None or review.confirmed_at is None or review.final_score is None:
            raise ApiProblem(409, "CONFIRMED_TEACHER_REVIEW_MISSING", "原始教师确认结果不存在")
        expected_feedback = (snapshot_detail or {}).get(
            "final_feedback", (snapshot_detail or {}).get("feedback")
        )
        expected_error_type = (snapshot_detail or {}).get(
            "final_error_type", (snapshot_detail or {}).get("error_type")
        )
        if (
            Decimal(review.final_score) != expected_score
            or review.final_feedback != expected_feedback
            or review.final_error_type != expected_error_type
        ):
            raise ApiProblem(
                409,
                "TEACHER_REVIEW_REQUEST_STALE",
                "原评分已在其他页面修改，请刷新并基于最新发布版本处理",
            )
        if Decimal(review.final_score) == new_score:
            raise ApiProblem(422, "SCORE_UNCHANGED", "新分数必须与当前分数不同")
        try:
            published_max_score = Decimal(str((snapshot_detail or {}).get("max_score")))
        except (InvalidOperation, TypeError, ValueError):
            raise ApiProblem(
                409,
                "TEACHER_REVIEW_REQUEST_STALE",
                "复核请求缺少发布时的题目分值，请重新发布成绩后再处理",
            ) from None
        current_answer_text = (
            answer.corrected_text if answer.corrected_text is not None else answer.recognized_text
        )
        if current_answer_text != (snapshot_detail or {}).get("student_answer_text"):
            raise ApiProblem(
                409,
                "TEACHER_REVIEW_REQUEST_STALE",
                "学生答案已发生变化，请重新定稿并发布后再处理",
            )
        if new_score > published_max_score:
            raise ApiProblem(422, "SCORE_OUT_OF_RANGE", "最终分数超出题目分值范围")
        revision = ScoreRevision(
            teacher_review_id=review.id,
            student_answer_id=request.student_answer_id,
            actor_id=actor.id,
            previous_score=review.final_score,
            new_score=new_score,
            previous_feedback=review.final_feedback,
            new_feedback=data.final_feedback,
            reason=f"学生错题申疑：{data.teacher_response}",
        )
        db.add(revision)
        db.flush()
        review.decision = "modified"
        review.final_score = new_score
        review.final_feedback = data.final_feedback
        review.final_error_type = data.final_error_type
        review.review_notes = data.teacher_response
        review.confirmed_at = now_utc()
        request.score_revision_id = revision.id
        request.status, request.decision = "resolved", data.action
    else:
        request.status = "rejected" if data.action == "reject" else "resolved"
        request.decision = data.action
    request.teacher_response = data.teacher_response.strip()
    request.reviewed_by = actor.id
    request.resolved_at = None if request.status == "waiting_student" else now_utc()
    thread = db.get(WrongQuestionThread, request.thread_id)
    if thread:
        if request.status in {"resolved", "rejected"}:
            thread.status = "closed"
        elif request.status == "waiting_student":
            thread.status = "waiting_student"
    audit(
        db,
        actor.id,
        "teacher_review_request.decide",
        "teacher_review_request",
        request.id,
        {"action": data.action, "requires_republish": data.action == "change_score"},
    )
    db.commit()
    db.refresh(request)
    return {
        **_review_request_view(db, request),
        "requires_score_snapshot_and_release": data.action == "change_score",
    }


def _learning_analysis_view(item: StudentLearningAnalysis) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "student_id": str(item.student_id),
        "status": item.status,
        "source_grade_release_ids": item.source_grade_release_ids or [],
        "content": item.content or {},
        "evidence": item.evidence or [],
        "error_code": item.error_code,
        "generated_at": item.generated_at,
        "created_at": item.created_at,
    }


def _learning_resource_versions(
    db: Session,
    *,
    student_id: uuid.UUID,
    class_ids: set[uuid.UUID],
) -> list[dict[str, object]]:
    if not class_ids:
        return []
    resources = db.scalars(
        select(TeachingResource)
        .join(SchoolClass, SchoolClass.id == TeachingResource.class_id)
        .join(ClassStudent, ClassStudent.class_id == SchoolClass.id)
        .join(Student, Student.id == ClassStudent.student_id)
        .where(
            TeachingResource.class_id.in_(class_ids),
            TeachingResource.status == "published",
            TeachingResource.owner_id == SchoolClass.owner_id,
            ClassStudent.student_id == student_id,
            ClassStudent.status == MembershipStatus.active,
            Student.status == "active",
            SchoolClass.status == "active",
            Student.owner_id == SchoolClass.owner_id,
        )
        .order_by(TeachingResource.id)
    ).all()
    return [
        {
            "resource_id": str(item.id),
            "updated_at": item.updated_at.isoformat(),
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "title": item.title,
            "resource_type": item.resource_type,
        }
        for item in resources
    ]


@router.post("/student/learning-analyses", status_code=202, tags=["student"])
def request_student_learning_analysis(db: Db, actor: Actor) -> dict[str, Any]:
    settings = get_settings()
    db.scalar(select(User.id).where(User.id == actor.id).with_for_update())
    rows = _released_result_rows(db, actor.id)[
        : max(1, settings.student_learning_max_grade_releases)
    ]
    if not rows:
        raise ApiProblem(422, "LEARNING_ANALYSIS_SOURCE_EMPTY", "暂无可用于学习分析的已发布成绩")
    student_ids = {row[0].student_id for row in rows}
    if len(student_ids) != 1:
        raise ApiProblem(409, "STUDENT_IDENTITY_AMBIGUOUS", "学生账号绑定状态异常")
    student_id = next(iter(student_ids))
    sources = sorted(
        [
            {
                "grade_release_id": str(release.id),
                "score_snapshot_id": str(snapshot.id),
                "score_snapshot_version": snapshot.version,
            }
            for _item, release, snapshot, _submission, _assignment in rows
        ],
        key=lambda item: item["grade_release_id"],
    )
    resource_versions = _learning_resource_versions(
        db,
        student_id=student_id,
        class_ids={release.class_id for _item, release, *_rest in rows},
    )
    source_hash = student_learning_source_hash(
        student_id=str(student_id),
        released_snapshots=sources,
        resource_versions=resource_versions,
    )
    existing = db.scalar(
        select(StudentLearningAnalysis).where(
            StudentLearningAnalysis.student_id == student_id,
            StudentLearningAnalysis.source_hash == source_hash,
        )
    )
    if existing:
        analysis = existing
        if analysis.status != "failed":
            return _learning_analysis_view(analysis)
        if _utc(analysis.updated_at) > now_utc() - timedelta(
            seconds=settings.student_learning_retry_cooldown_seconds
        ):
            raise ApiProblem(
                429,
                "LEARNING_ANALYSIS_RETRY_COOLDOWN",
                "学习分析刚刚失败，请稍后再试",
            )
        analysis.status = "queued"
        analysis.error_code = None
        analysis.content = {}
        analysis.evidence = []
        analysis.generated_at = None
        analysis.provider_request_id = None
        analysis.request_hash = None
        analysis.response_hash = None
        analysis.input_tokens = None
        analysis.output_tokens = None
        analysis.attempts = 0
        audit(
            db,
            actor.id,
            "student_learning_analysis.retry",
            "student_learning_analysis",
            analysis.id,
            {"source_count": len(sources)},
        )
    else:
        daily_count = (
            db.scalar(
                select(func.count())
                .select_from(StudentLearningAnalysis)
                .where(
                    StudentLearningAnalysis.user_id == actor.id,
                    StudentLearningAnalysis.created_at >= now_utc() - timedelta(days=1),
                )
            )
            or 0
        )
        if daily_count >= settings.student_learning_max_requests_per_day:
            raise ApiProblem(
                429,
                "LEARNING_ANALYSIS_DAILY_LIMIT",
                "今日学习分析请求次数已达上限",
            )
        analysis = StudentLearningAnalysis(
            user_id=actor.id,
            student_id=student_id,
            source_hash=source_hash,
            source_grade_release_ids=[item["grade_release_id"] for item in sources],
        )
        db.add(analysis)
        db.flush()
        audit(
            db,
            actor.id,
            "student_learning_analysis.create",
            "student_learning_analysis",
            analysis.id,
            {"source_count": len(sources)},
        )
    db.commit()
    if settings.app_env.lower() != "test":
        try:
            from workers.celery_app import celery_app

            celery_app.send_task("ahamark.student_learning_analysis.run", args=[str(analysis.id)])
        except Exception:
            analysis.status = "failed"
            analysis.error_code = "AI_WORKER_UNAVAILABLE"
            db.commit()
    return _learning_analysis_view(analysis)


@router.get("/student/learning-analyses", tags=["student"])
def list_student_learning_analyses(
    db: Db,
    actor: Actor,
    status: Literal["queued", "running", "complete", "failed"] | None = Query(None),
) -> dict[str, Any]:
    student_ids = _profile_ids(db, actor.id)
    if not student_ids:
        raise ApiProblem(403, "STUDENT_ACCOUNT_NOT_LINKED", "账号尚未绑定学生档案")
    query = select(StudentLearningAnalysis).where(
        StudentLearningAnalysis.user_id == actor.id,
        StudentLearningAnalysis.student_id.in_(student_ids),
    )
    if status:
        query = query.where(StudentLearningAnalysis.status == status)
    items = db.scalars(
        query.order_by(
            StudentLearningAnalysis.generated_at.desc(), StudentLearningAnalysis.created_at.desc()
        )
    ).all()
    payload = [_learning_analysis_view(item) for item in items]
    return {"items": payload, "total": len(payload)}


@router.get("/student/resources", tags=["student"])
def list_student_resources(db: Db, actor: Actor) -> dict[str, Any]:
    student_ids = _profile_ids(db, actor.id)
    if not student_ids:
        raise ApiProblem(403, "STUDENT_ACCOUNT_NOT_LINKED", "账号尚未绑定学生档案")
    items = db.scalars(
        select(TeachingResource)
        .join(ClassStudent, ClassStudent.class_id == TeachingResource.class_id)
        .join(SchoolClass, SchoolClass.id == TeachingResource.class_id)
        .join(Student, Student.id == ClassStudent.student_id)
        .where(
            ClassStudent.student_id.in_(student_ids),
            ClassStudent.status == MembershipStatus.active,
            SchoolClass.status == "active",
            TeachingResource.status == "published",
            TeachingResource.owner_id == SchoolClass.owner_id,
            Student.owner_id == SchoolClass.owner_id,
        )
        .distinct()
        .order_by(TeachingResource.sort_order, TeachingResource.published_at.desc())
    ).all()
    return {"items": [_resource_view(db, item) for item in items], "total": len(items)}


def _accessible_resource(
    db: Session, actor_id: uuid.UUID, resource_id: uuid.UUID
) -> TeachingResource:
    student_ids = _profile_ids(db, actor_id)
    item = db.scalar(
        select(TeachingResource)
        .join(ClassStudent, ClassStudent.class_id == TeachingResource.class_id)
        .join(SchoolClass, SchoolClass.id == TeachingResource.class_id)
        .join(Student, Student.id == ClassStudent.student_id)
        .where(
            TeachingResource.id == resource_id,
            TeachingResource.status == "published",
            ClassStudent.student_id.in_(student_ids),
            ClassStudent.status == MembershipStatus.active,
            SchoolClass.status == "active",
            TeachingResource.owner_id == SchoolClass.owner_id,
            Student.owner_id == SchoolClass.owner_id,
        )
    )
    if item is None:
        raise ApiProblem(404, "TEACHING_RESOURCE_NOT_FOUND", "教学资源不存在")
    return item


@router.post("/student/resources/{resource_id}/signed-url", tags=["student"])
def student_resource_signed_url(
    resource_id: uuid.UUID, db: Db, actor: Actor, storage: Storage
) -> dict[str, str]:
    item = _accessible_resource(db, actor.id, resource_id)
    if item.stored_file_id is None:
        raise ApiProblem(422, "RESOURCE_IS_EXTERNAL_URL", "该资源是外部链接，无需生成下载地址")
    file = db.get(StoredFile, item.stored_file_id)
    if file is None or file.status != FileStatus.ready:
        raise ApiProblem(409, "RESOURCE_FILE_NOT_AVAILABLE", "资源文件当前不可用")
    return {
        "url": storage.presigned_get(file.storage_key, get_settings().signed_url_expiry_seconds)
    }
