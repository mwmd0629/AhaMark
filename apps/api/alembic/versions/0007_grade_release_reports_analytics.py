"""immutable grade releases, reports, analytics, and teaching insights

Revision ID: 0007_grade_release_reports_analytics
Revises: 0006_submissions_grading_review
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

revision = "0007_grade_release_reports_analytics"
down_revision = "0006_submissions_grading_review"
branch_labels = None
depends_on = None

# Historical migrations must not read the live ORM metadata: later model changes
# would otherwise be applied before their own revisions.  These definitions are
# the exact five-table snapshot that 0007 created when it was introduced.
HISTORICAL_METADATA = sa.MetaData()


def _referenced_table(name: str) -> sa.Table:
    return sa.Table(
        name,
        HISTORICAL_METADATA,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    )


for _name in (
    "users",
    "assignments",
    "classes",
    "students",
    "submissions",
    "submission_score_snapshots",
    "stored_files",
):
    _referenced_table(_name)


grade_releases = sa.Table(
    "grade_releases",
    HISTORICAL_METADATA,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "owner_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "assignment_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "class_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("classes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(length=30), nullable=False, index=True),
    sa.Column("release_mode", sa.String(length=30), nullable=False),
    sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "created_by",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("notes", sa.Text(), nullable=True),
    sa.Column("idempotency_key", sa.String(length=100), nullable=True, unique=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "assignment_id",
        "class_id",
        "version",
        name="uq_grade_release_version",
    ),
)

grade_release_items = sa.Table(
    "grade_release_items",
    HISTORICAL_METADATA,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "grade_release_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("grade_releases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "student_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "submission_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("submissions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "score_snapshot_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("submission_score_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column("status", sa.String(length=30), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "grade_release_id",
        "submission_id",
        name="uq_release_submission",
    ),
)

report_jobs = sa.Table(
    "report_jobs",
    HISTORICAL_METADATA,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "owner_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "assignment_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "class_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("classes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "grade_release_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("grade_releases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column("report_type", sa.String(length=40), nullable=False, index=True),
    sa.Column("status", sa.String(length=30), nullable=False, index=True),
    sa.Column("progress", sa.Integer(), nullable=False),
    sa.Column(
        "stored_file_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("stored_files.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("error_code", sa.String(length=80), nullable=True),
    sa.Column("error_message", sa.Text(), nullable=True),
    sa.Column("idempotency_key", sa.String(length=100), nullable=False, unique=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, index=True),
)

analytics_snapshots = sa.Table(
    "analytics_snapshots",
    HISTORICAL_METADATA,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "owner_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "assignment_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "class_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("classes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "grade_release_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("grade_releases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column("schema_version", sa.String(length=20), nullable=False),
    sa.Column("status", sa.String(length=30), nullable=False, index=True),
    sa.Column("source_snapshot_count", sa.Integer(), nullable=False),
    sa.Column(
        "metrics",
        sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
        nullable=False,
    ),
    sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

teaching_insights = sa.Table(
    "teaching_insights",
    HISTORICAL_METADATA,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "owner_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "analytics_snapshot_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("analytics_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column("insight_type", sa.String(length=40), nullable=False),
    sa.Column("provider", sa.String(length=80), nullable=False),
    sa.Column("provider_version", sa.String(length=40), nullable=False),
    sa.Column("prompt_version", sa.String(length=40), nullable=False),
    sa.Column("status", sa.String(length=30), nullable=False, index=True),
    sa.Column(
        "content",
        sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
        nullable=False,
    ),
    sa.Column(
        "evidence",
        sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

HISTORICAL_TABLES = (
    grade_releases,
    grade_release_items,
    report_jobs,
    analytics_snapshots,
    teaching_insights,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in HISTORICAL_TABLES:
        bind.execute(CreateTable(table))
        for index in sorted(table.indexes, key=lambda candidate: candidate.name or ""):
            bind.execute(CreateIndex(index))


def downgrade() -> None:
    for table in reversed(HISTORICAL_TABLES):
        op.drop_table(table.name)
