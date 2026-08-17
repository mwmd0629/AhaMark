"""Add audited reference-answer source bindings.

Revision ID: 0041_reference_answer_source_bindings
Revises: 0040_recognition_character_boxes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_reference_answer_source_bindings"
down_revision: str | None = "0040_recognition_character_boxes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reference_answer_source_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("draft_revision_id", sa.Uuid(), nullable=False),
        sa.Column("paper_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_file_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("source_recognition_block_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid()),
        sa.Column("detected_number", sa.String(40), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("warning_codes", sa.JSON(), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.Uuid()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("binding_version > 0", name="ck_reference_binding_version"),
        sa.CheckConstraint("edit_version >= 0", name="ck_reference_binding_edit_version"),
        sa.CheckConstraint(
            "status IN ('suggested','confirmed','rejected','superseded')",
            name="ck_reference_binding_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_reference_binding_confidence",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["draft_revision_id"], ["assignment_draft_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_file_analysis_id"],
            ["assignment_source_file_analyses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_recognition_block_id"], ["recognition_blocks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_revision_id",
            "source_recognition_block_id",
            "binding_version",
            name="uq_reference_binding_revision_block_version",
        ),
    )
    for column in (
        "owner_id",
        "assignment_id",
        "draft_revision_id",
        "paper_version_id",
        "source_file_analysis_id",
        "source_recognition_block_id",
        "question_id",
        "status",
    ):
        op.create_index(
            f"ix_reference_answer_source_bindings_{column}",
            "reference_answer_source_bindings",
            [column],
        )

    op.create_table(
        "reference_answer_source_regions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("paper_page_id", sa.Uuid(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("display_order >= 0", name="ck_reference_source_region_order"),
        sa.CheckConstraint(
            "x >= 0 AND y >= 0 AND width > 0 AND height > 0 AND x + width <= 1 AND y + height <= 1",
            name="ck_reference_source_region_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"], ["reference_answer_source_bindings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["paper_page_id"], ["paper_pages.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", "display_order", name="uq_reference_source_region_order"),
    )
    op.create_index(
        "ix_reference_answer_source_regions_binding_id",
        "reference_answer_source_regions",
        ["binding_id"],
    )
    op.create_index(
        "ix_reference_answer_source_regions_paper_page_id",
        "reference_answer_source_regions",
        ["paper_page_id"],
    )


def downgrade() -> None:
    op.drop_table("reference_answer_source_regions")
    op.drop_table("reference_answer_source_bindings")
