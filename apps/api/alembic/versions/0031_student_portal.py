"""Add explicit student account links and grade visibility.

Revision ID: 0031_student_portal
Revises: 0030_collaborative_grading
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_student_portal"
down_revision: str | None = "0030_collaborative_grading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("students") as batch:
        batch.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_student_user", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_students_user_id", ["user_id"])
        batch.create_unique_constraint("uq_student_owner_user", ["owner_id", "user_id"])

    with op.batch_alter_table("grade_releases") as batch:
        batch.add_column(sa.Column("student_visible_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("student_visible_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_grade_release_student_visible_by",
            "users",
            ["student_visible_by"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_grade_releases_student_visible_at", ["student_visible_at"])


def downgrade() -> None:
    with op.batch_alter_table("grade_releases") as batch:
        batch.drop_index("ix_grade_releases_student_visible_at")
        batch.drop_constraint("fk_grade_release_student_visible_by", type_="foreignkey")
        batch.drop_column("student_visible_by")
        batch.drop_column("student_visible_at")

    with op.batch_alter_table("students") as batch:
        batch.drop_constraint("uq_student_owner_user", type_="unique")
        batch.drop_index("ix_students_user_id")
        batch.drop_constraint("fk_student_user", type_="foreignkey")
        batch.drop_column("user_id")
