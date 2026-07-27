"""add region-scoped answer recognition evidence

Revision ID: 0013_answer_recognition_evidence
Revises: 0012_submission_page_processing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0013_answer_recognition_evidence"
down_revision: str | None = "0012_submission_page_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_column(table: str, column: sa.Column[object]) -> None:
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
        (item for item in sa.inspect(op.get_bind()).get_indexes(table) if item["name"] == name),
        None,
    )
    if existing is None:
        op.create_index(name, table, columns)
    elif existing["column_names"] != columns or existing["unique"]:
        raise RuntimeError(f"incompatible existing index: {name}")


def _upgrade_online_prefix() -> None:
    _ensure_column(
        "submission_pages",
        sa.Column("page_version", sa.Integer(), nullable=False, server_default="1"),
    )
    _ensure_column(
        "student_answer_regions",
        sa.Column("region_version", sa.Integer(), nullable=False, server_default="1"),
    )

    for column in (
        sa.Column("provider_kind", sa.String(40), nullable=False, server_default="printed_text"),
        sa.Column(
            "config_version", sa.String(80), nullable=False, server_default="answer-evidence-v1"
        ),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("warning_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    ):
        _ensure_column("submission_recognition_jobs", column)
    _ensure_index(
        "submission_recognition_jobs",
        "ix_submission_recognition_jobs_input_hash",
        ["input_hash"],
    )


def _upgrade_offline_prefix() -> None:
    op.add_column(
        "submission_pages",
        sa.Column("page_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "student_answer_regions",
        sa.Column("region_version", sa.Integer(), nullable=False, server_default="1"),
    )
    for column in (
        sa.Column("provider_kind", sa.String(40), nullable=False, server_default="printed_text"),
        sa.Column(
            "config_version", sa.String(80), nullable=False, server_default="answer-evidence-v1"
        ),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("warning_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("submission_recognition_jobs", column)
    op.create_index(
        "ix_submission_recognition_jobs_input_hash",
        "submission_recognition_jobs",
        ["input_hash"],
    )


def upgrade() -> None:
    # Offline SQL assumes the complete schema produced by sequential revision 0012.
    if context.is_offline_mode():
        _upgrade_offline_prefix()
    else:
        _upgrade_online_prefix()

    op.create_table(
        "region_evidence_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_answer_region_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("margin_pixels", sa.Integer(), nullable=False),
        sa.Column("source_page_number", sa.Integer(), nullable=False),
        sa.Column("region_order", sa.Integer(), nullable=False),
        sa.Column("page_version", sa.Integer(), nullable=False),
        sa.Column("region_version", sa.Integer(), nullable=False),
        sa.Column("processing_config_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["submission_page_id"], ["submission_pages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["student_answer_region_id"], ["student_answer_regions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("object_key", name="uq_region_evidence_images_object_key"),
        sa.UniqueConstraint(
            "student_answer_region_id",
            "source_kind",
            "page_version",
            "region_version",
            "processing_config_version",
            name="uq_region_evidence_source_version",
        ),
    )
    for column in (
        "owner_id",
        "submission_id",
        "submission_page_id",
        "student_answer_region_id",
        "content_hash",
        "status",
        "stale_at",
    ):
        op.create_index(f"ix_region_evidence_images_{column}", "region_evidence_images", [column])

    block_columns = (
        sa.Column("student_answer_region_id", postgresql.UUID(as_uuid=True)),
        sa.Column("region_evidence_image_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_page_number", sa.Integer()),
        sa.Column("block_type", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("normalized_text", sa.Text()),
        sa.Column("reading_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("evidence_image_key", sa.String(512)),
        sa.Column("recognition_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    for column in block_columns:
        op.add_column("submission_recognition_blocks", column)
    op.create_foreign_key(
        "fk_submission_recognition_blocks_region",
        "submission_recognition_blocks",
        "student_answer_regions",
        ["student_answer_region_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_submission_recognition_blocks_evidence",
        "submission_recognition_blocks",
        "region_evidence_images",
        ["region_evidence_image_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_submission_recognition_blocks_confirmer",
        "submission_recognition_blocks",
        "users",
        ["confirmed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in (
        "student_answer_region_id",
        "region_evidence_image_id",
        "block_type",
        "requires_review",
        "stale_at",
    ):
        op.create_index(
            f"ix_submission_recognition_blocks_{column}",
            "submission_recognition_blocks",
            [column],
        )

    op.create_table(
        "recognition_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recognition_block_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("raw_text", sa.Text()),
        sa.Column("normalized_text", sa.Text()),
        sa.Column("latex", sa.Text()),
        sa.Column("warning_codes", sa.JSON(), nullable=False),
        sa.Column("editor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("base_recognition_version", sa.Integer(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["recognition_block_id"], ["submission_recognition_blocks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["editor_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("recognition_block_id", "revision", name="uq_recognition_revision"),
    )
    op.create_index(
        "ix_recognition_revisions_recognition_block_id",
        "recognition_revisions",
        ["recognition_block_id"],
    )
    op.create_index("ix_recognition_revisions_source", "recognition_revisions", ["source"])

    op.create_table(
        "question_recognition_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_answer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recognition_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("block_sources", sa.JSON(), nullable=False),
        sa.Column("normalized_text", sa.Text()),
        sa.Column("latex", sa.Text()),
        sa.Column("provider_versions", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("recognition_version", sa.Integer(), nullable=False),
        sa.Column("confirmed_revision", sa.Integer()),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_answer_id"], ["student_answers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["recognition_job_id"], ["submission_recognition_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "student_answer_id", "recognition_version", name="uq_question_evidence_version"
        ),
    )
    for column in (
        "owner_id",
        "submission_id",
        "student_answer_id",
        "recognition_job_id",
        "status",
        "requires_review",
        "stale_at",
    ):
        op.create_index(
            f"ix_question_recognition_evidence_{column}",
            "question_recognition_evidence",
            [column],
        )


def _downgrade_schema() -> None:
    op.drop_table("question_recognition_evidence")
    op.drop_table("recognition_revisions")
    for constraint in (
        "fk_submission_recognition_blocks_confirmer",
        "fk_submission_recognition_blocks_evidence",
        "fk_submission_recognition_blocks_region",
    ):
        op.drop_constraint(constraint, "submission_recognition_blocks", type_="foreignkey")
    for column in (
        "updated_at",
        "confirmed_at",
        "confirmed_by",
        "stale_at",
        "output_hash",
        "input_hash",
        "recognition_version",
        "evidence_image_key",
        "requires_review",
        "warning_codes",
        "reading_order",
        "normalized_text",
        "block_type",
        "source_page_number",
        "region_evidence_image_id",
        "student_answer_region_id",
    ):
        op.drop_column("submission_recognition_blocks", column)
    op.drop_table("region_evidence_images")
    op.drop_index(
        "ix_submission_recognition_jobs_input_hash", table_name="submission_recognition_jobs"
    )
    for column in (
        "cancelled_at",
        "completed_at",
        "started_at",
        "warning_codes",
        "generation",
        "max_attempts",
        "attempt",
        "progress",
        "output_hash",
        "input_hash",
        "config_version",
        "provider_kind",
    ):
        op.drop_column("submission_recognition_jobs", column)
    op.drop_column("student_answer_regions", "region_version")
    op.drop_column("submission_pages", "page_version")


def downgrade() -> None:
    # Both paths are deterministic; the explicit branch documents the offline boundary.
    if context.is_offline_mode():
        _downgrade_schema()
        return
    _downgrade_schema()
