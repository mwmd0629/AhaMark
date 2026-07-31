import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class Status(enum.StrEnum):
    active = "active"
    inactive = "inactive"


class FileStatus(enum.StrEnum):
    pending = "pending"
    ready = "ready"
    deleted = "deleted"
    failed = "failed"


class ArchiveStatus(enum.StrEnum):
    active = "active"
    archived = "archived"


class MembershipStatus(enum.StrEnum):
    active = "active"
    removed = "removed"


class ImportStatus(enum.StrEnum):
    preview_ready = "preview_ready"
    validation_failed = "validation_failed"
    confirmed = "confirmed"
    failed = "failed"
    expired = "expired"


class ImportRowStatus(enum.StrEnum):
    valid = "valid"
    invalid = "invalid"
    duplicate_in_file = "duplicate_in_file"
    duplicate_existing = "duplicate_existing"
    confirmed = "confirmed"
    skipped = "skipped"


class AssignmentStatus(enum.StrEnum):
    draft = "draft"
    published = "published"
    grading = "grading"
    completed = "completed"
    archived = "archived"


class VersionStatus(enum.StrEnum):
    draft = "draft"
    processing = "processing"
    ready = "ready"
    confirmed = "confirmed"
    superseded = "superseded"
    failed = "failed"


class QuestionStatus(enum.StrEnum):
    active = "active"
    removed = "removed"


class RecognitionStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partially_completed = "partially_completed"
    failed = "failed"
    cancelled = "cancelled"


class PageRecognitionStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    stale = "stale"


class CandidateStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    edited = "edited"
    rejected = "rejected"
    superseded = "superseded"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active, index=True)
    roles: Mapped[list["Role"]] = relationship(secondary="user_roles", back_populates="users")


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    users: Mapped[list[User]] = relationship(secondary="user_roles", back_populates="roles")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )


class StoredFile(Base):
    __tablename__ = "stored_files"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    size: Mapped[int] = mapped_column(BigInteger)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus), default=FileStatus.pending, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_resource", "resource_type", "resource_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, index=True
    )


class SchoolClass(TimestampMixin, Base):
    __tablename__ = "classes"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_class_owner_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    grade: Mapped[str | None] = mapped_column(String(40), index=True)
    subject: Mapped[str | None] = mapped_column(String(40), index=True)
    academic_year: Mapped[str | None] = mapped_column(String(20))
    semester: Mapped[str | None] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ArchiveStatus] = mapped_column(
        Enum(ArchiveStatus), default=ArchiveStatus.active, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Student(TimestampMixin, Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("owner_id", "student_number", name="uq_student_owner_number"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    student_number: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    gender: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[ArchiveStatus] = mapped_column(
        Enum(ArchiveStatus), default=ArchiveStatus.active, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassStudent(Base):
    __tablename__ = "class_students"
    __table_args__ = (UniqueConstraint("class_id", "student_id", name="uq_class_student"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus), default=MembershipStatus.active, index=True
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentGroup(TimestampMixin, Base):
    __tablename__ = "student_groups"
    __table_args__ = (UniqueConstraint("class_id", "name", name="uq_group_class_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(255))


class StudentGroupMember(Base):
    __tablename__ = "student_group_members"
    __table_args__ = (UniqueConstraint("group_id", "student_id", name="uq_group_student"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_groups.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ImportJob(Base):
    __tablename__ = "import_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))
    status: Mapped[ImportStatus] = mapped_column(Enum(ImportStatus), index=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_rows: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (UniqueConstraint("import_job_id", "row_number", name="uq_import_row"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    normalized_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[ImportRowStatus] = mapped_column(Enum(ImportRowStatus), index=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ImportError(Base):
    __tablename__ = "import_errors"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), index=True)
    subject: Mapped[str | None] = mapped_column(String(40), index=True)
    grade: Mapped[str | None] = mapped_column(String(40), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus), default=AssignmentStatus.draft, index=True
    )
    total_score: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    copied_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assignments.id", ondelete="SET NULL")
    )
    active_paper_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    active_rubric_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class AssignmentClass(Base):
    __tablename__ = "assignment_classes"
    __table_args__ = (UniqueConstraint("assignment_id", "class_id", name="uq_assignment_class"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class PaperVersion(Base):
    __tablename__ = "paper_versions"
    __table_args__ = (UniqueConstraint("assignment_id", "version", name="uq_paper_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[VersionStatus] = mapped_column(
        Enum(VersionStatus), default=VersionStatus.draft, index=True
    )
    source_type: Mapped[str] = mapped_column(String(20), default="manual")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class PaperPage(TimestampMixin, Base):
    __tablename__ = "paper_pages"
    __table_args__ = (
        UniqueConstraint("paper_version_id", "page_number", name="uq_paper_page_number"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="CASCADE"), index=True
    )
    stored_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    source_page_number: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="ready", index=True)
    preview_storage_key: Mapped[str | None] = mapped_column(String(512))
    thumbnail_storage_key: Mapped[str | None] = mapped_column(String(512))


class AssignmentGenerationJob(TimestampMixin, Base):
    __tablename__ = "assignment_generation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_assignment_generation_idempotency"
        ),
        UniqueConstraint("assignment_id", "generation", name="uq_assignment_generation_number"),
        CheckConstraint("generation > 0", name="ck_assignment_generation_positive"),
        CheckConstraint(
            "progress >= 0 AND progress <= 100", name="ck_assignment_generation_progress"
        ),
        Index(
            "ix_assignment_generation_owner_assignment_status",
            "owner_id",
            "assignment_id",
            "status",
        ),
        Index(
            "uq_assignment_generation_active",
            "assignment_id",
            unique=True,
            sqlite_where=text(
                "status IN ('queued','analyzing','processing_pages','extracting_questions',"
                "'generating_rubrics','validating')"
            ),
            postgresql_where=text(
                "status IN ('queued','analyzing','processing_pages','extracting_questions',"
                "'generating_rubrics','validating')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    provider_mode: Mapped[str] = mapped_column(String(32), default="unavailable")
    provider_config_version: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    celery_task_id: Mapped[str | None] = mapped_column(String(80), index=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssignmentDraftRevision(TimestampMixin, Base):
    __tablename__ = "assignment_draft_revisions"
    __table_args__ = (
        UniqueConstraint("assignment_id", "revision", name="uq_assignment_draft_revision"),
        CheckConstraint("revision > 0", name="ck_assignment_draft_revision_positive"),
        CheckConstraint(
            "teacher_edit_version >= 0", name="ck_assignment_draft_teacher_edit_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), unique=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="SET NULL")
    )
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    draft_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    created_by_type: Mapped[str] = mapped_column(String(32), default="worker")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationStageResult(TimestampMixin, Base):
    __tablename__ = "generation_stage_results"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", "stage_generation", name="uq_generation_stage_run"),
        CheckConstraint("stage_generation > 0", name="ck_generation_stage_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32))
    stage_generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    expected_teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_invocation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationIssue(TimestampMixin, Base):
    __tablename__ = "generation_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    draft_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE")
    )
    stage: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolution_status: Mapped[str] = mapped_column(String(24), default="open")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class AssignmentGenerationProviderInvocation(Base):
    __tablename__ = "assignment_generation_provider_invocations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    stage_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_stage_results.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    model_snapshot: Mapped[str | None] = mapped_column(String(160))
    endpoint_mode: Mapped[str] = mapped_column(String(80))
    provider_config_version: Mapped[str] = mapped_column(String(80), default="legacy-unknown")
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    stage_generation: Mapped[int] = mapped_column(Integer, default=1)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Any | None] = mapped_column(Numeric(12, 6))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    image_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class AssignmentFieldSuggestion(TimestampMixin, Base):
    __tablename__ = "assignment_field_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "draft_revision_id",
            "field_name",
            "suggestion_version",
            name="uq_assignment_field_suggestion_version",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_field_suggestion_confidence"
        ),
        CheckConstraint("suggestion_version > 0", name="ck_field_suggestion_version_positive"),
        CheckConstraint("teacher_edit_version >= 0", name="ck_field_suggestion_teacher_version"),
        Index(
            "ix_field_suggestion_revision_field_status",
            "draft_revision_id",
            "field_name",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"), index=True
    )
    field_name: Mapped[str] = mapped_column(String(40))
    suggested_value: Mapped[Any | None] = mapped_column(JSON)
    normalized_value: Mapped[Any | None] = mapped_column(JSON)
    confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_type: Mapped[str] = mapped_column(String(40))
    source_stage: Mapped[str] = mapped_column(String(32), default="analyzing")
    suggestion_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="suggested", index=True)
    teacher_value: Mapped[Any | None] = mapped_column(JSON)
    teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


class AssignmentSourceFileAnalysis(TimestampMixin, Base):
    __tablename__ = "assignment_source_file_analyses"
    __table_args__ = (
        CheckConstraint(
            "role_confidence >= 0 AND role_confidence <= 1",
            name="ck_source_file_role_confidence",
        ),
        CheckConstraint(
            "answer_source_confidence >= 0 AND answer_source_confidence <= 1",
            name="ck_source_file_answer_confidence",
        ),
        CheckConstraint("teacher_edit_version >= 0", name="ck_source_file_teacher_version"),
        Index(
            "ix_source_file_analysis_revision_file_status",
            "draft_revision_id",
            "stored_file_id",
            "analysis_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"), index=True
    )
    stored_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT"), index=True
    )
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    detected_mime_type: Mapped[str] = mapped_column(String(127))
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    suggested_role: Mapped[str] = mapped_column(String(32), default="unknown")
    role_confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    suggested_answer_source: Mapped[str] = mapped_column(String(32), default="unknown")
    answer_source_confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    duplicate_of_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL")
    )
    analysis_status: Mapped[str] = mapped_column(String(24), default="suggested", index=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    teacher_confirmed_role: Mapped[str | None] = mapped_column(String(32))
    teacher_confirmed_answer_source: Mapped[str | None] = mapped_column(String(32))
    teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


class AssignmentPageAnalysis(TimestampMixin, Base):
    __tablename__ = "assignment_page_analyses"
    __table_args__ = (
        UniqueConstraint(
            "source_file_analysis_id", "paper_page_id", name="uq_source_file_page_analysis"
        ),
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
            name="ck_page_analysis_quality",
        ),
        CheckConstraint(
            "blank_probability IS NULL OR (blank_probability >= 0 AND blank_probability <= 1)",
            name="ck_page_analysis_blank_probability",
        ),
        CheckConstraint(
            "duplicate_probability IS NULL OR "
            "(duplicate_probability >= 0 AND duplicate_probability <= 1)",
            name="ck_page_analysis_duplicate_probability",
        ),
        CheckConstraint("teacher_edit_version >= 0", name="ck_page_analysis_teacher_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"), index=True
    )
    paper_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="CASCADE"), index=True
    )
    source_file_analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_source_file_analyses.id", ondelete="CASCADE"), index=True
    )
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    quality_score: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    blank_probability: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    duplicate_probability: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    duplicate_of_page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="SET NULL")
    )
    missing_page_suspected: Mapped[bool] = mapped_column(Boolean, default=False)
    low_quality: Mapped[bool] = mapped_column(Boolean, default=False)
    corrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    mixed_document_suspected: Mapped[bool] = mapped_column(Boolean, default=False)
    variant_label: Mapped[str | None] = mapped_column(String(32))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperPageOrganizationSuggestion(TimestampMixin, Base):
    __tablename__ = "paper_page_organization_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "draft_revision_id",
            "paper_page_id",
            "suggestion_version",
            name="uq_page_org_suggestion_version",
        ),
        CheckConstraint("suggestion_version > 0", name="ck_page_org_version_positive"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_page_org_confidence"),
        CheckConstraint("teacher_edit_version >= 0", name="ck_page_org_teacher_version"),
        CheckConstraint("suggested_page_number > 0", name="ck_page_org_page_positive"),
        CheckConstraint("suggested_rotation IN (0, 90, 180, 270)", name="ck_page_org_rotation"),
        Index("ix_page_org_owner", "owner_id"),
        Index("ix_page_org_assignment", "assignment_id"),
        Index("ix_page_org_job", "generation_job_id"),
        Index("ix_page_org_revision", "draft_revision_id"),
        Index("ix_page_org_paper_version", "paper_version_id"),
        Index("ix_page_org_page", "paper_page_id"),
        Index("ix_page_org_status", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE")
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE")
    )
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE")
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="CASCADE")
    )
    paper_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="CASCADE")
    )
    suggestion_version: Mapped[int] = mapped_column(Integer)
    suggested_page_number: Mapped[int] = mapped_column(Integer)
    suggested_rotation: Mapped[int] = mapped_column(Integer)
    suggested_status: Mapped[str] = mapped_column(String(30))
    duplicate_of_page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="SET NULL")
    )
    variant_label: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="suggested")
    teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


