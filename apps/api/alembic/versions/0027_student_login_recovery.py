"""student login names and email recovery challenges

Revision ID: 0027_student_login_recovery
Revises: 0026_student_portal
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_student_login_recovery"
down_revision: str | None = "0026_student_portal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalized_login_name(student_number: object) -> str | None:
    if not isinstance(student_number, str):
        return None
    value = unicodedata.normalize("NFKC", student_number.strip()).casefold()
    if not value or len(value) > 64 or "@" in value or any(char.isspace() for char in value):
        return None
    return value


def _backfill_student_login_names() -> None:
    if op.get_context().as_sql:
        return
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT student_account_links.user_id AS user_id,
                   students.student_number AS student_number
            FROM student_account_links
            JOIN students ON students.id = student_account_links.student_id
            """
        )
    ).mappings()
    candidates = [
        (row["user_id"], _normalized_login_name(row["student_number"])) for row in rows
    ]
    counts = Counter(candidate for _, candidate in candidates if candidate is not None)
    for user_id, candidate in candidates:
        if candidate is None or counts[candidate] != 1:
            continue
        connection.execute(
            sa.text("UPDATE users SET login_name = :login_name WHERE id = :user_id"),
            {"login_name": candidate, "user_id": user_id},
        )


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("login_name", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))

    _backfill_student_login_names()

    with op.batch_alter_table("users") as batch:
        batch.create_unique_constraint("uq_users_login_name", ["login_name"])
        batch.create_index("ix_users_login_name", ["login_name"], unique=False)

    op.create_table(
        "auth_email_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    for column in ("user_id", "purpose", "expires_at", "consumed_at"):
        op.create_index(
            f"ix_auth_email_challenges_{column}", "auth_email_challenges", [column]
        )


def downgrade() -> None:
    op.drop_table("auth_email_challenges")
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_login_name")
        batch.drop_constraint("uq_users_login_name", type_="unique")
        batch.drop_column("email_verified_at")
        batch.drop_column("login_name")
