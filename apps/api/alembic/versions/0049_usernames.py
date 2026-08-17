"""Add administrator-issued usernames.

Revision ID: 0049_usernames
Revises: 0048_class_resources
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_usernames"
down_revision: str | None = "0048_class_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))
    op.execute(
        "UPDATE users SET username = "
        "'user-' || substr(replace(lower(CAST(id AS VARCHAR(36))), '-', ''), 1, 24) "
        "WHERE username IS NULL"
    )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("username", existing_type=sa.String(64), nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("username")