class AssignmentQuestionExtractionCandidate(TimestampMixin, Base):
    __tablename__ = "assignment_question_extraction_candidates"
    __table_args__ = (
        UniqueConstraint(
            "draft_revision_id",
            "candidate_version",
            "source_question_candidate_id",
            name="uq_question_extraction_revision_version_source",
        ),
        CheckConstraint("candidate_version > 0", name="ck_question_extraction_version_positive"),
        CheckConstraint(
            "overall_confidence >= 0 AND overall_confidence <= 1",
            name="ck_question_extraction_confidence",
        ),
        CheckConstraint(
            "max_score IS NULL OR max_score > 0", name="ck_question_extraction_score_positive"
        ),
        CheckConstraint("teacher_edit_version >= 0", name="ck_question_extraction_teacher_version"),
        CheckConstraint(
            "parent_candidate_id IS NULL OR parent_candidate_id <> id",
            name="ck_question_extraction_not_self_parent",
        ),
        Index("ix_aqec_owner", "owner_id"),
        Index("ix_aqec_assignment", "assignment_id"),
        Index("ix_aqec_job", "generation_job_id"),
        Index("ix_aqec_revision", "draft_revision_id"),
        Index("ix_aqec_paper_version", "paper_version_id"),
        Index("ix_aqec_recognition", "source_recognition_job_id"),
        Index("ix_aqec_parent", "parent_candidate_id"),
        Index("ix_aqec_status", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE")
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE")
    )
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE")
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="CASCADE")
    )
    source_recognition_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recognition_jobs.id", ondelete="SET NULL")
    )
    source_question_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("question_candidates.id", ondelete="SET NULL")
    )
    candidate_version: Mapped[int] = mapped_column(Integer)
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assignment_question_extraction_candidates.id", ondelete="SET NULL")
    )
    question_number: Mapped[str | None] = mapped_column(String(80))
    question_type: Mapped[str] = mapped_column(String(30), default="other")
    content_text: Mapped[str | None] = mapped_column(Text)
    content_latex: Mapped[str | None] = mapped_column(Text)
    max_score: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    difficulty: Mapped[str | None] = mapped_column(String(20))
    knowledge_point_suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)
    field_confidences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    overall_confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    extraction_method: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="suggested")
    manual_required: Mapped[bool] = mapped_column(Boolean, default=False)
    teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    teacher_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    materialized_question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), unique=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


