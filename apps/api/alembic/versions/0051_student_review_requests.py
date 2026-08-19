"""Add student grade review requests.

Revision ID: 0051_student_review_requests
Revises: 0050_forced_password_change
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_student_review_requests"
down_revision: str | None = "0050_forced_password_change"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "student_review_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grade_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_answer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("teacher_response", sa.Text(), nullable=True),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','needs_information','resolved','cancelled')",
            name="ck_student_review_request_status",
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('upheld','score_changed','needs_information')",
            name="ck_student_review_request_resolution",
        ),
        sa.ForeignKeyConstraint(["grade_release_id"], ["grade_releases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["score_snapshot_id"], ["submission_score_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["student_answer_id"], ["student_answers.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "owner_id",
        "student_id",
        "requested_by",
        "grade_release_id",
        "student_answer_id",
        "status",
    ):
        op.create_index(
            f"ix_student_review_requests_{column}",
            "student_review_requests",
            [column],
        )
    op.create_index(
        "ix_student_review_request_teacher_queue",
        "student_review_requests",
        ["owner_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("student_review_requests")
