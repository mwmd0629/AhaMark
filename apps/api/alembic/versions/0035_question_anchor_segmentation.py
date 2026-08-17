"""Add durable provenance for question-anchor segmentation.

Revision ID: 0035_question_anchor_segmentation
Revises: 0034_structured_rubric_authority
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_question_anchor_segmentation"
down_revision: str | None = "0034_structured_rubric_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("submission_question_anchors") as batch:
        batch.add_column(
            sa.Column("source_kind", sa.String(length=30), nullable=False, server_default="ocr")
        )
        batch.add_column(
            sa.Column("page_version", sa.Integer(), nullable=False, server_default="1")
        )
    with op.batch_alter_table("student_answer_regions") as batch:
        batch.add_column(sa.Column("source_question_anchor_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_student_answer_region_source_anchor",
            "submission_question_anchors",
            ["source_question_anchor_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_student_answer_regions_source_question_anchor_id",
            ["source_question_anchor_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("student_answer_regions") as batch:
        batch.drop_index("ix_student_answer_regions_source_question_anchor_id")
        batch.drop_constraint("fk_student_answer_region_source_anchor", type_="foreignkey")
        batch.drop_column("source_question_anchor_id")
    with op.batch_alter_table("submission_question_anchors") as batch:
        batch.drop_column("page_version")
        batch.drop_column("source_kind")