class AssignmentQuestionExtractionRegion(Base):
    __tablename__ = "assignment_question_extraction_regions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "display_order", name="uq_question_extraction_region_order"
        ),
        CheckConstraint("display_order >= 0", name="ck_question_extraction_region_order"),
        CheckConstraint(
            "x >= 0 AND y >= 0 AND width > 0 AND height > 0",
            name="ck_question_extraction_region_positive",
        ),
        CheckConstraint(
            "x + width <= 1 AND y + height <= 1", name="ck_question_extraction_region_bounds"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_question_extraction_region_confidence"
        ),
        Index("ix_aqer_candidate", "candidate_id"),
        Index("ix_aqer_page", "paper_page_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_question_extraction_candidates.id", ondelete="CASCADE")
    )
    paper_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="CASCADE")
    )
    display_order: Mapped[int] = mapped_column(Integer)
    region_type: Mapped[str] = mapped_column(String(30))
    x: Mapped[Any] = mapped_column(Numeric(8, 6))
    y: Mapped[Any] = mapped_column(Numeric(8, 6))
    width: Mapped[Any] = mapped_column(Numeric(8, 6))
    height: Mapped[Any] = mapped_column(Numeric(8, 6))
    confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_block_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    cross_page_group: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AssignmentAnswerDraftCandidate(TimestampMixin, Base):
    __tablename__ = "assignment_answer_draft_candidates"
    __table_args__ = (
        UniqueConstraint(
            "draft_revision_id",
            "question_id",
            "candidate_version",
            name="uq_answer_candidate_revision_question_version",
        ),
        CheckConstraint("candidate_version > 0", name="ck_answer_candidate_version_positive"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_answer_candidate_confidence"
        ),
        CheckConstraint("teacher_edit_version >= 0", name="ck_answer_candidate_teacher_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    question_version: Mapped[str] = mapped_column(String(160))
    candidate_version: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(32))
    source_file_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assignment_source_file_analyses.id", ondelete="SET NULL")
    )
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="SET NULL")
    )
    source_region: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_content: Mapped[str | None] = mapped_column(Text)
    normalized_content: Mapped[str | None] = mapped_column(Text)
    structured_content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    alternative_answers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="suggested", index=True)
    manual_required: Mapped[bool] = mapped_column(Boolean, default=False)
    teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    teacher_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    materialized_reference_answer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reference_answer_versions.id", ondelete="SET NULL"), unique=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


class AssignmentRubricDraftCandidate(TimestampMixin, Base):
    __tablename__ = "assignment_rubric_draft_candidates"
    __table_args__ = (
        UniqueConstraint(
            "draft_revision_id",
            "question_id",
            "candidate_version",
            name="uq_rubric_candidate_revision_question_version",
        ),
        CheckConstraint("candidate_version > 0", name="ck_rubric_candidate_version_positive"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_rubric_candidate_confidence"
        ),
        CheckConstraint(
            "total_points IS NULL OR total_points > 0", name="ck_rubric_candidate_points_positive"
        ),
        CheckConstraint("teacher_edit_version >= 0", name="ck_rubric_candidate_teacher_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"), index=True
    )
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    question_version: Mapped[str] = mapped_column(String(160))
    answer_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_answer_draft_candidates.id", ondelete="RESTRICT")
    )
    candidate_version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    scoring_mode: Mapped[str] = mapped_column(String(24))
    total_points: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    allow_partial_credit: Mapped[bool] = mapped_column(Boolean, default=True)
    domain_requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    common_error_types: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    feedback_templates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="suggested", index=True)
    manual_required: Mapped[bool] = mapped_column(Boolean, default=False)
    teacher_edit_version: Mapped[int] = mapped_column(Integer, default=0)
    teacher_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    materialized_structured_rubric_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("structured_rubric_versions.id", ondelete="SET NULL"), unique=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


class AssignmentRubricCriterionDraft(TimestampMixin, Base):
    __tablename__ = "assignment_rubric_criterion_drafts"
    __table_args__ = (
        UniqueConstraint(
            "rubric_candidate_id", "criterion_key", name="uq_rubric_draft_criterion_key"
        ),
        UniqueConstraint(
            "rubric_candidate_id", "display_order", name="uq_rubric_draft_criterion_order"
        ),
        CheckConstraint("display_order >= 0", name="ck_rubric_draft_order_nonnegative"),
        CheckConstraint("points IS NULL OR points >= 0", name="ck_rubric_draft_points_nonnegative"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_rubric_draft_confidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rubric_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_rubric_draft_candidates.id", ondelete="CASCADE"), index=True
    )
    criterion_key: Mapped[str] = mapped_column(String(80))
    display_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    points: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    criterion_type: Mapped[str] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    dependency_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    alternative_group: Mapped[str | None] = mapped_column(String(80))
    partial_credit_rule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deduction_rule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_rule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    common_error_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    feedback_template: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    manual_required: Mapped[bool] = mapped_column(Boolean, default=False)


class AssignmentRubricValidationResult(Base):
    __tablename__ = "assignment_rubric_validation_results"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rubric_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_rubric_draft_candidates.id", ondelete="CASCADE"), index=True
    )
    answer_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_answer_draft_candidates.id", ondelete="CASCADE")
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    validation_mode: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(30))
    deterministic_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    structural_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    issue_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str] = mapped_column(String(64))
    validator_version: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Question(TimestampMixin, Base):
    __tablename__ = "questions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="CASCADE"), index=True
    )
    parent_question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), index=True
    )
    question_number: Mapped[str] = mapped_column(String(40))
    display_order: Mapped[int] = mapped_column(Integer, index=True)
    question_type: Mapped[str] = mapped_column(String(30))
    content_text: Mapped[str | None] = mapped_column(Text)
    content_latex: Mapped[str | None] = mapped_column(Text)
    # Draft/OCR-created questions may not have a trustworthy score yet.
    # Publishing and rubric confirmation enforce completeness instead of a sentinel value.
    max_score: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[QuestionStatus] = mapped_column(
        Enum(QuestionStatus), default=QuestionStatus.active, index=True
    )
    source: Mapped[str] = mapped_column(String(20), default="manual")


class QuestionRegion(TimestampMixin, Base):
    __tablename__ = "question_regions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    paper_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="CASCADE"), index=True
    )
    region_type: Mapped[str] = mapped_column(String(30), default="question")
    x: Mapped[Any] = mapped_column(Numeric(8, 6))
    y: Mapped[Any] = mapped_column(Numeric(8, 6))
    width: Mapped[Any] = mapped_column(Numeric(8, 6))
    height: Mapped[Any] = mapped_column(Numeric(8, 6))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    __table_args__ = (UniqueConstraint("assignment_id", "version", name="uq_rubric_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[VersionStatus] = mapped_column(
        Enum(VersionStatus), default=VersionStatus.draft, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class QuestionRubric(TimestampMixin, Base):
    __tablename__ = "question_rubrics"
    __table_args__ = (
        UniqueConstraint("rubric_version_id", "question_id", name="uq_question_rubric"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    standard_answer: Mapped[str | None] = mapped_column(Text)
    alternative_answers: Mapped[list[str]] = mapped_column(JSON, default=list)
    scoring_notes: Mapped[str | None] = mapped_column(Text)
    allow_step_score: Mapped[bool] = mapped_column(default=True)
    unit_requirement: Mapped[str | None] = mapped_column(Text)
    format_requirement: Mapped[str | None] = mapped_column(Text)
    precision_requirement: Mapped[str | None] = mapped_column(Text)


class RubricItem(TimestampMixin, Base):
    __tablename__ = "rubric_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_rubric_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_rubrics.id", ondelete="CASCADE"), index=True
    )
    display_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    points: Mapped[Any] = mapped_column(Numeric(10, 2))
    item_type: Mapped[str] = mapped_column(String(30), default="step")
    required: Mapped[bool] = mapped_column(default=False)
    deduction_rule: Mapped[str | None] = mapped_column(Text)


class KnowledgePoint(TimestampMixin, Base):
    __tablename__ = "knowledge_points"
    __table_args__ = (
        UniqueConstraint("owner_id", "subject", "grade", "name", name="uq_knowledge_point"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    subject: Mapped[str | None] = mapped_column(String(40))
    grade: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="SET NULL")
    )


class QuestionKnowledgePoint(Base):
    __tablename__ = "question_knowledge_points"
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True
    )


class RecognitionJob(TimestampMixin, Base):
    __tablename__ = "recognition_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_recognition_job_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="CASCADE"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[RecognitionStatus] = mapped_column(
        Enum(RecognitionStatus), default=RecognitionStatus.queued, index=True
    )
    stage: Mapped[str] = mapped_column(String(40), default="converting", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(80))
    config_version: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80), index=True)
    candidate_result: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    error_message: Mapped[str | None] = mapped_column(Text)


