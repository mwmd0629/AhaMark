"""Add formula regions and recognition candidates.

Revision ID: 0047_formula_recognition_candidates
Revises: 0046_textbook_libraries
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_formula_recognition_candidates"
down_revision: str | None = "0046_textbook_libraries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "formula_regions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recognition_job_id", sa.Uuid(), nullable=False),
        sa.Column("paper_page_id", sa.Uuid(), nullable=False),
        sa.Column("source_block_id", sa.Uuid()),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("region_kind", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("detection_source", sa.String(80), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("status", sa.String(24), nullable=False, server_default="manual_required"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("display_order > 0", name="ck_formula_region_order_positive"),
        sa.CheckConstraint(
            "region_kind IN ('inline','display','unknown')", name="ck_formula_region_kind"
        ),
        sa.CheckConstraint(
            "x >= 0 AND y >= 0 AND width > 0 AND height > 0 AND x + width <= 1 AND y + height <= 1",
            name="ck_formula_region_coordinates",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_formula_region_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('suggested','manual_required','confirmed','rejected')",
            name="ck_formula_region_status",
        ),
        sa.ForeignKeyConstraint(
            ["recognition_job_id"], ["recognition_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["paper_page_id"], ["paper_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_block_id"], ["recognition_blocks.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "recognition_job_id", "paper_page_id", "display_order", name="uq_formula_region_order"
        ),
    )
    for column in ("recognition_job_id", "paper_page_id", "source_block_id", "status"):
        op.create_index(f"ix_formula_regions_{column}", "formula_regions", [column])

    op.create_table(
        "formula_recognition_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("formula_region_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_rank", sa.Integer(), nullable=False),
        sa.Column("latex", sa.Text(), nullable=False),
        sa.Column("normalized_latex", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("provider_version", sa.String(80), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("warning_codes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="manual_required"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("candidate_rank > 0", name="ck_formula_candidate_rank_positive"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_formula_candidate_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('suggested','manual_required','accepted','rejected')",
            name="ck_formula_candidate_status",
        ),
        sa.ForeignKeyConstraint(["formula_region_id"], ["formula_regions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "formula_region_id", "candidate_rank", name="uq_formula_candidate_region_rank"
        ),
    )
    for column in ("formula_region_id", "status"):
        op.create_index(
            f"ix_formula_recognition_candidates_{column}",
            "formula_recognition_candidates",
            [column],
        )


def downgrade() -> None:
    op.drop_table("formula_recognition_candidates")
    op.drop_table("formula_regions")
