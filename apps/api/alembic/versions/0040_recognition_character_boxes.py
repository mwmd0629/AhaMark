"""Persist normalized character coordinates for recognition text blocks.

Revision ID: 0040_recognition_character_boxes
Revises: 0039_pdf_content_sources
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_recognition_character_boxes"
down_revision: str | None = "0039_pdf_content_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("recognition_blocks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "character_boxes",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("recognition_blocks") as batch_op:
        batch_op.drop_column("character_boxes")
