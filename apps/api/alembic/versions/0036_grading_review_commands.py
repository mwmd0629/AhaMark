"""Add idempotent grading review commands.

Revision ID: 0036_grading_review_commands
Revises: 0035_question_anchor_segmentation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_grading_review_commands"
down_revision: str | None = "0035_question_anchor_segmentation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grading_review_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("grading_batch_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("command_type", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grading_batch_id"], ["grading_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_grading_review_command_key"),
    )
    op.create_index("ix_grading_review_commands_actor_id", "grading_review_commands", ["actor_id"])
    op.create_index(
        "ix_grading_review_commands_grading_batch_id",
        "grading_review_commands",
        ["grading_batch_id"],
    )
    op.create_index(
        "ix_grading_review_commands_submission_id",
        "grading_review_commands",
        ["submission_id"],
    )
    op.create_index(
        "ix_grading_review_commands_command_type",
        "grading_review_commands",
        ["command_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_grading_review_commands_command_type", table_name="grading_review_commands")
    op.drop_index("ix_grading_review_commands_submission_id", table_name="grading_review_commands")
    op.drop_index(
        "ix_grading_review_commands_grading_batch_id", table_name="grading_review_commands"
    )
    op.drop_index("ix_grading_review_commands_actor_id", table_name="grading_review_commands")
    op.drop_table("grading_review_commands")
