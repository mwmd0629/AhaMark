"""Add reusable class resources.

Revision ID: 0048_class_resources
Revises: 0047_formula_recognition_candidates
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_class_resources"
down_revision: str | None = "0047_formula_recognition_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "class_resources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("stored_file_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("resource_type", sa.String(24), nullable=False, server_default="exercise"),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ready"),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "resource_type IN ('exercise','handout','reference','other')",
            name="ck_class_resource_type",
        ),
        sa.CheckConstraint("status IN ('ready','archived')", name="ck_class_resource_status"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stored_file_id"], ["stored_files.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_class_resources_owner_id", "class_resources", ["owner_id"])
    op.create_index("ix_class_resources_class_id", "class_resources", ["class_id"])
    op.create_index("ix_class_resources_title", "class_resources", ["title"])
    op.create_index("ix_class_resources_resource_type", "class_resources", ["resource_type"])
    op.create_index("ix_class_resources_status", "class_resources", ["status"])


def downgrade() -> None:
    op.drop_table("class_resources")
