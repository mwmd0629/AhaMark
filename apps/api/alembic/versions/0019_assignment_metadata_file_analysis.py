"""Versioned assignment metadata suggestions and source-file analysis.

Revision ID: 0019_assignment_metadata_file_analysis
Revises: 0018_assignment_generation_orchestration
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_assignment_metadata_file_analysis"
down_revision: str | None = "0018_assignment_generation_orchestration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid()
JSON = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "assignment_field_suggestions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
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
        ),
        sa.Column(
            "draft_revision_id",
            UUID,
            sa.ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(40), nullable=False),
        sa.Column("suggested_value", JSON),
        sa.Column("normalized_value", JSON),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_stage", sa.String(32), nullable=False),
        sa.Column("suggestion_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("teacher_value", JSON),
        sa.Column("teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_revision_id",
            "field_name",
            "suggestion_version",
            name="uq_assignment_field_suggestion_version",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_field_suggestion_confidence"
        ),
        sa.CheckConstraint("suggestion_version > 0", name="ck_field_suggestion_version_positive"),
        sa.CheckConstraint("teacher_edit_version >= 0", name="ck_field_suggestion_teacher_version"),
    )
    for name, cols in (
        ("ix_assignment_field_suggestions_owner_id", ["owner_id"]),
        ("ix_assignment_field_suggestions_assignment_id", ["assignment_id"]),
        ("ix_assignment_field_suggestions_generation_job_id", ["generation_job_id"]),
        ("ix_assignment_field_suggestions_draft_revision_id", ["draft_revision_id"]),
        ("ix_assignment_field_suggestions_status", ["status"]),
        (
            "ix_field_suggestion_revision_field_status",
            ["draft_revision_id", "field_name", "status"],
        ),
    ):
        op.create_index(name, "assignment_field_suggestions", cols)

    op.create_table(
        "assignment_source_file_analyses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
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
        ),
        sa.Column(
            "draft_revision_id",
            UUID,
            sa.ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stored_file_id",
            UUID,
            sa.ForeignKey("stored_files.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("detected_mime_type", sa.String(127), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("suggested_role", sa.String(32), nullable=False),
        sa.Column("role_confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("suggested_answer_source", sa.String(32), nullable=False),
        sa.Column("answer_source_confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column(
            "duplicate_of_file_id", UUID, sa.ForeignKey("stored_files.id", ondelete="SET NULL")
        ),
        sa.Column("analysis_status", sa.String(24), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("warning_codes", JSON, nullable=False),
        sa.Column("teacher_confirmed_role", sa.String(32)),
        sa.Column("teacher_confirmed_answer_source", sa.String(32)),
        sa.Column("teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role_confidence >= 0 AND role_confidence <= 1", name="ck_source_file_role_confidence"
        ),
        sa.CheckConstraint(
            "answer_source_confidence >= 0 AND answer_source_confidence <= 1",
            name="ck_source_file_answer_confidence",
        ),
        sa.CheckConstraint("teacher_edit_version >= 0", name="ck_source_file_teacher_version"),
    )
    for name, cols in (
        ("ix_assignment_source_file_analyses_owner_id", ["owner_id"]),
        ("ix_assignment_source_file_analyses_assignment_id", ["assignment_id"]),
        ("ix_assignment_source_file_analyses_generation_job_id", ["generation_job_id"]),
        ("ix_assignment_source_file_analyses_draft_revision_id", ["draft_revision_id"]),
        ("ix_assignment_source_file_analyses_stored_file_id", ["stored_file_id"]),
        ("ix_assignment_source_file_analyses_checksum", ["checksum"]),
        ("ix_assignment_source_file_analyses_analysis_status", ["analysis_status"]),
        (
            "ix_source_file_analysis_revision_file_status",
            ["draft_revision_id", "stored_file_id", "analysis_status"],
        ),
    ):
        op.create_index(name, "assignment_source_file_analyses", cols)

    op.create_table(
        "assignment_page_analyses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
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
        ),
        sa.Column(
            "draft_revision_id",
            UUID,
            sa.ForeignKey("assignment_draft_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_page_id",
            UUID,
            sa.ForeignKey("paper_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_file_analysis_id",
            UUID,
            sa.ForeignKey("assignment_source_file_analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("quality_score", sa.Numeric(6, 5)),
        sa.Column("blank_probability", sa.Numeric(6, 5)),
        sa.Column("duplicate_probability", sa.Numeric(6, 5)),
        sa.Column(
            "duplicate_of_page_id", UUID, sa.ForeignKey("paper_pages.id", ondelete="SET NULL")
        ),
        sa.Column("missing_page_suspected", sa.Boolean(), nullable=False),
        sa.Column("low_quality", sa.Boolean(), nullable=False),
        sa.Column("corrupted", sa.Boolean(), nullable=False),
        sa.Column("mixed_document_suspected", sa.Boolean(), nullable=False),
        sa.Column("variant_label", sa.String(32)),
        sa.Column("metrics", JSON, nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("warning_codes", JSON, nullable=False),
        sa.Column("teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_file_analysis_id", "paper_page_id", name="uq_source_file_page_analysis"
        ),
        sa.CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
            name="ck_page_analysis_quality",
        ),
        sa.CheckConstraint(
            "blank_probability IS NULL OR (blank_probability >= 0 AND blank_probability <= 1)",
            name="ck_page_analysis_blank_probability",
        ),
        sa.CheckConstraint(
            "duplicate_probability IS NULL OR "
            "(duplicate_probability >= 0 AND duplicate_probability <= 1)",
            name="ck_page_analysis_duplicate_probability",
        ),
        sa.CheckConstraint("teacher_edit_version >= 0", name="ck_page_analysis_teacher_version"),
    )
    for col in (
        "owner_id",
        "assignment_id",
        "generation_job_id",
        "draft_revision_id",
        "paper_page_id",
        "source_file_analysis_id",
        "status",
    ):
        op.create_index(f"ix_assignment_page_analyses_{col}", "assignment_page_analyses", [col])


def downgrade() -> None:
    op.drop_table("assignment_page_analyses")
    op.drop_table("assignment_source_file_analyses")
    op.drop_table("assignment_field_suggestions")
