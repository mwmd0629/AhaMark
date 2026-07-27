"""Page organization and versioned question extraction drafts.

Revision ID: 0020_assignment_question_extraction
Revises: 0019_assignment_metadata_file_analysis
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_assignment_question_extraction"
down_revision: str | None = "0019_assignment_metadata_file_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid()
JSON = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "paper_page_organization_suggestions",
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
            "paper_version_id",
            UUID,
            sa.ForeignKey("paper_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_page_id",
            UUID,
            sa.ForeignKey("paper_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suggestion_version", sa.Integer(), nullable=False),
        sa.Column("suggested_page_number", sa.Integer(), nullable=False),
        sa.Column("suggested_rotation", sa.Integer(), nullable=False),
        sa.Column("suggested_status", sa.String(30), nullable=False),
        sa.Column(
            "duplicate_of_page_id", UUID, sa.ForeignKey("paper_pages.id", ondelete="SET NULL")
        ),
        sa.Column("variant_label", sa.String(32)),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("reason_codes", JSON, nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_revision_id",
            "paper_page_id",
            "suggestion_version",
            name="uq_page_org_suggestion_version",
        ),
        sa.CheckConstraint("suggestion_version > 0", name="ck_page_org_version_positive"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_page_org_confidence"),
        sa.CheckConstraint("teacher_edit_version >= 0", name="ck_page_org_teacher_version"),
        sa.CheckConstraint("suggested_page_number > 0", name="ck_page_org_page_positive"),
        sa.CheckConstraint("suggested_rotation IN (0, 90, 180, 270)", name="ck_page_org_rotation"),
    )
    op.create_table(
        "assignment_question_extraction_candidates",
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
            "paper_version_id",
            UUID,
            sa.ForeignKey("paper_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_recognition_job_id",
            UUID,
            sa.ForeignKey("recognition_jobs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "source_question_candidate_id",
            UUID,
            sa.ForeignKey("question_candidates.id", ondelete="SET NULL"),
        ),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column(
            "parent_candidate_id",
            UUID,
            sa.ForeignKey("assignment_question_extraction_candidates.id", ondelete="SET NULL"),
        ),
        sa.Column("question_number", sa.String(80)),
        sa.Column("question_type", sa.String(30), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("content_latex", sa.Text()),
        sa.Column("max_score", sa.Numeric(10, 2)),
        sa.Column("difficulty", sa.String(20)),
        sa.Column("knowledge_point_suggestions", JSON, nullable=False),
        sa.Column("field_confidences", JSON, nullable=False),
        sa.Column("overall_confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("extraction_method", sa.String(40), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("warning_codes", JSON, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("manual_required", sa.Boolean(), nullable=False),
        sa.Column("teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("teacher_value", JSON),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column(
            "materialized_question_id",
            UUID,
            sa.ForeignKey("questions.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_revision_id",
            "candidate_version",
            "source_question_candidate_id",
            name="uq_question_extraction_revision_version_source",
        ),
        sa.CheckConstraint("candidate_version > 0", name="ck_question_extraction_version_positive"),
        sa.CheckConstraint(
            "overall_confidence >= 0 AND overall_confidence <= 1",
            name="ck_question_extraction_confidence",
        ),
        sa.CheckConstraint(
            "max_score IS NULL OR max_score > 0", name="ck_question_extraction_score_positive"
        ),
        sa.CheckConstraint(
            "teacher_edit_version >= 0", name="ck_question_extraction_teacher_version"
        ),
        sa.CheckConstraint(
            "parent_candidate_id IS NULL OR parent_candidate_id <> id",
            name="ck_question_extraction_not_self_parent",
        ),
    )
    op.create_table(
        "assignment_question_extraction_regions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "candidate_id",
            UUID,
            sa.ForeignKey("assignment_question_extraction_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_page_id",
            UUID,
            sa.ForeignKey("paper_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("region_type", sa.String(30), nullable=False),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("source_block_ids", JSON, nullable=False),
        sa.Column("cross_page_group", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "candidate_id", "display_order", name="uq_question_extraction_region_order"
        ),
        sa.CheckConstraint("display_order >= 0", name="ck_question_extraction_region_order"),
        sa.CheckConstraint(
            "x >= 0 AND y >= 0 AND width > 0 AND height > 0",
            name="ck_question_extraction_region_positive",
        ),
        sa.CheckConstraint(
            "x + width <= 1 AND y + height <= 1", name="ck_question_extraction_region_bounds"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_question_extraction_region_confidence"
        ),
    )
    for name, table, column in (
        ("ix_page_org_owner", "paper_page_organization_suggestions", "owner_id"),
        ("ix_page_org_assignment", "paper_page_organization_suggestions", "assignment_id"),
        ("ix_page_org_job", "paper_page_organization_suggestions", "generation_job_id"),
        ("ix_page_org_revision", "paper_page_organization_suggestions", "draft_revision_id"),
        ("ix_page_org_paper_version", "paper_page_organization_suggestions", "paper_version_id"),
        ("ix_page_org_page", "paper_page_organization_suggestions", "paper_page_id"),
        ("ix_page_org_status", "paper_page_organization_suggestions", "status"),
        ("ix_aqec_owner", "assignment_question_extraction_candidates", "owner_id"),
        ("ix_aqec_assignment", "assignment_question_extraction_candidates", "assignment_id"),
        ("ix_aqec_job", "assignment_question_extraction_candidates", "generation_job_id"),
        ("ix_aqec_revision", "assignment_question_extraction_candidates", "draft_revision_id"),
        ("ix_aqec_paper_version", "assignment_question_extraction_candidates", "paper_version_id"),
        (
            "ix_aqec_recognition",
            "assignment_question_extraction_candidates",
            "source_recognition_job_id",
        ),
        ("ix_aqec_parent", "assignment_question_extraction_candidates", "parent_candidate_id"),
        ("ix_aqec_status", "assignment_question_extraction_candidates", "status"),
        ("ix_aqer_candidate", "assignment_question_extraction_regions", "candidate_id"),
        ("ix_aqer_page", "assignment_question_extraction_regions", "paper_page_id"),
    ):
        op.create_index(name, table, [column])


def downgrade() -> None:
    op.drop_table("assignment_question_extraction_regions")
    op.drop_table("assignment_question_extraction_candidates")
    op.drop_table("paper_page_organization_suggestions")