class PageProcessingResult(TimestampMixin, Base):
    __tablename__ = "page_processing_results"
    __table_args__ = (
        UniqueConstraint("recognition_job_id", "paper_page_id", name="uq_job_page_result"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recognition_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recognition_jobs.id", ondelete="CASCADE"), index=True
    )
    paper_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[PageRecognitionStatus] = mapped_column(Enum(PageRecognitionStatus), index=True)
    stage: Mapped[str] = mapped_column(String(40), default="converting")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    original_storage_key: Mapped[str | None] = mapped_column(String(512))
    rendered_storage_key: Mapped[str | None] = mapped_column(String(512))
    processed_storage_key: Mapped[str | None] = mapped_column(String(512))
    thumbnail_storage_key: Mapped[str | None] = mapped_column(String(512))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    detected_rotation: Mapped[int] = mapped_column(Integer, default=0)
    applied_rotation: Mapped[int] = mapped_column(Integer, default=0)
    crop_region: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quality_score: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    blur_score: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    shadow_score: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    processing_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)


class RecognitionBlock(TimestampMixin, Base):
    __tablename__ = "recognition_blocks"
    __table_args__ = (Index("ix_recognition_block_page_order", "paper_page_id", "display_order"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recognition_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recognition_jobs.id", ondelete="CASCADE"), index=True
    )
    paper_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="CASCADE"), index=True
    )
    block_type: Mapped[str] = mapped_column(String(30), index=True)
    display_order: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    latex: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    language: Mapped[str | None] = mapped_column(String(30))
    x: Mapped[Any] = mapped_column(Numeric(8, 6))
    y: Mapped[Any] = mapped_column(Numeric(8, 6))
    width: Mapped[Any] = mapped_column(Numeric(8, 6))
    height: Mapped[Any] = mapped_column(Numeric(8, 6))
    source: Mapped[str] = mapped_column(String(80))
    crop_storage_key: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(30), default="recognized", index=True)


class QuestionCandidate(TimestampMixin, Base):
    __tablename__ = "question_candidates"
    __table_args__ = (
        UniqueConstraint("recognition_job_id", "temporary_number", name="uq_job_candidate_number"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recognition_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recognition_jobs.id", ondelete="CASCADE"), index=True
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="CASCADE"), index=True
    )
    temporary_number: Mapped[str] = mapped_column(String(80))
    question_type: Mapped[str] = mapped_column(String(30), default="other")
    content_text: Mapped[str | None] = mapped_column(Text)
    content_latex: Mapped[str | None] = mapped_column(Text)
    suggested_score: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus), default=CandidateStatus.pending, index=True
    )
    source: Mapped[str] = mapped_column(String(80))
    confirmed_question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL")
    )


class QuestionCandidateRegion(Base):
    __tablename__ = "question_candidate_regions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_candidates.id", ondelete="CASCADE"), index=True
    )
    paper_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="CASCADE"), index=True
    )
    x: Mapped[Any] = mapped_column(Numeric(8, 6))
    y: Mapped[Any] = mapped_column(Numeric(8, 6))
    width: Mapped[Any] = mapped_column(Numeric(8, 6))
    height: Mapped[Any] = mapped_column(Numeric(8, 6))
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RecognitionCorrection(Base):
    __tablename__ = "recognition_corrections"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recognition_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recognition_jobs.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    field: Mapped[str] = mapped_column(String(50))
    original_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


# Submission grading is deliberately separate from paper recognition. String states are
# used here so deployments can add workflow states without rewriting PostgreSQL enums.
class GradingBatch(TimestampMixin, Base):
    __tablename__ = "grading_batches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    submission_count: Mapped[int] = mapped_column(Integer, default=0)
    recognized_count: Mapped[int] = mapped_column(Integer, default=0)
    graded_count: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Submission(TimestampMixin, Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint(
            "grading_batch_id", "student_id", "attempt_number", name="uq_submission_attempt"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    grading_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grading_batches.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    student_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="uploaded", index=True)
    source: Mapped[str] = mapped_column(String(30), default="teacher_upload")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recognized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubmissionPage(TimestampMixin, Base):
    __tablename__ = "submission_pages"
    __table_args__ = (UniqueConstraint("submission_id", "page_number", name="uq_submission_page"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), index=True
    )
    stored_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    source_page_number: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="ready", index=True)
    rendered_storage_key: Mapped[str | None] = mapped_column(String(512))
    processed_storage_key: Mapped[str | None] = mapped_column(String(512))
    thumbnail_storage_key: Mapped[str | None] = mapped_column(String(512))
    processing_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    blur_score: Mapped[Any | None] = mapped_column(Numeric(10, 5))
    brightness: Mapped[Any | None] = mapped_column(Numeric(10, 5))
    contrast: Mapped[Any | None] = mapped_column(Numeric(10, 5))
    blank_probability: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    duplicate_of_page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("submission_pages.id", ondelete="SET NULL")
    )
    orientation_confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    preprocessing_version: Mapped[str | None] = mapped_column(String(80))
    quality_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    processing_error_code: Mapped[str | None] = mapped_column(String(80), index=True)
    processing_error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(default=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    aligned_paper_page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_pages.id", ondelete="SET NULL"), index=True
    )
    alignment_transform: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    alignment_confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    alignment_failure_reason: Mapped[str | None] = mapped_column(String(160))
    page_version: Mapped[int] = mapped_column(Integer, default=1)


class SubmissionFileMatch(Base):
    __tablename__ = "submission_file_matches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grading_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grading_batches.id", ondelete="RESTRICT"), index=True
    )
    stored_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT"), unique=True
    )
    suggested_student_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL")
    )
    confirmed_student_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL")
    )
    match_method: Mapped[str] = mapped_column(String(30), default="unmatched")
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentAnswer(TimestampMixin, Base):
    __tablename__ = "student_answers"
    __table_args__ = (
        UniqueConstraint("submission_id", "question_id", name="uq_submission_question_answer"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    question_version_reference: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    recognized_text: Mapped[str | None] = mapped_column(Text)
    recognized_latex: Mapped[str | None] = mapped_column(Text)
    corrected_text: Mapped[str | None] = mapped_column(Text)
    corrected_latex: Mapped[str | None] = mapped_column(Text)
    recognition_confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    recognition_provider: Mapped[str | None] = mapped_column(String(80))
    recognition_provider_version: Mapped[str | None] = mapped_column(String(80))
    is_blank: Mapped[bool] = mapped_column(default=False)
    requires_review: Mapped[bool] = mapped_column(default=True, index=True)


class StudentAnswerRegion(TimestampMixin, Base):
    __tablename__ = "student_answer_regions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answers.id", ondelete="CASCADE"), index=True
    )
    submission_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_pages.id", ondelete="RESTRICT"), index=True
    )
    region_type: Mapped[str] = mapped_column(String(30), default="answer")
    x: Mapped[Any] = mapped_column(Numeric(8, 6))
    y: Mapped[Any] = mapped_column(Numeric(8, 6))
    width: Mapped[Any] = mapped_column(Numeric(8, 6))
    height: Mapped[Any] = mapped_column(Numeric(8, 6))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_origin: Mapped[str | None] = mapped_column(String(24))
    reason: Mapped[str | None] = mapped_column(String(255))
    segmentation_version: Mapped[str] = mapped_column(String(80), default="submission-seg-v1")
    region_version: Mapped[int] = mapped_column(Integer, default=1)


class SubmissionProcessingJob(TimestampMixin, Base):
    __tablename__ = "submission_processing_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_submission_processing_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(40), default="page_processing", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(80), default="local")
    provider_version: Mapped[str] = mapped_column(String(80), default="pillow")
    config_version: Mapped[str] = mapped_column(String(80), default="submission-processing-v1")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)


