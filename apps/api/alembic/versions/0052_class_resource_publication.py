"""Add explicit student publication state to class resources.

Revision ID: 0052_class_resource_publication
Revises: 0051_student_review_requests
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052_class_resource_publication"
down_revision: str | None = "0051_student_review_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "class_resources",
        sa.Column("student_visible", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "class_resources",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "class_resources",
        sa.Column("published_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_class_resources_published_by_users",
        "class_resources",
        "users",
        ["published_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_class_resources_student_visible",
        "class_resources",
        ["student_visible"],
    )


def downgrade() -> None:
    op.drop_index("ix_class_resources_student_visible", table_name="class_resources")
    op.drop_constraint(
        "fk_class_resources_published_by_users", "class_resources", type_="foreignkey"
    )
    op.drop_column("class_resources", "published_by")
    op.drop_column("class_resources", "published_at")
    op.drop_column("class_resources", "student_visible")
