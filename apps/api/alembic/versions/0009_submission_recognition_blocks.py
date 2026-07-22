"""submission page OCR blocks

Revision ID: 0009_submission_recognition_blocks
Revises: 0008_teacher_sessions
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_submission_recognition_blocks"
down_revision = "0008_teacher_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submission_recognition_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "submission_recognition_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submission_recognition_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "submission_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submission_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("latex", sa.Text()),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("provider_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("submission_page_id", "block_index", name="uq_submission_page_block"),
    )
    for column in ["submission_recognition_job_id", "submission_page_id", "status"]:
        op.create_index(
            f"ix_submission_recognition_blocks_{column}", "submission_recognition_blocks", [column]
        )


def downgrade() -> None:
    op.drop_table("submission_recognition_blocks")
