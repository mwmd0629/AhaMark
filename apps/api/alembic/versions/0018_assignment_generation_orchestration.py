"""Assignment generation jobs and immutable draft revisions.

Revision ID: 0018_assignment_generation_orchestration
Revises: 0017_ai_grading_suggestions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_assignment_generation_orchestration"
down_revision: str | None = "0017_ai_grading_suggestions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid()
JSON = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "assignment_generation_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "assignment_id",
            UUID,
            sa.ForeignKey("assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_stage", sa.String(32)),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("provider_mode", sa.String(32), nullable=False),
        sa.Column("provider_config_version", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("celery_task_id", sa.String(80)),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation > 0", name="ck_assignment_generation_positive"),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_assignment_generation_progress"),
        sa.UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_assignment_generation_idempotency"
        ),
        sa.UniqueConstraint("assignment_id", "generation", name="uq_assignment_generation_number"),
    )
    op.create_index(
        "ix_assignment_generation_owner_assignment_status",
        "assignment_generation_jobs",
        ["owner_id", "assignment_id", "status"],
    )
    op.create_index(
        "uq_assignment_generation_active",
        "assignment_generation_jobs",
        ["assignment_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued','analyzing','processing_pages','extracting_questions',"
            "'generating_rubrics','validating')"
        ),
        sqlite_where=sa.text(
            "status IN ('queued','analyzing','processing_pages','extracting_questions',"
            "'generating_rubrics','validating')"
        ),
    )
    op.create_index(
        "ix_assignment_generation_jobs_owner_id", "assignment_generation_jobs", ["owner_id"]
    )
    op.create_index(
        "ix_assignment_generation_jobs_assignment_id",
        "assignment_generation_jobs",
        ["assignment_id"],
    )
    op.create_index(
        "ix_assignment_generation_jobs_status", "assignment_generation_jobs", ["status"]
    )
    op.create_index(
        "ix_assignment_generation_jobs_celery_task_id",
        "assignment_generation_jobs",
        ["celery_task_id"],
    )
    op.create_table(
        "assignment_draft_revisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "assignment_id",
            UUID,
            sa.ForeignKey("assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "generation_job_id",
            UUID,
            sa.ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "parent_revision_id",
            UUID,
            sa.ForeignKey("assignment_draft_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("draft_payload", JSON, nullable=False),
        sa.Column("risk_summary", JSON, nullable=False),
        sa.Column("teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("created_by_type", sa.String(32), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_assignment_draft_revision_positive"),
        sa.CheckConstraint(
            "teacher_edit_version >= 0", name="ck_assignment_draft_teacher_edit_version"
        ),
        sa.UniqueConstraint("assignment_id", "revision", name="uq_assignment_draft_revision"),
    )
    op.create_index(
        "ix_assignment_draft_revisions_owner_id", "assignment_draft_revisions", ["owner_id"]
    )
    op.create_index(
        "ix_assignment_draft_revisions_assignment_id",
        "assignment_draft_revisions",
        ["assignment_id"],
    )
    op.create_index(
        "ix_assignment_draft_revisions_status", "assignment_draft_revisions", ["status"]
    )
    op.create_table(
        "generation_stage_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "job_id",
            UUID,
            sa.ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("stage_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expected_teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("result_payload", JSON, nullable=False),
        sa.Column("provider_invocation_id", UUID),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("stage_generation > 0", name="ck_generation_stage_positive"),
        sa.UniqueConstraint("job_id", "stage", "stage_generation", name="uq_generation_stage_run"),
    )
    op.create_index("ix_generation_stage_results_job_id", "generation_stage_results", ["job_id"])
    op.create_index("ix_generation_stage_results_status", "generation_stage_results", ["status"])
    op.create_table(
        "generation_issues",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "assignment_id",
            UUID,
            sa.ForeignKey("assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID,
            sa.ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "draft_revision_id",
            UUID,
            sa.ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"),
        ),
        sa.Column("stage", sa.String(32)),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(40)),
        sa.Column("entity_id", sa.String(100)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("resolution_status", sa.String(24), nullable=False),
        sa.Column("resolved_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, cols in (
        ("ix_generation_issues_owner_id", ["owner_id"]),
        ("ix_generation_issues_assignment_id", ["assignment_id"]),
        ("ix_generation_issues_job_id", ["job_id"]),
        ("ix_generation_issues_severity", ["severity"]),
        ("ix_generation_issues_code", ["code"]),
    ):
        op.create_index(name, "generation_issues", cols)
    op.create_table(
        "assignment_generation_provider_invocations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "job_id",
            UUID,
            sa.ForeignKey("assignment_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stage_result_id",
            UUID,
            sa.ForeignKey("generation_stage_results.id", ondelete="SET NULL"),
        ),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160)),
        sa.Column("endpoint_mode", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64)),
        sa.Column("provider_request_id", sa.String(160)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_cost", sa.Numeric(12, 6)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assignment_generation_provider_invocations_job_id",
        "assignment_generation_provider_invocations",
        ["job_id"],
    )
    op.create_index(
        "ix_assignment_generation_provider_invocations_status",
        "assignment_generation_provider_invocations",
        ["status"],
    )


def downgrade() -> None:
    for table in (
        "assignment_generation_provider_invocations",
        "generation_issues",
        "generation_stage_results",
        "assignment_draft_revisions",
    ):
        op.drop_table(table)
    op.drop_index("uq_assignment_generation_active", table_name="assignment_generation_jobs")
    op.drop_table("assignment_generation_jobs")
