"""structured rubric and deterministic validation evidence

Revision ID: 0015_structured_rubric_validation
Revises: 0014_recognition_scoring_input_version
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_structured_rubric_validation"
down_revision: str | None = "0014_recognition_scoring_input_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.Uuid()
JSON = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "reference_answer_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_file", sa.String(512)),
        sa.Column("source_page", sa.Integer()),
        sa.Column("source_region", JSON, nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("structured_content", JSON, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provenance", JSON, nullable=False),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("teacher_confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("question_id", "version", name="uq_reference_answer_question_version"),
    )
    op.create_index(
        "ix_reference_answer_versions_question_id", "reference_answer_versions", ["question_id"]
    )
    op.create_table(
        "structured_rubric_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("question_version", sa.String(100), nullable=False),
        sa.Column(
            "reference_answer_version_id",
            UUID,
            sa.ForeignKey("reference_answer_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("rubric_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("total_points", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("question_id", "rubric_version", name="uq_structured_rubric_version"),
    )
    op.create_table(
        "rubric_criteria",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "rubric_version_id",
            UUID,
            sa.ForeignKey("structured_rubric_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stable_key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("max_points", sa.Numeric(10, 2), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("criterion_type", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("dependencies", JSON, nullable=False),
        sa.Column("expected_evidence", JSON, nullable=False),
        sa.Column("validation_mode", sa.String(24), nullable=False),
        sa.Column("manual_review_policy", JSON, nullable=False),
        sa.Column("partial_credit_policy", JSON, nullable=False),
        sa.Column("error_category", sa.String(80)),
        sa.Column("validation_rule", JSON, nullable=False),
        sa.Column("metadata", JSON, nullable=False),
        sa.UniqueConstraint("rubric_version_id", "stable_key", name="uq_rubric_criterion_key"),
        sa.UniqueConstraint("rubric_version_id", "display_order", name="uq_rubric_criterion_order"),
    )
    op.create_table(
        "math_validation_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "submission_id",
            UUID,
            sa.ForeignKey("submissions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "student_answer_id",
            UUID,
            sa.ForeignKey("student_answers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "recognition_evidence_id",
            UUID,
            sa.ForeignKey("question_recognition_evidence.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scoring_input_version", sa.String(160), nullable=False),
        sa.Column(
            "reference_answer_version_id",
            UUID,
            sa.ForeignKey("reference_answer_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "rubric_version_id",
            UUID,
            sa.ForeignKey("structured_rubric_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("engine", sa.String(80), nullable=False),
        sa.Column("engine_version", sa.String(80), nullable=False),
        sa.Column("config_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_math_validation_idempotency"),
    )
    op.create_table(
        "criterion_validation_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "validation_job_id",
            UUID,
            sa.ForeignKey("math_validation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "criterion_id",
            UUID,
            sa.ForeignKey("rubric_criteria.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("suggested_points", sa.Numeric(10, 2)),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("normalized_student_input", JSON, nullable=False),
        sa.Column("normalized_expected_input", JSON, nullable=False),
        sa.Column("assumptions", JSON, nullable=False),
        sa.Column("comparison_method", sa.String(80), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("diagnostics", JSON, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("engine_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "validation_job_id", "criterion_id", "generation", name="uq_criterion_validation_run"
        ),
    )


def downgrade() -> None:
    op.drop_table("criterion_validation_results")
    op.drop_table("math_validation_jobs")
    op.drop_table("rubric_criteria")
    op.drop_table("structured_rubric_versions")
    op.drop_table("reference_answer_versions")