class SubmissionQuestionAnchor(TimestampMixin, Base):
    __tablename__ = "submission_question_anchors"
    __table_args__ = (
        UniqueConstraint(
            "submission_processing_job_id",
            "submission_page_id",
            "block_index",
            name="uq_submission_anchor_block",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_processing_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_processing_jobs.id", ondelete="CASCADE"), index=True
    )
    submission_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_pages.id", ondelete="CASCADE"), index=True
    )
    block_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(120))
    normalized_number: Mapped[str | None] = mapped_column(String(80), index=True)
    candidate_question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), index=True
    )
    confidence: Mapped[Any] = mapped_column(Numeric(6, 5))
    x: Mapped[Any] = mapped_column(Numeric(8, 6))
    y: Mapped[Any] = mapped_column(Numeric(8, 6))
    width: Mapped[Any] = mapped_column(Numeric(8, 6))
    height: Mapped[Any] = mapped_column(Numeric(8, 6))
    rejection_reason: Mapped[str | None] = mapped_column(String(160))


class SubmissionRecognitionJob(TimestampMixin, Base):
    __tablename__ = "submission_recognition_jobs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_submission_recognition_idempotency"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    provider: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    provider_kind: Mapped[str] = mapped_column(String(40), default="printed_text")
    config_version: Mapped[str] = mapped_column(String(80), default="answer-evidence-v1")
    input_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubmissionRecognitionBlock(Base):
    __tablename__ = "submission_recognition_blocks"
    __table_args__ = (
        UniqueConstraint("submission_page_id", "block_index", name="uq_submission_page_block"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_recognition_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_recognition_jobs.id", ondelete="CASCADE"), index=True
    )
    submission_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_pages.id", ondelete="CASCADE"), index=True
    )
    block_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    latex: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    status: Mapped[str] = mapped_column(String(30), index=True)
    x: Mapped[Any] = mapped_column(Numeric(8, 6))
    y: Mapped[Any] = mapped_column(Numeric(8, 6))
    width: Mapped[Any] = mapped_column(Numeric(8, 6))
    height: Mapped[Any] = mapped_column(Numeric(8, 6))
    provider: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    student_answer_region_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("student_answer_regions.id", ondelete="CASCADE"), index=True
    )
    region_evidence_image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("region_evidence_images.id", ondelete="RESTRICT"), index=True
    )
    source_page_number: Mapped[int | None] = mapped_column(Integer)
    block_type: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    reading_order: Mapped[int] = mapped_column(Integer, default=0)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    requires_review: Mapped[bool] = mapped_column(default=True, index=True)
    evidence_image_key: Mapped[str | None] = mapped_column(String(512))
    recognition_version: Mapped[int] = mapped_column(Integer, default=1)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class RegionEvidenceImage(TimestampMixin, Base):
    __tablename__ = "region_evidence_images"
    __table_args__ = (
        UniqueConstraint(
            "student_answer_region_id",
            "source_kind",
            "page_version",
            "region_version",
            "processing_config_version",
            name="uq_region_evidence_source_version",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    submission_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_pages.id", ondelete="CASCADE"), index=True
    )
    student_answer_region_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answer_regions.id", ondelete="CASCADE"), index=True
    )
    source_kind: Mapped[str] = mapped_column(String(20))
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    margin_pixels: Mapped[int] = mapped_column(Integer)
    source_page_number: Mapped[int] = mapped_column(Integer)
    region_order: Mapped[int] = mapped_column(Integer)
    page_version: Mapped[int] = mapped_column(Integer)
    region_version: Mapped[int] = mapped_column(Integer)
    processing_config_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="ready", index=True)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class RecognitionRevision(Base):
    __tablename__ = "recognition_revisions"
    __table_args__ = (
        UniqueConstraint("recognition_block_id", "revision", name="uq_recognition_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recognition_block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_recognition_blocks.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    latex: Mapped[str | None] = mapped_column(Text)
    warning_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    editor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    base_recognition_version: Mapped[int] = mapped_column(Integer)
    confirmed: Mapped[bool] = mapped_column(default=False)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class QuestionRecognitionEvidence(TimestampMixin, Base):
    __tablename__ = "question_recognition_evidence"
    __table_args__ = (
        UniqueConstraint(
            "student_answer_id", "recognition_version", name="uq_question_evidence_version"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answers.id", ondelete="CASCADE"), index=True
    )
    recognition_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_recognition_jobs.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="requires_review", index=True)
    block_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    latex: Mapped[str | None] = mapped_column(Text)
    provider_versions: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    recognition_version: Mapped[int] = mapped_column(Integer)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer)
    requires_review: Mapped[bool] = mapped_column(default=True, index=True)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_origin: Mapped[str | None] = mapped_column(String(24))


class GradingJob(TimestampMixin, Base):
    __tablename__ = "grading_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_grading_idempotency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    grading_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grading_batches.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), index=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    provider: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(80))
    config_version: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)


class GradingResult(TimestampMixin, Base):
    __tablename__ = "grading_results"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grading_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grading_jobs.id", ondelete="RESTRICT"), index=True
    )
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answers.id", ondelete="RESTRICT"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="RESTRICT"), index=True
    )
    grading_method: Mapped[str] = mapped_column(String(30))
    provider: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(80))
    score: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    max_score: Mapped[Any] = mapped_column(Numeric(10, 2))
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    recognized_answer_snapshot: Mapped[str | None] = mapped_column(Text)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(80))
    student_feedback: Mapped[str | None] = mapped_column(Text)
    requires_review: Mapped[bool] = mapped_column(default=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="suggested", index=True)


class GradingCriterionResult(Base):
    __tablename__ = "grading_criterion_results"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grading_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grading_results.id", ondelete="CASCADE"), index=True
    )
    rubric_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_items.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(30))
    awarded_points: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    max_points: Mapped[Any] = mapped_column(Numeric(10, 2))
    reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class GradingEvidence(Base):
    __tablename__ = "grading_evidence"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grading_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grading_results.id", ondelete="CASCADE"), index=True
    )
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answers.id", ondelete="RESTRICT")
    )
    submission_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_pages.id", ondelete="RESTRICT")
    )
    evidence_type: Mapped[str] = mapped_column(String(30))
    quote: Mapped[str | None] = mapped_column(String(500))
    x: Mapped[Any | None] = mapped_column(Numeric(8, 6))
    y: Mapped[Any | None] = mapped_column(Numeric(8, 6))
    width: Mapped[Any | None] = mapped_column(Numeric(8, 6))
    height: Mapped[Any | None] = mapped_column(Numeric(8, 6))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TeacherReview(TimestampMixin, Base):
    __tablename__ = "teacher_reviews"
    __table_args__ = (UniqueConstraint("student_answer_id", name="uq_answer_review"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grading_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("grading_results.id", ondelete="RESTRICT")
    )
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answers.id", ondelete="RESTRICT"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(String(30))
    final_score: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    final_feedback: Mapped[str | None] = mapped_column(Text)
    final_error_type: Mapped[str | None] = mapped_column(String(80))
    review_notes: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScoreRevision(Base):
    __tablename__ = "score_revisions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teacher_reviews.id", ondelete="RESTRICT"), index=True
    )
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answers.id", ondelete="RESTRICT"), index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    previous_score: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    new_score: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    previous_feedback: Mapped[str | None] = mapped_column(Text)
    new_feedback: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SubmissionScoreSnapshot(Base):
    __tablename__ = "submission_score_snapshots"
    __table_args__ = (UniqueConstraint("submission_id", "version", name="uq_snapshot_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="RESTRICT")
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="RESTRICT")
    )
    total_score: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    max_score: Mapped[Any] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(30), index=True)
    generated_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    version: Mapped[int] = mapped_column(Integer)
    details: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )


class ReferenceAnswerVersion(Base):
    __tablename__ = "reference_answer_versions"
    __table_args__ = (
        UniqueConstraint("question_id", "version", name="uq_reference_answer_question_version"),
        UniqueConstraint("origin_answer_candidate_id", name="uq_reference_answer_origin_candidate"),
        UniqueConstraint("materialization_key", name="uq_reference_answer_materialization_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    source_file: Mapped[str | None] = mapped_column(String(512))
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_region: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_content: Mapped[str] = mapped_column(Text)
    normalized_content: Mapped[str] = mapped_column(Text)
    structured_content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    teacher_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_answer_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assignment_answer_draft_candidates.id",
            name="fk_reference_answer_origin_candidate",
            ondelete="SET NULL",
            use_alter=True,
        ),
    )
    materialization_key: Mapped[str | None] = mapped_column(String(64))


