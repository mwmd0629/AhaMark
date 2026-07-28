"""Persist strict AI grading references and invocation timing.

Revision ID: 0025_ai_grading_audit_contract
Revises: 0024_nullable_publish_readiness_due_at
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_ai_grading_audit_contract"
down_revision: str | None = "0024_nullable_publish_readiness_due_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_criterion_suggestions") as batch:
        batch.add_column(
            sa.Column("validation_refs", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("error_codes", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(
            sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.true())
        )
    with op.batch_alter_table("ai_provider_invocations") as batch:
        batch.add_column(sa.Column("error_code", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_provider_invocations") as batch:
        batch.drop_column("completed_at")
        batch.drop_column("started_at")
        batch.drop_column("error_code")
    with op.batch_alter_table("ai_criterion_suggestions") as batch:
        batch.drop_column("requires_review")
        batch.drop_column("error_codes")
        batch.drop_column("validation_refs")
