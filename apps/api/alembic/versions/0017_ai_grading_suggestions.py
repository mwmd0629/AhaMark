"""Add immutable AI grading suggestions and teacher disposition audit.

Revision ID: 0017_ai_grading_suggestions
Revises: 0016_math_validation_task_id
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_ai_grading_suggestions"
down_revision: str | None = "0016_math_validation_task_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid()


def upgrade() -> None:
    op.create_table(
        "ai_scoring_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assignment_id", UUID, sa.ForeignKey("assignments.id"), nullable=False),
        sa.Column("question_id", UUID, sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("submission_id", UUID, sa.ForeignKey("submissions.id"), nullable=False),
        sa.Column("student_answer_id", UUID, sa.ForeignKey("student_answers.id"), nullable=False),
        sa.Column(
            "recognition_evidence_id",
            UUID,
            sa.ForeignKey("question_recognition_evidence.id"),
            nullable=False,
        ),
        sa.Column(
            "reference_answer_version_id",
            UUID,
            sa.ForeignKey("reference_answer_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "rubric_version_id",
            UUID,
            sa.ForeignKey("structured_rubric_versions.id"),
            nullable=False,
        ),
        sa.Column("math_validation_job_id", UUID, sa.ForeignKey("math_validation_jobs.id")),
        sa.Column("question_version", sa.String(100), nullable=False),
        sa.Column("scoring_input_version", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160)),
        sa.Column("model_snapshot", sa.String(160)),
        sa.Column("endpoint_mode", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("provider_config_version", sa.String(80), nullable=False),
        sa.Column("grading_config_version", sa.String(80), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64)),
        sa.Column("provider_request_id", sa.String(160)),
        sa.Column("celery_task_id", sa.String(80)),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("image_count", sa.Integer, nullable=False),
        sa.Column("image_bytes", sa.BigInteger, nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 6)),
        sa.Column("retryable", sa.Boolean, nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_ai_scoring_idempotency"),
        sa.UniqueConstraint("student_answer_id", "generation", name="uq_ai_scoring_generation"),
    )
    op.create_index(
        "ix_ai_scoring_jobs_student_answer_id", "ai_scoring_jobs", ["student_answer_id"]
    )
    op.create_index("ix_ai_scoring_jobs_status", "ai_scoring_jobs", ["status"])
    op.create_index("ix_ai_scoring_jobs_stale_at", "ai_scoring_jobs", ["stale_at"])
    op.create_table(
        "ai_criterion_suggestions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "ai_scoring_job_id",
            UUID,
            sa.ForeignKey("ai_scoring_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("criterion_id", UUID, sa.ForeignKey("rubric_criteria.id"), nullable=False),
        sa.Column("criterion_stable_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("suggested_points", sa.Numeric(10, 2)),
        sa.Column("max_points", sa.Numeric(10, 2), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("evidence_refs", sa.JSON, nullable=False),
        sa.Column("matched_steps", sa.JSON, nullable=False),
        sa.Column("missing_steps", sa.JSON, nullable=False),
        sa.Column("detected_errors", sa.JSON, nullable=False),
        sa.Column("reasoning_summary", sa.Text),
        sa.Column("manual_review_reason", sa.Text),
        sa.Column("student_feedback", sa.Text),
        sa.Column("teacher_note", sa.Text),
        sa.Column("abstained", sa.Boolean, nullable=False),
        sa.Column("deterministic_conflict", sa.Boolean, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ai_scoring_job_id", "criterion_id", name="uq_ai_criterion_job"),
    )
    op.create_table(
        "ai_feedback_drafts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "ai_scoring_job_id",
            UUID,
            sa.ForeignKey("ai_scoring_jobs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("student_feedback", sa.Text, nullable=False),
        sa.Column("teacher_summary", sa.Text, nullable=False),
        sa.Column("strengths", sa.JSON, nullable=False),
        sa.Column("improvements", sa.JSON, nullable=False),
        sa.Column("error_categories", sa.JSON, nullable=False),
        sa.Column("risk_flags", sa.JSON, nullable=False),
        sa.Column("suggestion_ids", sa.JSON, nullable=False),
        sa.Column("teacher_disposition", sa.String(30), nullable=False),
        sa.Column("edited_by", UUID, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_provider_invocations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "ai_scoring_job_id",
            UUID,
            sa.ForeignKey("ai_scoring_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("endpoint_mode", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160)),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("provider_request_id", sa.String(160)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64)),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("cache_tokens", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("retry_number", sa.Integer, nullable=False),
        sa.Column("response_status", sa.String(40), nullable=False),
        sa.Column("capability_gaps", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_suggestion_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "suggestion_id", UUID, sa.ForeignKey("ai_criterion_suggestions.id"), nullable=False
        ),
        sa.Column("reviewer_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("original_points", sa.Numeric(10, 2)),
        sa.Column("selected_points", sa.Numeric(10, 2)),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("scoring_input_version", sa.String(160), nullable=False),
        sa.Column(
            "rubric_version_id",
            UUID,
            sa.ForeignKey("structured_rubric_versions.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "ai_suggestion_reviews",
        "ai_provider_invocations",
        "ai_feedback_drafts",
        "ai_criterion_suggestions",
        "ai_scoring_jobs",
    ):
        op.drop_table(table)