class StructuredRubricVersion(Base):
    __tablename__ = "structured_rubric_versions"
    __table_args__ = (
        UniqueConstraint("question_id", "rubric_version", name="uq_structured_rubric_version"),
        UniqueConstraint(
            "origin_rubric_candidate_id", name="uq_structured_rubric_origin_candidate"
        ),
        UniqueConstraint("materialization_key", name="uq_structured_rubric_materialization_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    question_version: Mapped[str] = mapped_column(String(100))
    reference_answer_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reference_answer_versions.id", ondelete="RESTRICT"), index=True
    )
    rubric_version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    total_points: Mapped[Any] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_rubric_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assignment_rubric_draft_candidates.id",
            name="fk_structured_rubric_origin_candidate",
            ondelete="SET NULL",
            use_alter=True,
        ),
    )
    materialization_key: Mapped[str | None] = mapped_column(String(64))


class RubricCriterion(Base):
    __tablename__ = "rubric_criteria"
    __table_args__ = (
        UniqueConstraint("rubric_version_id", "stable_key", name="uq_rubric_criterion_key"),
        UniqueConstraint("rubric_version_id", "display_order", name="uq_rubric_criterion_order"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("structured_rubric_versions.id", ondelete="CASCADE"), index=True
    )
    stable_key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    max_points: Mapped[Any] = mapped_column(Numeric(10, 2))
    display_order: Mapped[int] = mapped_column(Integer)
    criterion_type: Mapped[str] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(default=True)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    expected_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_mode: Mapped[str] = mapped_column(String(24))
    manual_review_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    partial_credit_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_category: Mapped[str | None] = mapped_column(String(80))
    validation_rule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class AssignmentReviewSession(TimestampMixin, Base):
    __tablename__ = "assignment_review_sessions"
    __table_args__ = (
        CheckConstraint("review_version > 0", name="ck_assignment_review_version_positive"),
        CheckConstraint("generation > 0", name="ck_assignment_review_generation_positive"),
        Index(
            "uq_assignment_review_active",
            "assignment_id",
            unique=True,
            sqlite_where=text(
                "status IN ('draft','in_review','changes_required',"
                "'ready_for_binding','ready_to_publish')"
            ),
            postgresql_where=text(
                "status IN ('draft','in_review','changes_required',"
                "'ready_for_binding','ready_to_publish')"
            ),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_generation_jobs.id", ondelete="RESTRICT")
    )
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="RESTRICT")
    )
    generation: Mapped[int] = mapped_column(Integer)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    review_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    risk_ledger_hash: Mapped[str] = mapped_column(String(64))
    blocking_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    info_count: Mapped[int] = mapped_column(Integer, default=0)
    expected_assignment_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="RESTRICT")
    )
    structured_binding_hash: Mapped[str] = mapped_column(String(64))
    legacy_rubric_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssignmentReviewItem(TimestampMixin, Base):
    __tablename__ = "assignment_review_items"
    __table_args__ = (
        UniqueConstraint(
            "review_session_id", "issue_code", "source_hash", name="uq_review_item_source"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_review_sessions.id", ondelete="RESTRICT"), index=True
    )
    section: Mapped[str] = mapped_column(String(32), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    field_name: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), index=True)
    issue_code: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    eligibility: Mapped[bool] = mapped_column(Boolean, default=False)
    teacher_action: Mapped[str | None] = mapped_column(String(24))
    teacher_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssignmentExplicitConfirmation(Base):
    __tablename__ = "assignment_explicit_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "review_session_id",
            "confirmation_type",
            "confirmation_version",
            name="uq_assignment_confirmation_version",
        ),
        CheckConstraint(
            "confirmation_version > 0", name="ck_assignment_confirmation_version_positive"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_review_sessions.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    confirmation_type: Mapped[str] = mapped_column(String(32), index=True)
    confirmed_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_hash: Mapped[str] = mapped_column(String(64))
    fingerprint_schema_version: Mapped[str | None] = mapped_column(String(40))
    paper_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="RESTRICT"), index=True
    )
    question_scope_hash: Mapped[str | None] = mapped_column(String(64))
    confirmation_origin: Mapped[str | None] = mapped_column(String(24))
    confirmation_version: Mapped[int] = mapped_column(Integer)
    confirmed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(160))


class AssignmentRubricPublicationBinding(Base):
    __tablename__ = "assignment_rubric_publication_bindings"
    __table_args__ = (
        UniqueConstraint("assignment_id", "binding_version", name="uq_assignment_binding_version"),
        UniqueConstraint(
            "review_session_id", "source_binding_hash", name="uq_review_binding_source"
        ),
        CheckConstraint("binding_version > 0", name="ck_assignment_binding_version_positive"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    review_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_review_sessions.id", ondelete="RESTRICT"), index=True
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="RESTRICT")
    )
    legacy_rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="RESTRICT"), unique=True
    )
    binding_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    source_binding_hash: Mapped[str] = mapped_column(String(64))
    source_semantic_hash: Mapped[str | None] = mapped_column(String(64))
    target_legacy_hash: Mapped[str | None] = mapped_column(String(64))
    projection_profile: Mapped[str | None] = mapped_column(String(64))
    projection_version: Mapped[str | None] = mapped_column(String(40))
    loss_report: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    loss_report_hash: Mapped[str | None] = mapped_column(String(64))
    mapping: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssignmentPublishReadinessSnapshot(Base):
    __tablename__ = "assignment_publish_readiness_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    review_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_review_sessions.id", ondelete="RESTRICT"), index=True
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_versions.id", ondelete="RESTRICT")
    )
    legacy_rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="RESTRICT")
    )
    binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_rubric_publication_bindings.id", ondelete="RESTRICT")
    )
    generation: Mapped[int] = mapped_column(Integer)
    draft_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_draft_revisions.id", ondelete="RESTRICT")
    )
    risk_ledger_hash: Mapped[str] = mapped_column(String(64))
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    assignment_state_hash: Mapped[str] = mapped_column(String(64))
    class_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_score: Mapped[Any] = mapped_column(Numeric(10, 2))
    issue_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    readiness_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class MathValidationJob(TimestampMixin, Base):
    __tablename__ = "math_validation_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_math_validation_idempotency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), index=True
    )
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answers.id", ondelete="RESTRICT"), index=True
    )
    recognition_evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_recognition_evidence.id", ondelete="RESTRICT"), index=True
    )
    scoring_input_version: Mapped[str] = mapped_column(String(160))
    reference_answer_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reference_answer_versions.id", ondelete="RESTRICT")
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("structured_rubric_versions.id", ondelete="RESTRICT")
    )
    engine: Mapped[str] = mapped_column(String(80), default="ahamark-safe-math")
    engine_version: Mapped[str] = mapped_column(String(80))
    config_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    celery_task_id: Mapped[str | None] = mapped_column(String(50), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(String(80))


class CriterionValidationResult(Base):
    __tablename__ = "criterion_validation_results"
    __table_args__ = (
        UniqueConstraint(
            "validation_job_id", "criterion_id", "generation", name="uq_criterion_validation_run"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("math_validation_jobs.id", ondelete="CASCADE"), index=True
    )
    criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_criteria.id", ondelete="RESTRICT"), index=True
    )
    generation: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(30), index=True)
    suggested_points: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    normalized_student_input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    normalized_expected_input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    comparison_method: Mapped[str] = mapped_column(String(80))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[int] = mapped_column(Integer)
    engine_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class GradeRelease(TimestampMixin, Base):
    __tablename__ = "grade_releases"
    __table_args__ = (
        UniqueConstraint("assignment_id", "class_id", "version", name="uq_grade_release_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    release_mode: Mapped[str] = mapped_column(String(30), default="score_and_feedback")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    notes: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)


class GradeReleaseItem(Base):
    __tablename__ = "grade_release_items"
    __table_args__ = (
        UniqueConstraint("grade_release_id", "submission_id", name="uq_release_submission"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grade_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grade_releases.id", ondelete="RESTRICT"), index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), index=True
    )
    score_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_score_snapshots.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="included", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ReportJob(Base):
    __tablename__ = "report_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    grade_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grade_releases.id", ondelete="RESTRICT"), index=True
    )
    report_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stored_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT")
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ReportJobStudentScope(Base):
    __tablename__ = "report_job_student_scopes"
    report_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("report_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classes.id", ondelete="RESTRICT"), index=True
    )
    grade_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grade_releases.id", ondelete="RESTRICT"), index=True
    )
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    status: Mapped[str] = mapped_column(String(30), default="complete", index=True)
    source_snapshot_count: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TeachingInsight(TimestampMixin, Base):
    __tablename__ = "teaching_insights"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    analytics_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analytics_snapshots.id", ondelete="RESTRICT"), index=True
    )
    insight_type: Mapped[str] = mapped_column(String(40), default="class_review")
    provider: Mapped[str] = mapped_column(String(80), default="rule_based")
    provider_version: Mapped[str] = mapped_column(String(40), default="1.0")
    prompt_version: Mapped[str] = mapped_column(String(40), default="rules-v1")
    status: Mapped[str] = mapped_column(String(30), default="generated", index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))


