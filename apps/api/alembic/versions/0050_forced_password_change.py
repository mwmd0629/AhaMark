"""Add first-login password change state.

Revision ID: 0050_forced_password_change
Revises: 0049_usernames
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_forced_password_change"
down_revision: str | None = "0049_usernames"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("must_change_password")
