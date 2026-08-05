"""Submission collection, grading, review, and immutable score snapshots.

Revision ID: 0006_submissions_grading_review
Revises: 0005_nullable_question_score

This historical migration is intentionally self-contained.  Its first revision imported
the live ORM metadata, which made an empty-database replay change whenever current models
changed.  The definitions below are frozen to the exact schema emitted by commit
f7783f0073592140c1400d6e7f41ffb17638c64e, where this migration was introduced.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

revision: str = "0006_submissions_grading_review"
down_revision: str | None = "0005_nullable_question_score"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGINAL_COMMIT = "f7783f0073592140c1400d6e7f41ffb17638c64e"
ORIGINAL_POSTGRESQL_DDL_SHA256 = "7b51a51adb536dcda9934e9332b9221c3471e730e2a9d31989bafb06bc0a5681"
ORIGINAL_SQLITE_DDL_SHA256 = "bcfa404a77ba05597bb4545febea60997704fa2f518e3c7fc6baccf6d586b50a"

UUID = postgresql.UUID(as_uuid=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
METADATA = sa.MetaData()

# These tables already exist at revision 0005.  Minimal declarations let SQLAlchemy
# resolve historical foreign keys while keeping 0006 responsible only for TABLES below.
for _existing_table in (
    "users",
    "assignments",
    "classes",
    "students",
    "stored_files",
    "questions",
    "rubric_versions",
    "rubric_items",
    "paper_versions",
):
    sa.Table(_existing_table, METADATA, sa.Column("id", UUID, primary_key=True))


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


grading_batches = sa.Table(
    "grading_batches",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    sa.Column(
        "assignment_id", UUID, sa.ForeignKey("assignments.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column("class_id", UUID, sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("name", sa.String(160)),
    sa.Column("description", sa.Text()),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("submission_count", sa.Integer(), nullable=False),
    sa.Column("recognized_count", sa.Integer(), nullable=False),
    sa.Column("graded_count", sa.Integer(), nullable=False),
    sa.Column("reviewed_count", sa.Integer(), nullable=False),
    sa.Column("failed_count", sa.Integer(), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    *_timestamps(),
)
for column in ("owner_id", "assignment_id", "class_id", "status"):
    sa.Index(f"ix_grading_batches_{column}", grading_batches.c[column])

submissions = sa.Table(
    "submissions",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    sa.Column(
        "grading_batch_id",
        UUID,
        sa.ForeignKey("grading_batches.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "assignment_id", UUID, sa.ForeignKey("assignments.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column("class_id", UUID, sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("student_id", UUID, sa.ForeignKey("students.id", ondelete="RESTRICT")),
    sa.Column("attempt_number", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("source", sa.String(30), nullable=False),
    sa.Column("submitted_at", sa.DateTime(timezone=True)),
    sa.Column("recognized_at", sa.DateTime(timezone=True)),
    sa.Column("graded_at", sa.DateTime(timezone=True)),
    sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    sa.Column("finalized_at", sa.DateTime(timezone=True)),
    *_timestamps(),
    sa.UniqueConstraint(
        "grading_batch_id", "student_id", "attempt_number", name="uq_submission_attempt"
    ),
)
for column in (
    "owner_id",
    "grading_batch_id",
    "assignment_id",
    "class_id",
    "student_id",
    "status",
):
    sa.Index(f"ix_submissions_{column}", submissions.c[column])

submission_file_matches = sa.Table(
    "submission_file_matches",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "grading_batch_id",
        UUID,
        sa.ForeignKey("grading_batches.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "stored_file_id",
        UUID,
        sa.ForeignKey("stored_files.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    ),
    sa.Column("suggested_student_id", UUID, sa.ForeignKey("students.id", ondelete="SET NULL")),
    sa.Column("confirmed_student_id", UUID, sa.ForeignKey("students.id", ondelete="SET NULL")),
    sa.Column("match_method", sa.String(30), nullable=False),
    sa.Column("confidence", sa.Numeric(6, 5)),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("reason", sa.String(255)),
    sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("confirmed_at", sa.DateTime(timezone=True)),
)
for column in ("grading_batch_id", "status"):
    sa.Index(f"ix_submission_file_matches_{column}", submission_file_matches.c[column])

submission_pages = sa.Table(
    "submission_pages",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "submission_id", UUID, sa.ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column(
        "stored_file_id",
        UUID,
        sa.ForeignKey("stored_files.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("page_number", sa.Integer(), nullable=False),
    sa.Column("source_page_number", sa.Integer()),
    sa.Column("width", sa.Integer()),
    sa.Column("height", sa.Integer()),
    sa.Column("rotation", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("rendered_storage_key", sa.String(512)),
    sa.Column("processed_storage_key", sa.String(512)),
    sa.Column("thumbnail_storage_key", sa.String(512)),
    *_timestamps(),
    sa.UniqueConstraint("submission_id", "page_number", name="uq_submission_page"),
)
for column in ("submission_id", "stored_file_id", "status"):
    sa.Index(f"ix_submission_pages_{column}", submission_pages.c[column])

student_answers = sa.Table(
    "student_answers",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "submission_id", UUID, sa.ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column(
        "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column("question_version_reference", sa.String(100), nullable=False),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("recognized_text", sa.Text()),
    sa.Column("recognized_latex", sa.Text()),
    sa.Column("corrected_text", sa.Text()),
    sa.Column("corrected_latex", sa.Text()),
    sa.Column("recognition_confidence", sa.Numeric(6, 5)),
    sa.Column("recognition_provider", sa.String(80)),
    sa.Column("recognition_provider_version", sa.String(80)),
    sa.Column("is_blank", sa.Boolean(), nullable=False),
    sa.Column("requires_review", sa.Boolean(), nullable=False),
    *_timestamps(),
    sa.UniqueConstraint("submission_id", "question_id", name="uq_submission_question_answer"),
)
for column in ("submission_id", "question_id", "status", "requires_review"):
    sa.Index(f"ix_student_answers_{column}", student_answers.c[column])

student_answer_regions = sa.Table(
    "student_answer_regions",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "student_answer_id",
        UUID,
        sa.ForeignKey("student_answers.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "submission_page_id",
        UUID,
        sa.ForeignKey("submission_pages.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("region_type", sa.String(30), nullable=False),
    sa.Column("x", sa.Numeric(8, 6), nullable=False),
    sa.Column("y", sa.Numeric(8, 6), nullable=False),
    sa.Column("width", sa.Numeric(8, 6), nullable=False),
    sa.Column("height", sa.Numeric(8, 6), nullable=False),
    sa.Column("source", sa.String(20), nullable=False),
    sa.Column("confidence", sa.Numeric(6, 5)),
    *_timestamps(),
)
for column in ("student_answer_id", "submission_page_id"):
    sa.Index(f"ix_student_answer_regions_{column}", student_answer_regions.c[column])

submission_recognition_jobs = sa.Table(
    "submission_recognition_jobs",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    sa.Column(
        "submission_id", UUID, sa.ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("provider", sa.String(80), nullable=False),
    sa.Column("provider_version", sa.String(80), nullable=False),
    sa.Column("idempotency_key", sa.String(100), nullable=False),
    sa.Column("error_code", sa.String(80)),
    sa.Column("error_message", sa.Text()),
    *_timestamps(),
    sa.UniqueConstraint(
        "owner_id", "idempotency_key", name="uq_submission_recognition_idempotency"
    ),
)
for column in ("owner_id", "submission_id", "status"):
    sa.Index(f"ix_submission_recognition_jobs_{column}", submission_recognition_jobs.c[column])

grading_jobs = sa.Table(
    "grading_jobs",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    sa.Column(
        "grading_batch_id",
        UUID,
        sa.ForeignKey("grading_batches.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "submission_id", UUID, sa.ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column("question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT")),
    sa.Column(
        "rubric_version_id",
        UUID,
        sa.ForeignKey("rubric_versions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("provider", sa.String(80), nullable=False),
    sa.Column("provider_version", sa.String(80), nullable=False),
    sa.Column("prompt_version", sa.String(80), nullable=False),
    sa.Column("config_version", sa.String(80), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("idempotency_key", sa.String(100), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("failed_at", sa.DateTime(timezone=True)),
    sa.Column("error_code", sa.String(80)),
    sa.Column("error_message", sa.Text()),
    *_timestamps(),
    sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_grading_idempotency"),
)
for column in ("owner_id", "grading_batch_id", "submission_id", "question_id", "status"):
    sa.Index(f"ix_grading_jobs_{column}", grading_jobs.c[column])

grading_results = sa.Table(
    "grading_results",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "grading_job_id",
        UUID,
        sa.ForeignKey("grading_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "student_answer_id",
        UUID,
        sa.ForeignKey("student_answers.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column(
        "rubric_version_id",
        UUID,
        sa.ForeignKey("rubric_versions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("grading_method", sa.String(30), nullable=False),
    sa.Column("provider", sa.String(80), nullable=False),
    sa.Column("provider_version", sa.String(80), nullable=False),
    sa.Column("prompt_version", sa.String(80), nullable=False),
    sa.Column("score", sa.Numeric(10, 2)),
    sa.Column("max_score", sa.Numeric(10, 2), nullable=False),
    sa.Column("confidence", sa.Numeric(6, 5)),
    sa.Column("recognized_answer_snapshot", sa.Text()),
    sa.Column("reasoning_summary", sa.Text()),
    sa.Column("error_type", sa.String(80)),
    sa.Column("student_feedback", sa.Text()),
    sa.Column("requires_review", sa.Boolean(), nullable=False),
    sa.Column("status", sa.String(30), nullable=False),
    *_timestamps(),
)
for column in (
    "grading_job_id",
    "student_answer_id",
    "question_id",
    "rubric_version_id",
    "requires_review",
    "status",
):
    sa.Index(f"ix_grading_results_{column}", grading_results.c[column])

grading_criterion_results = sa.Table(
    "grading_criterion_results",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "grading_result_id",
        UUID,
        sa.ForeignKey("grading_results.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "rubric_item_id",
        UUID,
        sa.ForeignKey("rubric_items.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("awarded_points", sa.Numeric(10, 2)),
    sa.Column("max_points", sa.Numeric(10, 2), nullable=False),
    sa.Column("reason", sa.Text()),
    sa.Column("confidence", sa.Numeric(6, 5)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
sa.Index(
    "ix_grading_criterion_results_grading_result_id",
    grading_criterion_results.c.grading_result_id,
)

grading_evidence = sa.Table(
    "grading_evidence",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "grading_result_id",
        UUID,
        sa.ForeignKey("grading_results.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "student_answer_id",
        UUID,
        sa.ForeignKey("student_answers.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "submission_page_id",
        UUID,
        sa.ForeignKey("submission_pages.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("evidence_type", sa.String(30), nullable=False),
    sa.Column("quote", sa.String(500)),
    sa.Column("x", sa.Numeric(8, 6)),
    sa.Column("y", sa.Numeric(8, 6)),
    sa.Column("width", sa.Numeric(8, 6)),
    sa.Column("height", sa.Numeric(8, 6)),
    sa.Column("description", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
sa.Index("ix_grading_evidence_grading_result_id", grading_evidence.c.grading_result_id)

teacher_reviews = sa.Table(
    "teacher_reviews",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("grading_result_id", UUID, sa.ForeignKey("grading_results.id", ondelete="RESTRICT")),
    sa.Column(
        "student_answer_id",
        UUID,
        sa.ForeignKey("student_answers.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("reviewer_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("decision", sa.String(30), nullable=False),
    sa.Column("final_score", sa.Numeric(10, 2)),
    sa.Column("final_feedback", sa.Text()),
    sa.Column("final_error_type", sa.String(80)),
    sa.Column("review_notes", sa.Text()),
    sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    *_timestamps(),
    sa.UniqueConstraint("student_answer_id", name="uq_answer_review"),
)
sa.Index("ix_teacher_reviews_student_answer_id", teacher_reviews.c.student_answer_id)

score_revisions = sa.Table(
    "score_revisions",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "teacher_review_id",
        UUID,
        sa.ForeignKey("teacher_reviews.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "student_answer_id",
        UUID,
        sa.ForeignKey("student_answers.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("actor_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("previous_score", sa.Numeric(10, 2)),
    sa.Column("new_score", sa.Numeric(10, 2)),
    sa.Column("previous_feedback", sa.Text()),
    sa.Column("new_feedback", sa.Text()),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
for column in ("teacher_review_id", "student_answer_id"):
    sa.Index(f"ix_score_revisions_{column}", score_revisions.c[column])

submission_score_snapshots = sa.Table(
    "submission_score_snapshots",
    METADATA,
    sa.Column("id", UUID, primary_key=True),
    sa.Column(
        "submission_id", UUID, sa.ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column(
        "assignment_id", UUID, sa.ForeignKey("assignments.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column(
        "student_id", UUID, sa.ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    ),
    sa.Column(
        "paper_version_id",
        UUID,
        sa.ForeignKey("paper_versions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "rubric_version_id",
        UUID,
        sa.ForeignKey("rubric_versions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("total_score", sa.Numeric(10, 2)),
    sa.Column("max_score", sa.Numeric(10, 2), nullable=False),
    sa.Column("status", sa.String(30), nullable=False),
    sa.Column("generated_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("details", JSON, nullable=False),
    sa.UniqueConstraint("submission_id", "version", name="uq_snapshot_version"),
)
for column in ("submission_id", "assignment_id", "student_id", "status"):
    sa.Index(f"ix_submission_score_snapshots_{column}", submission_score_snapshots.c[column])

TABLES = [
    grading_batches,
    submissions,
    submission_file_matches,
    submission_pages,
    student_answers,
    student_answer_regions,
    submission_recognition_jobs,
    grading_jobs,
    grading_results,
    grading_criterion_results,
    grading_evidence,
    teacher_reviews,
    score_revisions,
    submission_score_snapshots,
]


def compiled_schema(dialect: sa.engine.Dialect) -> str:
    """Return a stable representation used by the historical-schema guard tests."""

    statements: list[str] = []
    for table in TABLES:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        statements.extend(
            sorted(
                str(CreateIndex(index).compile(dialect=dialect)).strip()
                for index in table.indexes
            )
        )
    return "\n".join(statements)


def upgrade() -> None:
    for table in TABLES:
        op.execute(CreateTable(table))
        for index in table.indexes:
            op.execute(CreateIndex(index))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table.name)
