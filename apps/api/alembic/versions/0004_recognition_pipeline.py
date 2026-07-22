"""recognition pipeline

Revision ID: 0004_recognition_pipeline
Revises: 0003_assignments_papers_rubrics
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_recognition_pipeline"
down_revision: str | None = "0003_assignments_papers_rubrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum values cannot be removed safely on downgrade; old stage-like values are kept.
    if op.get_bind().dialect.name == "postgresql":
        for value in ["running", "partially_completed", "cancelled"]:
            op.execute(f"ALTER TYPE recognitionstatus ADD VALUE IF NOT EXISTS '{value}'")
    op.add_column("recognition_jobs", sa.Column("assignment_id", sa.Uuid(), nullable=True))
    op.add_column(
        "recognition_jobs",
        sa.Column("stage", sa.String(40), nullable=False, server_default="converting"),
    )
    op.add_column(
        "recognition_jobs", sa.Column("progress", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "recognition_jobs",
        sa.Column("provider", sa.String(80), nullable=False, server_default="unavailable"),
    )
    op.add_column(
        "recognition_jobs",
        sa.Column("provider_version", sa.String(80), nullable=False, server_default="none"),
    )
    op.add_column(
        "recognition_jobs",
        sa.Column("config_version", sa.String(80), nullable=False, server_default="2026-07-22"),
    )
    op.add_column(
        "recognition_jobs", sa.Column("attempt", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("recognition_jobs", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column("recognition_jobs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("recognition_jobs", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("recognition_jobs", sa.Column("failed_at", sa.DateTime(timezone=True)))
    op.add_column("recognition_jobs", sa.Column("error_code", sa.String(80)))
    op.execute(
        "UPDATE recognition_jobs SET assignment_id = "
        "(SELECT assignment_id FROM paper_versions WHERE paper_versions.id = "
        "recognition_jobs.paper_version_id), idempotency_key = CAST(id AS VARCHAR)"
    )
    op.alter_column("recognition_jobs", "assignment_id", nullable=False)
    op.alter_column("recognition_jobs", "idempotency_key", nullable=False)
    op.drop_column("recognition_jobs", "attempts")
    op.create_foreign_key(
        "fk_recognition_job_assignment",
        "recognition_jobs",
        "assignments",
        ["assignment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_recognition_jobs_assignment_id", "recognition_jobs", ["assignment_id"])
    op.create_index("ix_recognition_jobs_stage", "recognition_jobs", ["stage"])
    op.create_index("ix_recognition_jobs_error_code", "recognition_jobs", ["error_code"])
    op.create_unique_constraint(
        "uq_recognition_job_key", "recognition_jobs", ["owner_id", "idempotency_key"]
    )

    op.create_table(
        "page_processing_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "recognition_job_id",
            sa.Uuid(),
            sa.ForeignKey("recognition_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_page_id",
            sa.Uuid(),
            sa.ForeignKey("paper_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("original_storage_key", sa.String(512)),
        sa.Column("rendered_storage_key", sa.String(512)),
        sa.Column("processed_storage_key", sa.String(512)),
        sa.Column("thumbnail_storage_key", sa.String(512)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("detected_rotation", sa.Integer(), nullable=False),
        sa.Column("applied_rotation", sa.Integer(), nullable=False),
        sa.Column("crop_region", sa.JSON()),
        sa.Column("quality_score", sa.Numeric(6, 5)),
        sa.Column("blur_score", sa.Numeric(6, 5)),
        sa.Column("shadow_score", sa.Numeric(6, 5)),
        sa.Column("processing_parameters", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("recognition_job_id", "paper_page_id", name="uq_job_page_result"),
    )
    op.create_index(
        "ix_page_processing_results_recognition_job_id",
        "page_processing_results",
        ["recognition_job_id"],
    )
    op.create_index(
        "ix_page_processing_results_paper_page_id", "page_processing_results", ["paper_page_id"]
    )
    op.create_index("ix_page_processing_results_status", "page_processing_results", ["status"])
    op.create_table(
        "recognition_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "recognition_job_id",
            sa.Uuid(),
            sa.ForeignKey("recognition_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_page_id",
            sa.Uuid(),
            sa.ForeignKey("paper_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_type", sa.String(30), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("latex", sa.Text()),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("language", sa.String(30)),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("crop_storage_key", sa.String(512)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_recognition_blocks_recognition_job_id", "recognition_blocks", ["recognition_job_id"]
    )
    op.create_index("ix_recognition_blocks_paper_page_id", "recognition_blocks", ["paper_page_id"])
    op.create_index(
        "ix_recognition_block_page_order", "recognition_blocks", ["paper_page_id", "display_order"]
    )
    op.create_table(
        "question_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "recognition_job_id",
            sa.Uuid(),
            sa.ForeignKey("recognition_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_version_id",
            sa.Uuid(),
            sa.ForeignKey("paper_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("temporary_number", sa.String(80), nullable=False),
        sa.Column("question_type", sa.String(30), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("content_latex", sa.Text()),
        sa.Column("suggested_score", sa.Numeric(10, 2)),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column(
            "confirmed_question_id", sa.Uuid(), sa.ForeignKey("questions.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "recognition_job_id", "temporary_number", name="uq_job_candidate_number"
        ),
    )
    op.create_index(
        "ix_question_candidates_recognition_job_id", "question_candidates", ["recognition_job_id"]
    )
    op.create_index("ix_question_candidates_status", "question_candidates", ["status"])
    op.create_table(
        "question_candidate_regions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "question_candidate_id",
            sa.Uuid(),
            sa.ForeignKey("question_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_page_id",
            sa.Uuid(),
            sa.ForeignKey("paper_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recognition_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "recognition_job_id",
            sa.Uuid(),
            sa.ForeignKey("recognition_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.String(50), nullable=False),
        sa.Column("original_value", sa.Text()),
        sa.Column("corrected_value", sa.Text()),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in [
        "recognition_corrections",
        "question_candidate_regions",
        "question_candidates",
        "recognition_blocks",
        "page_processing_results",
    ]:
        op.drop_table(table)
    op.drop_constraint("uq_recognition_job_key", "recognition_jobs", type_="unique")
    op.drop_constraint("fk_recognition_job_assignment", "recognition_jobs", type_="foreignkey")
    op.add_column(
        "recognition_jobs", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
    )
    for column in [
        "error_code",
        "failed_at",
        "completed_at",
        "started_at",
        "idempotency_key",
        "attempt",
        "config_version",
        "provider_version",
        "provider",
        "progress",
        "stage",
        "assignment_id",
    ]:
        op.drop_column("recognition_jobs", column)
