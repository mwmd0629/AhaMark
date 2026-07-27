"""Complete Assignment Generation Provider invocation audit fields.

Revision ID: 0023_assignment_provider_invocation_audit
Revises: 0022_assignment_central_review_publish
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_assignment_provider_invocation_audit"
down_revision: str | None = "0022_assignment_central_review_publish"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "assignment_generation_provider_invocations"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("model_snapshot", sa.String(160)))
    op.add_column(
        TABLE,
        sa.Column(
            "provider_config_version",
            sa.String(80),
            nullable=False,
            server_default="legacy-unknown",
        ),
    )
    op.add_column(
        TABLE, sa.Column("stage_generation", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(TABLE, sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(TABLE, sa.Column("image_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        TABLE, sa.Column("image_bytes", sa.BigInteger(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    for column in (
        "image_bytes",
        "image_count",
        "retry_count",
        "stage_generation",
        "provider_config_version",
        "model_snapshot",
    ):
        op.drop_column(TABLE, column)
