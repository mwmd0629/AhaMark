"""allow student accounts without a recovery email

Revision ID: 0028_optional_student_recovery_email
Revises: 0027_student_login_recovery
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_optional_student_recovery_email"
down_revision: str | None = "0027_student_login_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "email",
            existing_type=sa.String(length=320),
            nullable=True,
        )


def downgrade() -> None:
    # A downgrade must preserve accounts created without an email while restoring
    # the legacy NOT NULL constraint. These values are never used by upgraded code.
    op.execute(
        sa.text(
            """
            UPDATE users
            SET email = 'student-' || REPLACE(CAST(id AS VARCHAR), '-', '')
                        || '@downgrade.synthetic.invalid'
            WHERE email IS NULL
            """
        )
    )
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "email",
            existing_type=sa.String(length=320),
            nullable=False,
        )
