"""Version textbook indexes as question-only candidates.

Revision ID: 0045_textbook_question_only_indexes
Revises: 0044_textbook_content_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_textbook_question_only_indexes"
down_revision: str | None = "0044_textbook_content_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "textbook_content_indexes",
        sa.Column(
            "index_policy",
            sa.String(40),
            nullable=False,
            server_default="legacy-page-windows-v2",
        ),
    )


def downgrade() -> None:
    op.drop_column("textbook_content_indexes", "index_policy")
