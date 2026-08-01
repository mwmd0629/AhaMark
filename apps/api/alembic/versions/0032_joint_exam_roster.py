"""Add joint-exam mode and immutable participant snapshots.

Revision ID: 0032_joint_exam_roster
Revises: 0031_student_portal
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_joint_exam_roster"
down_revision: str | None = "0031_student_portal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assignments") as batch:
        batch.add_column(
            sa.Column(
                "delivery_mode",
                sa.String(length=30),
                nullable=False,
                server_default="class_assignment",
            )
        )
        batch.create_index("ix_assignments_delivery_mode", ["delivery_mode"])

    op.create_table(
        "assignment_participant_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("student_number", sa.String(length=64), nullable=False),
        sa.Column("student_name", sa.String(length=120), nullable=False),
        sa.Column("membership_joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assignment_id", "student_id", name="uq_assignment_participant_student"
        ),
    )
    op.create_index(
        "ix_assignment_participant_snapshots_assignment_id",
        "assignment_participant_snapshots",
        ["assignment_id"],
    )
    op.create_index(
        "ix_assignment_participant_snapshots_class_id",
        "assignment_participant_snapshots",
        ["class_id"],
    )
    op.create_index(
        "ix_assignment_participant_snapshots_student_id",
        "assignment_participant_snapshots",
        ["student_id"],
    )
    op.create_index(
        "ix_assignment_participant_class",
        "assignment_participant_snapshots",
        ["assignment_id", "class_id"],
    )


def downgrade() -> None:
    op.drop_table("assignment_participant_snapshots")
    with op.batch_alter_table("assignments") as batch:
        batch.drop_index("ix_assignments_delivery_mode")
        batch.drop_column("delivery_mode")