class AIScoringJob(TimestampMixin, Base):
    __tablename__ = "ai_scoring_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_ai_scoring_idempotency"),
        UniqueConstraint("student_answer_id", "generation", name="uq_ai_scoring_generation"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT")
    )
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id", ondelete="RESTRICT"))
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT")
    )
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_answers.id", ondelete="RESTRICT"), index=True
    )
    recognition_evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_recognition_evidence.id", ondelete="RESTRICT")
    )
    reference_answer_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reference_answer_versions.id", ondelete="RESTRICT")
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("structured_rubric_versions.id", ondelete="RESTRICT")
    )
    math_validation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("math_validation_jobs.id", ondelete="RESTRICT")
    )
    question_version: Mapped[str] = mapped_column(String(100))
    scoring_input_version: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    generation: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    model_snapshot: Mapped[str | None] = mapped_column(String(160))
    endpoint_mode: Mapped[str] = mapped_column(String(80), default="responses")
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    provider_config_version: Mapped[str] = mapped_column(String(80))
    grading_config_version: Mapped[str] = mapped_column(String(80))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    celery_task_id: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    image_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    estimated_cost: Mapped[Any | None] = mapped_column(Numeric(12, 6))
    retryable: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AICriterionSuggestion(Base):
    __tablename__ = "ai_criterion_suggestions"
    __table_args__ = (
        UniqueConstraint("ai_scoring_job_id", "criterion_id", name="uq_ai_criterion_job"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ai_scoring_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_scoring_jobs.id", ondelete="CASCADE"), index=True
    )
    criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_criteria.id", ondelete="RESTRICT")
    )
    criterion_stable_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(40))
    suggested_points: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    max_points: Mapped[Any] = mapped_column(Numeric(10, 2))
    confidence: Mapped[Any | None] = mapped_column(Numeric(6, 5))
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    validation_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    requires_review: Mapped[bool] = mapped_column(default=True)
    matched_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    detected_errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    manual_review_reason: Mapped[str | None] = mapped_column(Text)
    student_feedback: Mapped[str | None] = mapped_column(Text)
    teacher_note: Mapped[str | None] = mapped_column(Text)
    abstained: Mapped[bool] = mapped_column(default=False)
    deterministic_conflict: Mapped[bool] = mapped_column(default=False)
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AIFeedbackDraft(TimestampMixin, Base):
    __tablename__ = "ai_feedback_drafts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ai_scoring_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_scoring_jobs.id", ondelete="RESTRICT"), unique=True
    )
    student_feedback: Mapped[str] = mapped_column(Text, default="")
    teacher_summary: Mapped[str] = mapped_column(Text, default="")
    strengths: Mapped[list[str]] = mapped_column(JSON, default=list)
    improvements: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggestion_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    teacher_disposition: Mapped[str] = mapped_column(String(30), default="pending")
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AIProviderInvocation(Base):
    __tablename__ = "ai_provider_invocations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ai_scoring_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_scoring_jobs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(80))
    endpoint_mode: Mapped[str] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    retry_number: Mapped[int] = mapped_column(Integer, default=0)
    response_status: Mapped[str] = mapped_column(String(40))
    capability_gaps: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AISuggestionReview(Base):
    __tablename__ = "ai_suggestion_reviews"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_criterion_suggestions.id", ondelete="RESTRICT"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(30))
    original_points: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    selected_points: Mapped[Any | None] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(Text)
    scoring_input_version: Mapped[str] = mapped_column(String(160))
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("structured_rubric_versions.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProcessingRun(TimestampMixin, Base):
    __tablename__ = "processing_runs"
    __table_args__ = (
        UniqueConstraint(
            "grading_batch_id",
            "generation",
            name="uq_processing_run_batch_generation",
        ),
        CheckConstraint("generation > 0", name="ck_processing_run_generation_positive"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_input', 'waiting_codex', "
            "'awaiting_teacher_review', 'partially_failed', 'failed', 'stale', 'cancelled')",
            name="ck_processing_run_status",
        ),
        CheckConstraint(
            "mode IN ('codex_local')",
            name="ck_processing_run_mode",
        ),
        CheckConstraint(
            "submission_count >= 0 AND step_count >= 0 "
            "AND completed_step_count >= 0 AND failed_step_count >= 0 "
            "AND pending_codex_count >= 0",
            name="ck_processing_run_counters_nonnegative",
        ),
        CheckConstraint(
            "completed_step_count + failed_step_count <= step_count",
            name="ck_processing_run_terminal_counters_bounded",
        ),
        Index(
            "ix_processing_run_owner_batch_status",
            "owner_id",
            "grading_batch_id",
            "status",
        ),
        Index(
            "ix_processing_run_batch_request_hash",
            "grading_batch_id",
            "request_hash",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_processing_run_owner", ondelete="RESTRICT")
    )
    grading_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "grading_batches.id",
            name="fk_processing_run_grading_batch",
            ondelete="RESTRICT",
        )
    )
    status: Mapped[str] = mapped_column(String(30), default="queued")
    mode: Mapped[str] = mapped_column(String(30), default="codex_local")
    generation: Mapped[int] = mapped_column(Integer)
    input_version: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    input_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )
    submission_count: Mapped[int] = mapped_column(Integer, default=0)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_step_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_step_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_codex_count: Mapped[int] = mapped_column(Integer, default=0)
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["ProcessingStep"]] = relationship(back_populates="run")


class ProcessingRunCommand(TimestampMixin, Base):
    __tablename__ = "processing_run_commands"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "idempotency_key",
            name="uq_processing_run_command_owner_idempotency",
        ),
        CheckConstraint(
            "operation IN ('continue', 'retry', 'reconcile')",
            name="ck_processing_run_command_operation",
        ),
        CheckConstraint(
            "idempotency_key = trim(idempotency_key) "
            "AND length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_processing_run_command_idempotency_key",
        ),
        CheckConstraint(
            "length(request_hash) = 64",
            name="ck_processing_run_command_request_hash",
        ),
        CheckConstraint(
            "expected_generation IS NULL OR expected_generation > 0",
            name="ck_processing_run_command_expected_generation_positive",
        ),
        CheckConstraint(
            "result_generation > 0",
            name="ck_processing_run_command_result_generation_positive",
        ),
        CheckConstraint(
            "(operation = 'continue' AND source_run_id IS NULL "
            "AND expected_generation IS NULL) "
            "OR (operation = 'retry' AND source_run_id IS NOT NULL "
            "AND expected_generation IS NOT NULL) "
            "OR (operation = 'reconcile' AND source_run_id IS NOT NULL "
            "AND expected_generation IS NOT NULL AND source_run_id = result_run_id "
            "AND expected_generation = result_generation)",
            name="ck_processing_run_command_shape",
        ),
        Index(
            "ix_processing_run_command_owner_batch_created",
            "owner_id",
            "grading_batch_id",
            "created_at",
        ),
        Index(
            "ix_processing_run_command_source_operation",
            "source_run_id",
            "operation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_processing_run_command_owner",
            ondelete="RESTRICT",
        )
    )
    grading_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "grading_batches.id",
            name="fk_processing_run_command_grading_batch",
            ondelete="RESTRICT",
        )
    )
    operation: Mapped[str] = mapped_column(String(20))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "processing_runs.id",
            name="fk_processing_run_command_source_run",
            ondelete="RESTRICT",
        )
    )
    result_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "processing_runs.id",
            name="fk_processing_run_command_result_run",
            ondelete="RESTRICT",
        )
    )
    expected_generation: Mapped[int | None] = mapped_column(Integer)
    result_generation: Mapped[int] = mapped_column(Integer)


