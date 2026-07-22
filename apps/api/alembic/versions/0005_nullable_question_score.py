"""allow unknown scores on draft questions

Revision ID: 0005_nullable_question_score
Revises: 0004_recognition_pipeline
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_nullable_question_score"
down_revision = "0004_recognition_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "questions",
        "max_score",
        existing_type=sa.Numeric(10, 2),
        nullable=True,
    )


def downgrade() -> None:
    # PostgreSQL will reject this change if unknown scores still exist, which is
    # intentionally safer than inventing a value during rollback.
    op.alter_column(
        "questions",
        "max_score",
        existing_type=sa.Numeric(10, 2),
        nullable=False,
    )
