"""Disable forced password changes for all accounts.

Revision ID: 0053_disable_forced_password_change
Revises: 0052_class_resource_publication
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_disable_forced_password_change"
down_revision: str | None = "0052_class_resource_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE users SET must_change_password = false"))


def downgrade() -> None:
    # Previous values cannot be reconstructed safely; keep accounts unblocked.
    pass