class ProcessingStep(TimestampMixin, Base):
    __tablename__ = "processing_steps"
    __table_args__ = (
        UniqueConstraint(
            "processing_run_id",
            "scope_key",
            "kind",
            "generation",
            name="uq_processing_step_run_scope_kind_generation",
        ),
        CheckConstraint("generation > 0", name="ck_processing_step_generation_positive"),
        CheckConstraint(
            "attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts",
            name="ck_processing_step_attempt_bounds",
        ),
        CheckConstraint(
            "kind IN ('recognition', 'codex_suggestion', 'review_readiness')",
            name="ck_processing_step_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'dispatched', 'running', 'succeeded', 'blocked_review', "
            "'retryable_failed', 'terminal_failed', 'stale', 'cancelled')",
            name="ck_processing_step_status",
        ),
        CheckConstraint(
            "(dispatch_token IS NULL AND dispatch_owner IS NULL "
            "AND dispatch_lease_expires_at IS NULL) "
            "OR (dispatch_token IS NOT NULL AND dispatch_owner IS NOT NULL "
            "AND dispatch_lease_expires_at IS NOT NULL)",
            name="ck_processing_step_dispatch_lease_complete",
        ),
        Index(
            "ix_processing_step_run_status_available",
            "processing_run_id",
            "status",
            "available_at",
        ),
        Index(
            "ix_processing_step_submission_kind_status",
            "submission_id",
            "kind",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    processing_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "processing_runs.id",
            name="fk_processing_step_run",
            ondelete="RESTRICT",
        )
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "submissions.id",
            name="fk_processing_step_submission",
            ondelete="RESTRICT",
        )
    )
    student_answer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "student_answers.id",
            name="fk_processing_step_student_answer",
            ondelete="RESTRICT",
        )
    )
    scope_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40))
    stage: Mapped[str] = mapped_column(
        String(40),
        default="answer_recognition",
        server_default=text("'answer_recognition'"),
    )
    status: Mapped[str] = mapped_column(String(30), default="pending")
    generation: Mapped[int] = mapped_column(Integer)
    input_version: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_token: Mapped[str | None] = mapped_column(String(128))
    dispatch_owner: Mapped[str | None] = mapped_column(String(160))
    dispatch_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recognition_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "submission_recognition_jobs.id",
            name="fk_processing_step_recognition_job",
            ondelete="RESTRICT",
        )
    )
    submission_processing_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "submission_processing_jobs.id",
            name="fk_processing_step_submission_processing_job",
            ondelete="RESTRICT",
        ),
        index=True,
    )
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[ProcessingRun] = relationship(back_populates="steps")
    codex_work_item: Mapped["CodexWorkItem | None"] = relationship(back_populates="step")


class CodexWorkItem(TimestampMixin, Base):
    __tablename__ = "codex_work_items"
    __table_args__ = (
        UniqueConstraint(
            "processing_step_id",
            name="uq_codex_work_item_processing_step",
        ),
        CheckConstraint("generation > 0", name="ck_codex_work_item_generation_positive"),
        CheckConstraint(
            "attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts",
            name="ck_codex_work_item_attempt_bounds",
        ),
        CheckConstraint(
            "provider = 'codex_local'",
            name="ck_codex_work_item_provider",
        ),
        CheckConstraint(
            "status IN ('queued', 'leased', 'submitted', 'applied', 'retryable_failed', "
            "'terminal_failed', 'stale', 'cancelled')",
            name="ck_codex_work_item_status",
        ),
        CheckConstraint(
            "(status = 'leased' AND lease_token_hash IS NOT NULL "
            "AND length(lease_token_hash) = 64 AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'leased' AND lease_token_hash IS NULL "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_codex_work_item_lease_state",
        ),
        CheckConstraint(
            "length(request_hash) = 64",
            name="ck_codex_work_item_request_hash",
        ),
        CheckConstraint(
            "(response_payload IS NULL AND response_hash IS NULL "
            "AND submitted_lease_token_hash IS NULL AND submitted_at IS NULL) "
            "OR (response_payload IS NOT NULL AND response_hash IS NOT NULL "
            "AND length(response_hash) = 64 AND submitted_lease_token_hash IS NOT NULL "
            "AND length(submitted_lease_token_hash) = 64 AND submitted_at IS NOT NULL)",
            name="ck_codex_work_item_submission_audit_complete",
        ),
        CheckConstraint(
            "(status IN ('submitted', 'applied') AND response_payload IS NOT NULL "
            "AND response_hash IS NOT NULL AND submitted_lease_token_hash IS NOT NULL "
            "AND submitted_at IS NOT NULL) "
            "OR (status IN ('queued', 'leased', 'retryable_failed') "
            "AND response_payload IS NULL AND response_hash IS NULL "
            "AND submitted_lease_token_hash IS NULL AND submitted_at IS NULL) "
            "OR (status IN ('terminal_failed', 'stale', 'cancelled') "
            "AND ((response_payload IS NULL AND response_hash IS NULL "
            "AND submitted_lease_token_hash IS NULL AND submitted_at IS NULL) "
            "OR (response_payload IS NOT NULL AND response_hash IS NOT NULL "
            "AND submitted_lease_token_hash IS NOT NULL AND submitted_at IS NOT NULL)))",
            name="ck_codex_work_item_submission_state",
        ),
        CheckConstraint(
            "(grading_job_id IS NULL AND grading_result_id IS NULL) "
            "OR (grading_job_id IS NOT NULL AND grading_result_id IS NOT NULL)",
            name="ck_codex_work_item_applied_refs_complete",
        ),
        CheckConstraint(
            "(status = 'applied' AND response_payload IS NOT NULL AND response_hash IS NOT NULL "
            "AND grading_job_id IS NOT NULL AND grading_result_id IS NOT NULL "
            "AND applied_at IS NOT NULL) "
            "OR (status <> 'applied' AND grading_job_id IS NULL AND grading_result_id IS NULL "
            "AND applied_at IS NULL)",
            name="ck_codex_work_item_applied_state",
        ),
        Index(
            "ix_codex_work_item_owner_batch_status_available",
            "owner_id",
            "grading_batch_id",
            "status",
            "available_at",
        ),
        Index(
            "ix_codex_work_item_submission_answer_status",
            "submission_id",
            "student_answer_id",
            "status",
        ),
        Index(
            "ix_codex_work_item_claim",
            "status",
            "available_at",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    processing_step_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "processing_steps.id",
            name="fk_codex_work_item_processing_step",
            ondelete="RESTRICT",
        )
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_codex_work_item_owner", ondelete="RESTRICT")
    )
    grading_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "grading_batches.id",
            name="fk_codex_work_item_grading_batch",
            ondelete="RESTRICT",
        )
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "submissions.id",
            name="fk_codex_work_item_submission",
            ondelete="RESTRICT",
        )
    )
    student_answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "student_answers.id",
            name="fk_codex_work_item_student_answer",
            ondelete="RESTRICT",
        )
    )
    status: Mapped[str] = mapped_column(String(30), default="queued")
    generation: Mapped[int] = mapped_column(Integer)
    input_version: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    )
    response_hash: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(80), default="codex_local")
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    config_version: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grading_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "grading_jobs.id",
            name="fk_codex_work_item_grading_job",
            ondelete="RESTRICT",
        )
    )
    grading_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "grading_results.id",
            name="fk_codex_work_item_grading_result",
            ondelete="RESTRICT",
        )
    )
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    step: Mapped[ProcessingStep] = relationship(back_populates="codex_work_item")
