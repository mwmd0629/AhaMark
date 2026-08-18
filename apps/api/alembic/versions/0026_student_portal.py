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


def _column(table: str, name: str) -> dict[str, object] | None:
    return next(
        (item for item in sa.inspect(op.get_bind()).get_columns(table) if item["name"] == name),
        None,
    )


def _ensure_column(table: str, column: sa.Column) -> bool:
    """Return true when the caller must add ``column`` in its batch block."""

    existing = _column(table, str(column.name))
    if existing is None:
        return True
    if existing["nullable"] != column.nullable:
        raise RuntimeError(f"incompatible existing column: {table}.{column.name}")
    actual_type = existing["type"]
    if (
        isinstance(column.type, sa.String)
        and getattr(actual_type, "length", None) != column.type.length
    ):
        raise RuntimeError(f"incompatible existing column: {table}.{column.name}")
    if isinstance(column.type, sa.Boolean) and not isinstance(actual_type, sa.Boolean):
        raise RuntimeError(f"incompatible existing column: {table}.{column.name}")
    if isinstance(column.type, sa.Uuid) and op.get_bind().dialect.name != "sqlite":
        if actual_type._type_affinity is not column.type._type_affinity:
            raise RuntimeError(f"incompatible existing column: {table}.{column.name}")
    return False


def _has_foreign_key(
    table: str, columns: list[str], referred_table: str, referred_columns: list[str]
) -> bool:
    return any(
        item["constrained_columns"] == columns
        and item["referred_table"] == referred_table
        and item["referred_columns"] == referred_columns
        for item in sa.inspect(op.get_bind()).get_foreign_keys(table)
    )


def _has_unique(table: str, columns: list[str]) -> bool:
    return any(
        item["column_names"] == columns
        for item in sa.inspect(op.get_bind()).get_unique_constraints(table)
    )


def _has_index(table: str, name: str, columns: list[str]) -> bool:
    existing = next(
        (item for item in sa.inspect(op.get_bind()).get_indexes(table) if item["name"] == name),
        None,
    )
    if existing is None:
        return False
    if existing["column_names"] != columns or existing["unique"]:
        raise RuntimeError(f"incompatible existing index: {name}")
    return True


def _upgrade_portal_columns() -> None:
    must_change_password = sa.Column(
        "must_change_password",
        sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )
    submitted_by = sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True)
    idempotency_key = sa.Column("student_idempotency_key", sa.String(length=128), nullable=True)

    if op.get_context().as_sql:
        with op.batch_alter_table("users") as batch:
            batch.add_column(must_change_password)
        with op.batch_alter_table("submissions") as batch:
            batch.add_column(submitted_by)
            batch.add_column(idempotency_key)
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
        return

    add_password = _ensure_column("users", must_change_password)
    with op.batch_alter_table("users") as batch:
        if add_password:
            batch.add_column(must_change_password)

    add_submitted_by = _ensure_column("submissions", submitted_by)
    add_idempotency = _ensure_column("submissions", idempotency_key)
    has_foreign_key = _has_foreign_key("submissions", ["submitted_by_user_id"], "users", ["id"])
    has_unique = _has_unique("submissions", ["submitted_by_user_id", "student_idempotency_key"])
    has_index = _has_index(
        "submissions", "ix_submissions_submitted_by_user_id", ["submitted_by_user_id"]
    )
    with op.batch_alter_table("submissions") as batch:
        if add_submitted_by:
            batch.add_column(submitted_by)
        if add_idempotency:
            batch.add_column(idempotency_key)
        if not has_foreign_key:
            batch.create_foreign_key(
                "fk_submissions_submitted_by_user_id_users",
                "users",
                ["submitted_by_user_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        if not has_unique:
            batch.create_unique_constraint(
                "uq_student_submission_idempotency",
                ["submitted_by_user_id", "student_idempotency_key"],
            )
        if not has_index:
            batch.create_index("ix_submissions_submitted_by_user_id", ["submitted_by_user_id"])


def _downgrade_portal_columns() -> None:
    if op.get_context().as_sql:
        with op.batch_alter_table("submissions") as batch:
            batch.drop_index("ix_submissions_submitted_by_user_id")
            batch.drop_constraint("uq_student_submission_idempotency", type_="unique")
            batch.drop_constraint("fk_submissions_submitted_by_user_id_users", type_="foreignkey")
            batch.drop_column("student_idempotency_key")
            batch.drop_column("submitted_by_user_id")
        with op.batch_alter_table("users") as batch:
            batch.drop_column("must_change_password")
        return

    inspector = sa.inspect(op.get_bind())
    submission_columns = {item["name"] for item in inspector.get_columns("submissions")}
    index_names = {item["name"] for item in inspector.get_indexes("submissions")}
    unique_name = next(
        (
            item["name"]
            for item in inspector.get_unique_constraints("submissions")
            if item["column_names"] == ["submitted_by_user_id", "student_idempotency_key"]
            and item["name"]
        ),
        None,
    )
    foreign_key_name = next(
        (
            item["name"]
            for item in inspector.get_foreign_keys("submissions")
            if item["constrained_columns"] == ["submitted_by_user_id"] and item["name"]
        ),
        None,
    )
    with op.batch_alter_table("submissions") as batch:
        if "ix_submissions_submitted_by_user_id" in index_names:
            batch.drop_index("ix_submissions_submitted_by_user_id")
        if unique_name:
            batch.drop_constraint(unique_name, type_="unique")
        if foreign_key_name:
            batch.drop_constraint(foreign_key_name, type_="foreignkey")
        if "student_idempotency_key" in submission_columns:
            batch.drop_column("student_idempotency_key")
        if "submitted_by_user_id" in submission_columns:
            batch.drop_column("submitted_by_user_id")

    if _column("users", "must_change_password") is not None:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("must_change_password")


def upgrade() -> None:
    _upgrade_portal_columns()

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
    _downgrade_portal_columns()
