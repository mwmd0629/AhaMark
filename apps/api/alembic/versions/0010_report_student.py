"""optional student scope for report jobs

Revision ID: 0010_report_student
Revises: 0009_submission_recognition_blocks
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_report_student"
down_revision = "0009_submission_recognition_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_job_student_scopes",
        sa.Column(
            "report_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_report_job_student_scopes_student_id",
        "report_job_student_scopes",
        ["student_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_job_student_scopes_student_id", table_name="report_job_student_scopes")
    op.drop_table("report_job_student_scopes")
