"""initial foundation tables"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    status = sa.Enum("active", "inactive", name="status")
    file_status = sa.Enum("pending", "ready", "deleted", "failed", name="filestatus")
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_status", "users", ["status"])
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
    )
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "stored_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(127), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", file_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stored_files_owner_id", "stored_files", ["owner_id"])
    op.create_index("ix_stored_files_checksum", "stored_files", ["checksum"])
    op.create_index("ix_stored_files_status", "stored_files", ["status"])
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(100)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_resource", "audit_logs", ["resource_type", "resource_id"])
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    for table in ["audit_logs", "stored_files", "user_roles", "roles", "users"]:
        op.drop_table(table)
    sa.Enum(name="filestatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="status").drop(op.get_bind(), checkfirst=True)
