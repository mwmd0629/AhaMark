"""add submission page processing and segmentation workflow

Revision ID: 0012_submission_page_processing
Revises: 0011_answer_region_confirmation
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0012_submission_page_processing"
down_revision: str | None = "0011_answer_region_confirmation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table) if index["name"]
    }


def _foreign_keys(table: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if constraint["name"]
    }


def _ensure_column(table: str, column: sa.Column[Any]) -> None:
    existing = next(
        (
            item
            for item in sa.inspect(op.get_bind()).get_columns(table)
            if item["name"] == column.name
        ),
        None,
    )
    if existing is None:
        op.add_column(table, column)
        return
    actual_type = existing["type"]
    expected_type = column.type
    same_type = actual_type._type_affinity is expected_type._type_affinity
    same_shape = all(
        getattr(actual_type, attribute, None) == getattr(expected_type, attribute, None)
        for attribute in ("length", "precision", "scale")
    )
    if not same_type or not same_shape or existing["nullable"] != column.nullable:
        raise RuntimeError(f"incompatible existing column: {table}.{column.name}")


def _ensure_index(table: str, name: str, columns: list[str]) -> None:
    existing = next(
        (index for index in sa.inspect(op.get_bind()).get_indexes(table) if index["name"] == name),
        None,
    )
    if existing is None:
        op.create_index(name, table, columns, unique=False)
    elif existing["column_names"] != columns or existing["unique"]:
        raise RuntimeError(f"incompatible existing index: {name}")


def _ensure_foreign_key(
    table: str,
    name: str,
    columns: list[str],
    referred_table: str,
    referred_columns: list[str],
    ondelete: str,
) -> None:
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys(table)

    def compatible(constraint: Any) -> bool:
        options = constraint.get("options") or {}
        return (
            constraint["constrained_columns"] == columns
            and constraint["referred_table"] == referred_table
            and constraint["referred_columns"] == referred_columns
            and str(options.get("ondelete", "")).upper() == ondelete.upper()
        )

    named = next((constraint for constraint in foreign_keys if constraint["name"] == name), None)
    if named is not None:
        if not compatible(named):
            raise RuntimeError(f"incompatible existing foreign key: {name}")
        return
    equivalents = [constraint for constraint in foreign_keys if compatible(constraint)]
    conflicting = [
        constraint
        for constraint in foreign_keys
        if constraint["constrained_columns"] == columns and not compatible(constraint)
    ]
    if conflicting or len(equivalents) > 1:
        raise RuntimeError(f"ambiguous existing foreign key for {table}.{columns}")
    if equivalents:
        old_name = equivalents[0]["name"]
        if not isinstance(old_name, str):
            raise RuntimeError(f"unnamed existing foreign key for {table}.{columns}")
        op.execute(sa.text(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old_name}" TO "{name}"'))
        return
    op.create_foreign_key(
        name,
        table,
        referred_table,
        columns,
        referred_columns,
        ondelete=ondelete,
    )


def _ensure_unique(table: str, name: str, columns: list[str]) -> None:
    constraints = sa.inspect(op.get_bind()).get_unique_constraints(table)
    named = next((constraint for constraint in constraints if constraint["name"] == name), None)
    if named is None:
        same_columns = [
            constraint for constraint in constraints if constraint["column_names"] == columns
        ]
        if same_columns:
            raise RuntimeError(f"unstable existing unique constraint name on {table}: {columns}")
        op.create_unique_constraint(name, table, columns)
    elif named["column_names"] != columns:
        raise RuntimeError(f"incompatible existing unique constraint: {name}")


def _ensure_primary_key(table: str, columns: list[str]) -> None:
    primary_key = sa.inspect(op.get_bind()).get_pk_constraint(table)
    if primary_key["constrained_columns"] != columns:
        raise RuntimeError(f"incompatible existing primary key on {table}")


def _upgrade_offline() -> None:
    """Emit deterministic DDL for the complete schema produced by revision 0011."""
    page_columns: list[sa.Column[Any]] = [
        sa.Column("processing_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("blur_score", sa.Numeric(10, 5)),
        sa.Column("brightness", sa.Numeric(10, 5)),
        sa.Column("contrast", sa.Numeric(10, 5)),
        sa.Column("blank_probability", sa.Numeric(6, 5)),
        sa.Column("duplicate_of_page_id", postgresql.UUID(as_uuid=True)),
        sa.Column("orientation_confidence", sa.Numeric(6, 5)),
        sa.Column("preprocessing_version", sa.String(80)),
        sa.Column("quality_warnings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("processing_error_code", sa.String(80)),
        sa.Column("processing_error_message", sa.Text()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("perceptual_hash", sa.String(64)),
        sa.Column("aligned_paper_page_id", postgresql.UUID(as_uuid=True)),
        sa.Column("alignment_transform", sa.JSON()),
        sa.Column("alignment_confidence", sa.Numeric(6, 5)),
        sa.Column("alignment_failure_reason", sa.String(160)),
    ]
    for column_definition in page_columns:
        op.add_column("submission_pages", column_definition)
    op.create_foreign_key(
        "fk_submission_pages_duplicate",
        "submission_pages",
        "submission_pages",
        ["duplicate_of_page_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_submission_pages_aligned_paper_page",
        "submission_pages",
        "paper_pages",
        ["aligned_paper_page_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in (
        "processing_status",
        "processing_error_code",
        "perceptual_hash",
        "aligned_paper_page_id",
    ):
        op.create_index(f"ix_submission_pages_{column}", "submission_pages", [column])

    op.add_column("student_answer_regions", sa.Column("reason", sa.String(255)))
    op.add_column(
        "student_answer_regions",
        sa.Column(
            "segmentation_version",
            sa.String(80),
            nullable=False,
            server_default="submission-seg-v1",
        ),
    )

    op.create_table(
        "submission_processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("provider_version", sa.String(80), nullable=False),
        sa.Column("config_version", sa.String(80), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="submission_processing_jobs_owner_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name="submission_processing_jobs_submission_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_submission_processing_key"),
    )
    for column in ("owner_id", "submission_id", "status", "stage", "error_code"):
        op.create_index(
            f"ix_submission_processing_jobs_{column}",
            "submission_processing_jobs",
            [column],
        )

    op.create_table(
        "submission_question_anchors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_processing_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(120), nullable=False),
        sa.Column("normalized_number", sa.String(80)),
        sa.Column("candidate_question_id", postgresql.UUID(as_uuid=True)),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("rejection_reason", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_processing_job_id"],
            ["submission_processing_jobs.id"],
            name="submission_question_anchors_submission_processing_job_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submission_page_id"],
            ["submission_pages.id"],
            name="submission_question_anchors_submission_page_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_question_id"],
            ["questions.id"],
            name="submission_question_anchors_candidate_question_id_fkey",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "submission_processing_job_id",
            "submission_page_id",
            "block_index",
            name="uq_submission_anchor_block",
        ),
    )
    for column in (
        "submission_processing_job_id",
        "submission_page_id",
        "normalized_number",
        "candidate_question_id",
    ):
        op.create_index(
            f"ix_submission_question_anchors_{column}",
            "submission_question_anchors",
            [column],
        )


def _downgrade_offline() -> None:
    op.drop_table("submission_question_anchors")
    op.drop_table("submission_processing_jobs")
    for column in ("segmentation_version", "reason"):
        op.drop_column("student_answer_regions", column)
    for constraint in (
        "fk_submission_pages_aligned_paper_page",
        "fk_submission_pages_duplicate",
    ):
        op.drop_constraint(constraint, "submission_pages", type_="foreignkey")
    for column in (
        "aligned_paper_page_id",
        "perceptual_hash",
        "processing_error_code",
        "processing_status",
    ):
        op.drop_index(f"ix_submission_pages_{column}", table_name="submission_pages")
    for column in (
        "alignment_failure_reason",
        "alignment_confidence",
        "alignment_transform",
        "aligned_paper_page_id",
        "perceptual_hash",
        "retryable",
        "processing_error_message",
        "processing_error_code",
        "quality_warnings",
        "preprocessing_version",
        "orientation_confidence",
        "duplicate_of_page_id",
        "blank_probability",
        "contrast",
        "brightness",
        "blur_score",
        "processing_status",
    ):
        op.drop_column("submission_pages", column)


def _upgrade_online() -> None:
    page_columns: list[sa.Column[Any]] = [
        sa.Column("processing_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("blur_score", sa.Numeric(10, 5)),
        sa.Column("brightness", sa.Numeric(10, 5)),
        sa.Column("contrast", sa.Numeric(10, 5)),
        sa.Column("blank_probability", sa.Numeric(6, 5)),
        sa.Column("duplicate_of_page_id", postgresql.UUID(as_uuid=True)),
        sa.Column("orientation_confidence", sa.Numeric(6, 5)),
        sa.Column("preprocessing_version", sa.String(80)),
        sa.Column("quality_warnings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("processing_error_code", sa.String(80)),
        sa.Column("processing_error_message", sa.Text()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("perceptual_hash", sa.String(64)),
        sa.Column("aligned_paper_page_id", postgresql.UUID(as_uuid=True)),
        sa.Column("alignment_transform", sa.JSON()),
        sa.Column("alignment_confidence", sa.Numeric(6, 5)),
        sa.Column("alignment_failure_reason", sa.String(160)),
    ]
    for column_definition in page_columns:
        _ensure_column("submission_pages", column_definition)
    op.alter_column("submission_pages", "processing_status", server_default="pending")
    op.alter_column("submission_pages", "quality_warnings", server_default="[]")
    op.alter_column("submission_pages", "retryable", server_default=sa.true())
    _ensure_foreign_key(
        "submission_pages",
        "fk_submission_pages_duplicate",
        ["duplicate_of_page_id"],
        "submission_pages",
        ["id"],
        "SET NULL",
    )
    _ensure_foreign_key(
        "submission_pages",
        "fk_submission_pages_aligned_paper_page",
        ["aligned_paper_page_id"],
        "paper_pages",
        ["id"],
        "SET NULL",
    )
    for column in (
        "processing_status",
        "processing_error_code",
        "perceptual_hash",
        "aligned_paper_page_id",
    ):
        _ensure_index("submission_pages", f"ix_submission_pages_{column}", [column])

    _ensure_column("student_answer_regions", sa.Column("reason", sa.String(255)))
    _ensure_column(
        "student_answer_regions",
        sa.Column(
            "segmentation_version",
            sa.String(80),
            nullable=False,
            server_default="submission-seg-v1",
        ),
    )
    op.alter_column(
        "student_answer_regions",
        "segmentation_version",
        server_default="submission-seg-v1",
    )

    if "submission_processing_jobs" not in _tables():
        op.create_table(
            "submission_processing_jobs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("stage", sa.String(40), nullable=False),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("idempotency_key", sa.String(128), nullable=False),
            sa.Column("provider", sa.String(80), nullable=False),
            sa.Column("provider_version", sa.String(80), nullable=False),
            sa.Column("config_version", sa.String(80), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("error_code", sa.String(80)),
            sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["owner_id"],
                ["users.id"],
                name="submission_processing_jobs_owner_id_fkey",
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["submission_id"],
                ["submissions.id"],
                name="submission_processing_jobs_submission_id_fkey",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_submission_processing_key"),
        )
    processing_columns: list[sa.Column[Any]] = [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("provider_version", sa.String(80), nullable=False),
        sa.Column("config_version", sa.String(80), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]
    for column_definition in processing_columns:
        _ensure_column("submission_processing_jobs", column_definition)
    _ensure_primary_key("submission_processing_jobs", ["id"])
    _ensure_unique(
        "submission_processing_jobs",
        "uq_submission_processing_key",
        ["owner_id", "idempotency_key"],
    )
    _ensure_foreign_key(
        "submission_processing_jobs",
        "submission_processing_jobs_owner_id_fkey",
        ["owner_id"],
        "users",
        ["id"],
        "RESTRICT",
    )
    _ensure_foreign_key(
        "submission_processing_jobs",
        "submission_processing_jobs_submission_id_fkey",
        ["submission_id"],
        "submissions",
        ["id"],
        "CASCADE",
    )
    for column in ("owner_id", "submission_id", "status", "stage", "error_code"):
        _ensure_index(
            "submission_processing_jobs",
            f"ix_submission_processing_jobs_{column}",
            [column],
        )

    if "submission_question_anchors" not in _tables():
        op.create_table(
            "submission_question_anchors",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "submission_processing_job_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column("submission_page_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("block_index", sa.Integer(), nullable=False),
            sa.Column("text", sa.String(120), nullable=False),
            sa.Column("normalized_number", sa.String(80)),
            sa.Column("candidate_question_id", postgresql.UUID(as_uuid=True)),
            sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
            sa.Column("x", sa.Numeric(8, 6), nullable=False),
            sa.Column("y", sa.Numeric(8, 6), nullable=False),
            sa.Column("width", sa.Numeric(8, 6), nullable=False),
            sa.Column("height", sa.Numeric(8, 6), nullable=False),
            sa.Column("rejection_reason", sa.String(160)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["submission_processing_job_id"],
                ["submission_processing_jobs.id"],
                name="submission_question_anchors_submission_processing_job_id_fkey",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["submission_page_id"],
                ["submission_pages.id"],
                name="submission_question_anchors_submission_page_id_fkey",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["candidate_question_id"],
                ["questions.id"],
                name="submission_question_anchors_candidate_question_id_fkey",
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "submission_processing_job_id",
                "submission_page_id",
                "block_index",
                name="uq_submission_anchor_block",
            ),
        )
    anchor_columns: list[sa.Column[Any]] = [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_processing_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(120), nullable=False),
        sa.Column("normalized_number", sa.String(80)),
        sa.Column("candidate_question_id", postgresql.UUID(as_uuid=True)),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("rejection_reason", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]
    for column_definition in anchor_columns:
        _ensure_column("submission_question_anchors", column_definition)
    _ensure_primary_key("submission_question_anchors", ["id"])
    _ensure_unique(
        "submission_question_anchors",
        "uq_submission_anchor_block",
        ["submission_processing_job_id", "submission_page_id", "block_index"],
    )
    _ensure_foreign_key(
        "submission_question_anchors",
        "submission_question_anchors_submission_processing_job_id_fkey",
        ["submission_processing_job_id"],
        "submission_processing_jobs",
        ["id"],
        "CASCADE",
    )
    _ensure_foreign_key(
        "submission_question_anchors",
        "submission_question_anchors_submission_page_id_fkey",
        ["submission_page_id"],
        "submission_pages",
        ["id"],
        "CASCADE",
    )
    _ensure_foreign_key(
        "submission_question_anchors",
        "submission_question_anchors_candidate_question_id_fkey",
        ["candidate_question_id"],
        "questions",
        ["id"],
        "SET NULL",
    )
    for column in (
        "submission_processing_job_id",
        "submission_page_id",
        "normalized_number",
        "candidate_question_id",
    ):
        _ensure_index(
            "submission_question_anchors",
            f"ix_submission_question_anchors_{column}",
            [column],
        )


def _downgrade_online() -> None:
    if "submission_question_anchors" in _tables():
        op.drop_table("submission_question_anchors")
    if "submission_processing_jobs" in _tables():
        op.drop_table("submission_processing_jobs")
    for column in ("segmentation_version", "reason"):
        if column in _columns("student_answer_regions"):
            op.drop_column("student_answer_regions", column)
    for constraint in (
        "fk_submission_pages_aligned_paper_page",
        "fk_submission_pages_duplicate",
    ):
        if constraint in _foreign_keys("submission_pages"):
            op.drop_constraint(constraint, "submission_pages", type_="foreignkey")
    for column in (
        "alignment_failure_reason",
        "alignment_confidence",
        "alignment_transform",
        "aligned_paper_page_id",
        "perceptual_hash",
        "retryable",
        "processing_error_message",
        "processing_error_code",
        "quality_warnings",
        "preprocessing_version",
        "orientation_confidence",
        "duplicate_of_page_id",
        "blank_probability",
        "contrast",
        "brightness",
        "blur_score",
        "processing_status",
    ):
        if column in _columns("submission_pages"):
            op.drop_column("submission_pages", column)


def upgrade() -> None:
    # Offline SQL assumes a complete sequential migration from revision 0011.
    if context.is_offline_mode():
        _upgrade_offline()
        return
    _upgrade_online()


def downgrade() -> None:
    if context.is_offline_mode():
        _downgrade_offline()
        return
    _downgrade_online()
