"""Versioned answer and structured-rubric generation candidates.

Revision ID: 0021_assignment_answer_rubric_generation
Revises: 0020_assignment_question_extraction
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_assignment_answer_rubric_generation"
down_revision: str | None = "0020_assignment_question_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid()
JSON = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "assignment_answer_draft_candidates",
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
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("question_version", sa.String(160), nullable=False),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column(
            "source_file_analysis_id",
            UUID,
            sa.ForeignKey("assignment_source_file_analyses.id", ondelete="SET NULL"),
        ),
        sa.Column("source_page_id", UUID, sa.ForeignKey("paper_pages.id", ondelete="SET NULL")),
        sa.Column("source_region", JSON, nullable=False),
        sa.Column("raw_content", sa.Text()),
        sa.Column("normalized_content", sa.Text()),
        sa.Column("structured_content", JSON, nullable=False),
        sa.Column("alternative_answers", JSON, nullable=False),
        sa.Column("provenance", JSON, nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("warning_codes", JSON, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("manual_required", sa.Boolean(), nullable=False),
        sa.Column("teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("teacher_value", JSON),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column(
            "materialized_reference_answer_id",
            UUID,
            sa.ForeignKey("reference_answer_versions.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_revision_id",
            "question_id",
            "candidate_version",
            name="uq_answer_candidate_revision_question_version",
        ),
        sa.CheckConstraint("candidate_version > 0", name="ck_answer_candidate_version_positive"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_answer_candidate_confidence"
        ),
        sa.CheckConstraint("teacher_edit_version >= 0", name="ck_answer_candidate_teacher_version"),
    )
    op.create_table(
        "assignment_rubric_draft_candidates",
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
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("question_version", sa.String(160), nullable=False),
        sa.Column(
            "answer_candidate_id",
            UUID,
            sa.ForeignKey("assignment_answer_draft_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("scoring_mode", sa.String(24), nullable=False),
        sa.Column("total_points", sa.Numeric(10, 2)),
        sa.Column("allow_partial_credit", sa.Boolean(), nullable=False),
        sa.Column("domain_requirements", JSON, nullable=False),
        sa.Column("validation_config", JSON, nullable=False),
        sa.Column("common_error_types", JSON, nullable=False),
        sa.Column("feedback_templates", JSON, nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("warning_codes", JSON, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("manual_required", sa.Boolean(), nullable=False),
        sa.Column("teacher_edit_version", sa.Integer(), nullable=False),
        sa.Column("teacher_value", JSON),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column(
            "materialized_structured_rubric_id",
            UUID,
            sa.ForeignKey("structured_rubric_versions.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_revision_id",
            "question_id",
            "candidate_version",
            name="uq_rubric_candidate_revision_question_version",
        ),
        sa.CheckConstraint("candidate_version > 0", name="ck_rubric_candidate_version_positive"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_rubric_candidate_confidence"
        ),
        sa.CheckConstraint(
            "total_points IS NULL OR total_points > 0", name="ck_rubric_candidate_points_positive"
        ),
        sa.CheckConstraint("teacher_edit_version >= 0", name="ck_rubric_candidate_teacher_version"),
    )
    op.create_table(
        "assignment_rubric_criterion_drafts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "rubric_candidate_id",
            UUID,
            sa.ForeignKey("assignment_rubric_draft_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("criterion_key", sa.String(80), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("points", sa.Numeric(10, 2)),
        sa.Column("criterion_type", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("dependency_keys", JSON, nullable=False),
        sa.Column("alternative_group", sa.String(80)),
        sa.Column("partial_credit_rule", JSON, nullable=False),
        sa.Column("deduction_rule", JSON, nullable=False),
        sa.Column("validation_rule", JSON, nullable=False),
        sa.Column("common_error_codes", JSON, nullable=False),
        sa.Column("feedback_template", sa.Text()),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("manual_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "rubric_candidate_id", "criterion_key", name="uq_rubric_draft_criterion_key"
        ),
        sa.UniqueConstraint(
            "rubric_candidate_id", "display_order", name="uq_rubric_draft_criterion_order"
        ),
        sa.CheckConstraint("display_order >= 0", name="ck_rubric_draft_order_nonnegative"),
        sa.CheckConstraint(
            "points IS NULL OR points >= 0", name="ck_rubric_draft_points_nonnegative"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_rubric_draft_confidence"
        ),
    )
    op.create_table(
        "assignment_rubric_validation_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "rubric_candidate_id",
            UUID,
            sa.ForeignKey("assignment_rubric_draft_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "answer_candidate_id",
            UUID,
            sa.ForeignKey("assignment_answer_draft_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("validation_mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("deterministic_result", JSON, nullable=False),
        sa.Column("structural_result", JSON, nullable=False),
        sa.Column("issue_codes", JSON, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("validator_version", sa.String(80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table, prefix in (
        ("assignment_answer_draft_candidates", "ix_aadc"),
        ("assignment_rubric_draft_candidates", "ix_ardc"),
    ):
        for column in (
            "owner_id",
            "assignment_id",
            "generation_job_id",
            "draft_revision_id",
            "question_id",
            "status",
        ):
            op.create_index(f"{prefix}_{column}", table, [column])
    op.create_index(
        "ix_arcd_rubric_candidate", "assignment_rubric_criterion_drafts", ["rubric_candidate_id"]
    )
    op.create_index(
        "ix_arvr_rubric_candidate", "assignment_rubric_validation_results", ["rubric_candidate_id"]
    )
    op.create_index("ix_arvr_question", "assignment_rubric_validation_results", ["question_id"])


def downgrade() -> None:
    op.drop_table("assignment_rubric_validation_results")
    op.drop_table("assignment_rubric_criterion_drafts")
    op.drop_table("assignment_rubric_draft_candidates")
    op.drop_table("assignment_answer_draft_candidates")
