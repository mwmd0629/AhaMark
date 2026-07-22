import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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
