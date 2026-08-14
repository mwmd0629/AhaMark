"""Add conservative PDF content-mode and text-source evidence.

Revision ID: 0039_pdf_content_sources
Revises: 0038_question_structure_reviews
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_pdf_content_sources"
down_revision: str | None = "0038_question_structure_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assignment_source_file_analyses") as batch_op:
        batch_op.add_column(
            sa.Column("content_mode", sa.String(16), nullable=False, server_default="unknown")
        )
        batch_op.add_column(
            sa.Column("text_source", sa.String(16), nullable=False, server_default="unavailable")
        )
        batch_op.add_column(
            sa.Column(
                "content_mode_confidence",
                sa.Numeric(6, 5),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.create_check_constraint(
            "ck_source_file_content_mode_confidence",
            "content_mode_confidence >= 0 AND content_mode_confidence <= 1",
        )
    with op.batch_alter_table("assignment_page_analyses") as batch_op:
        batch_op.add_column(
            sa.Column("content_mode", sa.String(16), nullable=False, server_default="unknown")
        )
        batch_op.add_column(
            sa.Column("text_source", sa.String(16), nullable=False, server_default="unavailable")
        )
        batch_op.add_column(
            sa.Column(
                "content_mode_confidence",
                sa.Numeric(6, 5),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("text_character_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_check_constraint(
            "ck_page_analysis_content_mode_confidence",
            "content_mode_confidence >= 0 AND content_mode_confidence <= 1",
        )


def downgrade() -> None:
    with op.batch_alter_table("assignment_page_analyses") as batch_op:
        batch_op.drop_constraint("ck_page_analysis_content_mode_confidence", type_="check")
        batch_op.drop_column("text_character_count")
        batch_op.drop_column("content_mode_confidence")
        batch_op.drop_column("text_source")
        batch_op.drop_column("content_mode")
    with op.batch_alter_table("assignment_source_file_analyses") as batch_op:
        batch_op.drop_constraint("ck_source_file_content_mode_confidence", type_="check")
        batch_op.drop_column("content_mode_confidence")
        batch_op.drop_column("text_source")
        batch_op.drop_column("content_mode")
