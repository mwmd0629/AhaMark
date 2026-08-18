"""immutable grade releases, reports, analytics, and teaching insights

Revision ID: 0007_grade_release_reports_analytics
Revises: 0006_submissions_grading_review
"""

from collections.abc import Iterable

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_grade_release_reports_analytics"
down_revision = "0006_submissions_grading_review"
branch_labels = None
depends_on = None

TABLES = [
    "grade_releases",
    "grade_release_items",
    "report_jobs",
    "analytics_snapshots",
    "teaching_insights",
]

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _id_column() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _create_table(
    name: str,
    *elements: sa.Column | sa.Constraint,
    indexes: Iterable[str] = (),
) -> None:
    """Create the revision-0007 schema without consulting live ORM metadata."""

    op.create_table(name, *elements)
    for column in indexes:
        op.create_index(f"ix_{name}_{column}", name, [column])


def upgrade() -> None:
    _create_table(
        "grade_releases",
        _id_column(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("release_mode", sa.String(length=30), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "assignment_id", "class_id", "version", name="uq_grade_release_version"
        ),
        sa.UniqueConstraint("idempotency_key"),
        indexes=("owner_id", "assignment_id", "class_id", "status"),
    )

    _create_table(
        "grade_release_items",
        _id_column(),
        sa.Column("grade_release_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("score_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["grade_release_id"], ["grade_releases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["score_snapshot_id"], ["submission_score_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("grade_release_id", "submission_id", name="uq_release_submission"),
        indexes=("grade_release_id", "student_id", "submission_id", "score_snapshot_id", "status"),
    )

    _create_table(
        "report_jobs",
        _id_column(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("grade_release_id", sa.Uuid(), nullable=False),
        sa.Column("report_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("stored_file_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grade_release_id"], ["grade_releases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["stored_file_id"], ["stored_files.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key"),
        indexes=(
            "owner_id",
            "assignment_id",
            "class_id",
            "grade_release_id",
            "report_type",
            "status",
            "expires_at",
        ),
    )

    _create_table(
        "analytics_snapshots",
        _id_column(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("grade_release_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source_snapshot_count", sa.Integer(), nullable=False),
        sa.Column("metrics", JSON_DOCUMENT, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grade_release_id"], ["grade_releases.id"], ondelete="RESTRICT"),
        indexes=("owner_id", "assignment_id", "class_id", "grade_release_id", "status"),
    )

    _create_table(
        "teaching_insights",
        _id_column(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("analytics_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("insight_type", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("provider_version", sa.String(length=40), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("content", JSON_DOCUMENT, nullable=False),
        sa.Column("evidence", JSON_DOCUMENT, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["analytics_snapshot_id"], ["analytics_snapshots.id"], ondelete="RESTRICT"
        ),
        indexes=("owner_id", "analytics_snapshot_id", "status"),
    )


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
