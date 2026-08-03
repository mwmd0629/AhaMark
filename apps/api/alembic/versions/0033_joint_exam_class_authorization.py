"""Authorize cross-teacher classes for joint exams.

Revision ID: 0033_joint_exam_class_authorization
Revises: 0032_joint_exam_roster
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_joint_exam_class_authorization"
down_revision: str | None = "0032_joint_exam_roster"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assignment_classes") as batch:
        batch.add_column(sa.Column("authorized_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_assignment_class_authorized_by",
            "users",
            ["authorized_by"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_assignment_classes_authorized_by", ["authorized_by"])
    op.execute(
        sa.text(
            "UPDATE assignment_classes "
            "SET authorized_by = ("
            "SELECT assignments.owner_id FROM assignments "
            "WHERE assignments.id = assignment_classes.assignment_id"
            ") WHERE authorized_by IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("assignment_classes") as batch:
        batch.drop_index("ix_assignment_classes_authorized_by")
        batch.drop_constraint("fk_assignment_class_authorized_by", type_="foreignkey")
        batch.drop_column("authorized_by")
