"""Persist Celery task IDs for validation traceability.

Revision ID: 0016_math_validation_task_id
Revises: 0015_structured_rubric_validation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_math_validation_task_id"
down_revision: str | None = "0015_structured_rubric_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "math_validation_jobs",
        sa.Column("celery_task_id", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "ix_math_validation_jobs_celery_task_id",
        "math_validation_jobs",
        ["celery_task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_math_validation_jobs_celery_task_id",
        table_name="math_validation_jobs",
    )
    op.drop_column("math_validation_jobs", "celery_task_id")
