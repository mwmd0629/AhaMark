"""Allow publication readiness snapshots without a deadline.

Revision ID: 0024_nullable_publish_readiness_due_at
Revises: 0023_assignment_provider_invocation_audit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_nullable_publish_readiness_due_at"
down_revision: str | None = "0023_assignment_provider_invocation_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assignment_publish_readiness_snapshots") as batch:
        batch.alter_column(
            "due_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("assignment_publish_readiness_snapshots") as batch:
        batch.alter_column(
            "due_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
