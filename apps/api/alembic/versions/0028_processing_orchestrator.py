"""Add durable processing orchestration and Codex-local work queues.

Revision ID: 0028_processing_orchestrator
Revises: 0027_semantic_projection
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_processing_orchestrator"
down_revision: str | None = "0027_semantic_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
NULLABLE_JSON_DOCUMENT = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "processing_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("grading_batch_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("input_version", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "input_manifest",
            JSON_DOCUMENT,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("submission_count", sa.Integer(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("completed_step_count", sa.Integer(), nullable=False),
        sa.Column("failed_step_count", sa.Integer(), nullable=False),
        sa.Column("pending_codex_count", sa.Integer(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "generation > 0",
            name="ck_processing_run_generation_positive",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_input', 'waiting_codex', "
            "'awaiting_teacher_review', 'partially_failed', 'failed', 'stale', 'cancelled')",
            name="ck_processing_run_status",
        ),
        sa.CheckConstraint(
            "mode IN ('codex_local')",
            name="ck_processing_run_mode",
        ),
        sa.CheckConstraint(
            "submission_count >= 0 AND step_count >= 0 "
            "AND completed_step_count >= 0 AND failed_step_count >= 0 "
            "AND pending_codex_count >= 0",
            name="ck_processing_run_counters_nonnegative",
        ),
        sa.CheckConstraint(
            "completed_step_count + failed_step_count <= step_count",
            name="ck_processing_run_terminal_counters_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_processing_run_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grading_batch_id"],
            ["grading_batches.id"],
            name="fk_processing_run_grading_batch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_processing_runs"),
        sa.UniqueConstraint(
            "grading_batch_id",
            "generation",
            name="uq_processing_run_batch_generation",
        ),
    )
    op.create_index(
        "ix_processing_run_batch_request_hash",
        "processing_runs",
        ["grading_batch_id", "request_hash"],
    )
    op.create_index(
        "ix_processing_run_owner_batch_status",
        "processing_runs",
        ["owner_id", "grading_batch_id", "status"],
    )

    op.create_table(
        "processing_run_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("grading_batch_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "request_payload",
            JSON_DOCUMENT,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column("result_run_id", sa.Uuid(), nullable=False),
        sa.Column("expected_generation", sa.Integer()),
        sa.Column("result_generation", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('continue', 'retry', 'reconcile')",
            name="ck_processing_run_command_operation",
        ),
        sa.CheckConstraint(
            "idempotency_key = trim(idempotency_key) "
            "AND length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_processing_run_command_idempotency_key",
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name="ck_processing_run_command_request_hash",
        ),
        sa.CheckConstraint(
            "expected_generation IS NULL OR expected_generation > 0",
            name="ck_processing_run_command_expected_generation_positive",
        ),
        sa.CheckConstraint(
            "result_generation > 0",
            name="ck_processing_run_command_result_generation_positive",
        ),
        sa.CheckConstraint(
            "(operation = 'continue' AND source_run_id IS NULL "
            "AND expected_generation IS NULL) "
            "OR (operation = 'retry' AND source_run_id IS NOT NULL "
            "AND expected_generation IS NOT NULL) "
            "OR (operation = 'reconcile' AND source_run_id IS NOT NULL "
            "AND expected_generation IS NOT NULL AND source_run_id = result_run_id "
            "AND expected_generation = result_generation)",
            name="ck_processing_run_command_shape",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_processing_run_command_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grading_batch_id"],
            ["grading_batches.id"],
            name="fk_processing_run_command_grading_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["processing_runs.id"],
            name="fk_processing_run_command_source_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_run_id"],
            ["processing_runs.id"],
            name="fk_processing_run_command_result_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_processing_run_commands"),
        sa.UniqueConstraint(
            "owner_id",
            "idempotency_key",
            name="uq_processing_run_command_owner_idempotency",
        ),
    )
    op.create_index(
        "ix_processing_run_command_owner_batch_created",
        "processing_run_commands",
        ["owner_id", "grading_batch_id", "created_at"],
    )
    op.create_index(
        "ix_processing_run_command_source_operation",
        "processing_run_commands",
        ["source_run_id", "operation"],
    )

    op.create_table(
        "processing_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("processing_run_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("student_answer_id", sa.Uuid()),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("input_version", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_token", sa.String(length=128)),
        sa.Column("dispatch_owner", sa.String(length=160)),
        sa.Column("dispatch_lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("recognition_job_id", sa.Uuid()),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "generation > 0",
            name="ck_processing_step_generation_positive",
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts",
            name="ck_processing_step_attempt_bounds",
        ),
        sa.CheckConstraint(
            "kind IN ('recognition', 'codex_suggestion', 'review_readiness')",
            name="ck_processing_step_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched', 'running', 'succeeded', 'blocked_review', "
            "'retryable_failed', 'terminal_failed', 'stale', 'cancelled')",
            name="ck_processing_step_status",
        ),
        sa.CheckConstraint(
            "(dispatch_token IS NULL AND dispatch_owner IS NULL "
            "AND dispatch_lease_expires_at IS NULL) "
            "OR (dispatch_token IS NOT NULL AND dispatch_owner IS NOT NULL "
            "AND dispatch_lease_expires_at IS NOT NULL)",
            name="ck_processing_step_dispatch_lease_complete",
        ),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name="fk_processing_step_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name="fk_processing_step_submission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_answer_id"],
            ["student_answers.id"],
            name="fk_processing_step_student_answer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recognition_job_id"],
            ["submission_recognition_jobs.id"],
            name="fk_processing_step_recognition_job",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_processing_steps"),
        sa.UniqueConstraint(
            "processing_run_id",
            "scope_key",
            "kind",
            "generation",
            name="uq_processing_step_run_scope_kind_generation",
        ),
    )
    op.create_index(
        "ix_processing_step_run_status_available",
        "processing_steps",
        ["processing_run_id", "status", "available_at"],
    )
    op.create_index(
        "ix_processing_step_submission_kind_status",
        "processing_steps",
        ["submission_id", "kind", "status"],
    )

    op.create_table(
        "codex_work_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("processing_step_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("grading_batch_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("student_answer_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("input_version", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_payload", JSON_DOCUMENT, nullable=False),
        sa.Column("response_payload", NULLABLE_JSON_DOCUMENT),
        sa.Column("response_hash", sa.String(length=64)),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("config_version", sa.String(length=80), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token_hash", sa.String(length=64)),
        sa.Column("lease_owner", sa.String(length=160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_lease_token_hash", sa.String(length=64)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("grading_job_id", sa.Uuid()),
        sa.Column("grading_result_id", sa.Uuid()),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "generation > 0",
            name="ck_codex_work_item_generation_positive",
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts",
            name="ck_codex_work_item_attempt_bounds",
        ),
        sa.CheckConstraint(
            "provider = 'codex_local'",
            name="ck_codex_work_item_provider",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'submitted', 'applied', 'retryable_failed', "
            "'terminal_failed', 'stale', 'cancelled')",
            name="ck_codex_work_item_status",
        ),
        sa.CheckConstraint(
            "(status = 'leased' AND lease_token_hash IS NOT NULL "
            "AND length(lease_token_hash) = 64 AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'leased' AND lease_token_hash IS NULL "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_codex_work_item_lease_state",
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name="ck_codex_work_item_request_hash",
        ),
        sa.CheckConstraint(
            "(response_payload IS NULL AND response_hash IS NULL "
            "AND submitted_lease_token_hash IS NULL AND submitted_at IS NULL) "
            "OR (response_payload IS NOT NULL AND response_hash IS NOT NULL "
            "AND length(response_hash) = 64 AND submitted_lease_token_hash IS NOT NULL "
            "AND length(submitted_lease_token_hash) = 64 AND submitted_at IS NOT NULL)",
            name="ck_codex_work_item_submission_audit_complete",
        ),
        sa.CheckConstraint(
            "(status IN ('submitted', 'applied') AND response_payload IS NOT NULL "
            "AND response_hash IS NOT NULL AND submitted_lease_token_hash IS NOT NULL "
            "AND submitted_at IS NOT NULL) "
            "OR (status IN ('queued', 'leased', 'retryable_failed') "
            "AND response_payload IS NULL AND response_hash IS NULL "
            "AND submitted_lease_token_hash IS NULL AND submitted_at IS NULL) "
            "OR (status IN ('terminal_failed', 'stale', 'cancelled') "
            "AND ((response_payload IS NULL AND response_hash IS NULL "
            "AND submitted_lease_token_hash IS NULL AND submitted_at IS NULL) "
            "OR (response_payload IS NOT NULL AND response_hash IS NOT NULL "
            "AND submitted_lease_token_hash IS NOT NULL AND submitted_at IS NOT NULL)))",
            name="ck_codex_work_item_submission_state",
        ),
        sa.CheckConstraint(
            "(grading_job_id IS NULL AND grading_result_id IS NULL) "
            "OR (grading_job_id IS NOT NULL AND grading_result_id IS NOT NULL)",
            name="ck_codex_work_item_applied_refs_complete",
        ),
        sa.CheckConstraint(
            "(status = 'applied' AND response_payload IS NOT NULL AND response_hash IS NOT NULL "
            "AND grading_job_id IS NOT NULL AND grading_result_id IS NOT NULL "
            "AND applied_at IS NOT NULL) "
            "OR (status <> 'applied' AND grading_job_id IS NULL "
            "AND grading_result_id IS NULL AND applied_at IS NULL)",
            name="ck_codex_work_item_applied_state",
        ),
        sa.ForeignKeyConstraint(
            ["processing_step_id"],
            ["processing_steps.id"],
            name="fk_codex_work_item_processing_step",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_codex_work_item_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grading_batch_id"],
            ["grading_batches.id"],
            name="fk_codex_work_item_grading_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name="fk_codex_work_item_submission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_answer_id"],
            ["student_answers.id"],
            name="fk_codex_work_item_student_answer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grading_job_id"],
            ["grading_jobs.id"],
            name="fk_codex_work_item_grading_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grading_result_id"],
            ["grading_results.id"],
            name="fk_codex_work_item_grading_result",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_codex_work_items"),
        sa.UniqueConstraint(
            "processing_step_id",
            name="uq_codex_work_item_processing_step",
        ),
    )
    op.create_index(
        "ix_codex_work_item_owner_batch_status_available",
        "codex_work_items",
        ["owner_id", "grading_batch_id", "status", "available_at"],
    )
    op.create_index(
        "ix_codex_work_item_submission_answer_status",
        "codex_work_items",
        ["submission_id", "student_answer_id", "status"],
    )
    op.create_index(
        "ix_codex_work_item_claim",
        "codex_work_items",
        ["status", "available_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_codex_work_item_claim",
        table_name="codex_work_items",
    )
    op.drop_index(
        "ix_codex_work_item_submission_answer_status",
        table_name="codex_work_items",
    )
    op.drop_index(
        "ix_codex_work_item_owner_batch_status_available",
        table_name="codex_work_items",
    )
    op.drop_table("codex_work_items")

    op.drop_index(
        "ix_processing_step_submission_kind_status",
        table_name="processing_steps",
    )
    op.drop_index(
        "ix_processing_step_run_status_available",
        table_name="processing_steps",
    )
    op.drop_table("processing_steps")

    op.drop_index(
        "ix_processing_run_command_source_operation",
        table_name="processing_run_commands",
    )
    op.drop_index(
        "ix_processing_run_command_owner_batch_created",
        table_name="processing_run_commands",
    )
    op.drop_table("processing_run_commands")

    op.drop_index(
        "ix_processing_run_owner_batch_status",
        table_name="processing_runs",
    )
    op.drop_index(
        "ix_processing_run_batch_request_hash",
        table_name="processing_runs",
    )
    op.drop_table("processing_runs")
