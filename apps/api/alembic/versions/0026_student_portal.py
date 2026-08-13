"""student portal, resources, wrong-question review, and learning analysis

Revision ID: 0026_student_portal
Revises: 0025_ai_grading_audit_contract
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_student_portal"
down_revision: str | None = "0025_ai_grading_audit_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("submissions") as batch:
        batch.add_column(sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("student_idempotency_key", sa.String(length=128), nullable=True))
        batch.create_foreign_key(
            "fk_submissions_submitted_by_user_id_users",
            "users",
            ["submitted_by_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_student_submission_idempotency",
            ["submitted_by_user_id", "student_idempotency_key"],
        )
        batch.create_index("ix_submissions_submitted_by_user_id", ["submitted_by_user_id"])

    op.create_table(
        "student_account_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("linked_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["linked_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", name="uq_student_account_link_user"),
        sa.UniqueConstraint("student_id", name="uq_student_account_link_student"),
    )
    op.create_index("ix_student_account_links_user_id", "student_account_links", ["user_id"])
    op.create_index("ix_student_account_links_student_id", "student_account_links", ["student_id"])
    op.create_index("ix_student_account_links_linked_by", "student_account_links", ["linked_by"])
    op.create_index("ix_student_account_links_status", "student_account_links", ["status"])

    op.create_table(
        "teaching_resources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=True),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stored_file_id", sa.Uuid(), nullable=True),
        sa.Column("external_url", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "(stored_file_id IS NOT NULL) <> (external_url IS NOT NULL)",
            name="ck_teaching_resource_target",
        ),
        sa.UniqueConstraint("stored_file_id", name="uq_teaching_resource_stored_file"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["stored_file_id"], ["stored_files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
    )
    for column in (
        "owner_id",
        "class_id",
        "assignment_id",
        "resource_type",
        "title",
        "stored_file_id",
        "status",
        "published_at",
    ):
        op.create_index(f"ix_teaching_resources_{column}", "teaching_resources", [column])
    op.create_index(
        "ix_teaching_resources_class_status_sort_published",
        "teaching_resources",
        ["class_id", "status", "sort_order", "published_at"],
    )

    op.create_table(
        "wrong_question_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("student_answer_id", sa.Uuid(), nullable=False),
        sa.Column("score_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_answer_id"], ["student_answers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["score_snapshot_id"], ["submission_score_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "user_id", "student_answer_id", "score_snapshot_id", name="uq_wrong_question_thread"
        ),
    )
    for column in ("user_id", "student_id", "student_answer_id", "score_snapshot_id", "status"):
        op.create_index(f"ix_wrong_question_threads_{column}", "wrong_question_threads", [column])

    op.create_table(
        "wrong_question_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_payload", JSON_DOCUMENT, nullable=False),
        sa.Column("provider_request_id", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["wrong_question_threads.id"], ondelete="CASCADE"),
    )
    for column in ("thread_id", "role", "provider_request_id", "created_at"):
        op.create_index(f"ix_wrong_question_messages_{column}", "wrong_question_messages", [column])
    op.create_index(
        "ix_wrong_question_messages_thread_created",
        "wrong_question_messages",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "wrong_question_ai_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("user_message_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("provider_request_id", sa.String(length=160), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["thread_id"], ["wrong_question_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_message_id"], ["wrong_question_messages.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("thread_id", "generation", name="uq_wrong_question_ai_generation"),
    )
    for column in ("thread_id", "user_message_id", "status", "provider_request_id", "error_code"):
        op.create_index(f"ix_wrong_question_ai_jobs_{column}", "wrong_question_ai_jobs", [column])
    op.create_index(
        "ix_wrong_question_ai_jobs_thread_created",
        "wrong_question_ai_jobs",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "ix_wrong_question_ai_jobs_thread_status",
        "wrong_question_ai_jobs",
        ["thread_id", "status"],
    )

    op.create_table(
        "student_teacher_review_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("student_answer_id", sa.Uuid(), nullable=False),
        sa.Column("score_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("student_question", sa.Text(), nullable=False),
        sa.Column("conversation_summary", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(length=40), nullable=True),
        sa.Column("teacher_response", sa.Text(), nullable=True),
        sa.Column("score_revision_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["thread_id"], ["wrong_question_threads.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_answer_id"], ["student_answers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["score_snapshot_id"], ["submission_score_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["score_revision_id"], ["score_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("thread_id", name="uq_student_review_request_thread"),
    )
    for column in (
        "thread_id",
        "requester_user_id",
        "student_id",
        "teacher_id",
        "student_answer_id",
        "score_snapshot_id",
        "status",
        "decision",
        "score_revision_id",
    ):
        op.create_index(
            f"ix_student_teacher_review_requests_{column}",
            "student_teacher_review_requests",
            [column],
        )
    op.create_index(
        "ix_student_review_teacher_status_submitted",
        "student_teacher_review_requests",
        ["teacher_id", "status", "submitted_at"],
    )

    op.create_table(
        "student_learning_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_grade_release_ids", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("provider_request_id", sa.String(length=160), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("content", JSON_DOCUMENT, nullable=False),
        sa.Column("evidence", JSON_DOCUMENT, nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("student_id", "source_hash", name="uq_student_analysis_source"),
    )
    for column in (
        "user_id",
        "student_id",
        "status",
        "source_hash",
        "provider_request_id",
        "error_code",
        "generated_at",
    ):
        op.create_index(
            f"ix_student_learning_analyses_{column}", "student_learning_analyses", [column]
        )
    op.create_index(
        "ix_student_analysis_user_created",
        "student_learning_analyses",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_student_analysis_user_status_generated",
        "student_learning_analyses",
        ["user_id", "status", "generated_at"],
    )


def downgrade() -> None:
    for name in (
        "student_learning_analyses",
        "student_teacher_review_requests",
        "wrong_question_ai_jobs",
        "wrong_question_messages",
        "wrong_question_threads",
        "teaching_resources",
        "student_account_links",
    ):
        op.drop_table(name)
    with op.batch_alter_table("submissions") as batch:
        batch.drop_index("ix_submissions_submitted_by_user_id")
        batch.drop_constraint("uq_student_submission_idempotency", type_="unique")
        batch.drop_constraint("fk_submissions_submitted_by_user_id_users", type_="foreignkey")
        batch.drop_column("student_idempotency_key")
        batch.drop_column("submitted_by_user_id")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("must_change_password")
