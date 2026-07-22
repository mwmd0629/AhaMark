"""persistent teacher sessions

Revision ID: 0008_teacher_sessions
Revises: 0007_grade_release_reports_analytics
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_teacher_sessions"
down_revision = "0007_grade_release_reports_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ["user_id", "token_hash", "expires_at", "revoked_at"]:
        op.create_index(f"ix_user_sessions_{column}", "user_sessions", [column])


def downgrade() -> None:
    op.drop_table("user_sessions")
