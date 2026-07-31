"""Add durable processing-stage and automatic-confirmation provenance.

Revision ID: 0029_processing_auto_confirmation
Revises: 0028_processing_orchestrator
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0029_processing_auto_confirmation"
down_revision: str | None = "0028_processing_orchestrator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Revision 0006 historically constructs this table from live ORM metadata.
    # Fresh online databases can therefore already contain this new column even
    # though existing databases at 0028 do not. Keep the new migration safe for
    # both histories without changing the old migration.
    region_columns = (
        set()
        if context.is_offline_mode()
        else {
            item["name"]
            for item in sa.inspect(op.get_bind()).get_columns("student_answer_regions")
        }
    )
    if "confirmation_origin" not in region_columns:
        op.add_column(
            "student_answer_regions",
            sa.Column("confirmation_origin", sa.String(length=24)),
        )

    evidence_columns = (
        set()
        if context.is_offline_mode()
        else {
            item["name"]
            for item in sa.inspect(op.get_bind()).get_columns(
                "question_recognition_evidence"
            )
        }
    )
    if "confirmation_origin" not in evidence_columns:
        op.add_column(
            "question_recognition_evidence",
            sa.Column("confirmation_origin", sa.String(length=24)),
        )

    with op.batch_alter_table("processing_steps") as batch:
        batch.add_column(
            sa.Column(
                "stage",
                sa.String(length=40),
                server_default="answer_recognition",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("submission_processing_job_id", sa.Uuid()))
        batch.create_foreign_key(
            "fk_processing_step_submission_processing_job",
            "submission_processing_jobs",
            ["submission_processing_job_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_processing_steps_submission_processing_job_id",
            ["submission_processing_job_id"],
        )

    op.execute(
        sa.text(
            """
            UPDATE processing_steps
            SET stage = CASE kind
                WHEN 'codex_suggestion' THEN 'codex_suggestion'
                WHEN 'review_readiness' THEN 'review_readiness'
                ELSE 'answer_recognition'
            END
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("processing_steps") as batch:
        batch.drop_index("ix_processing_steps_submission_processing_job_id")
        batch.drop_constraint(
            "fk_processing_step_submission_processing_job",
            type_="foreignkey",
        )
        batch.drop_column("submission_processing_job_id")
        batch.drop_column("stage")

    if context.is_offline_mode():
        op.drop_column("question_recognition_evidence", "confirmation_origin")
        op.drop_column("student_answer_regions", "confirmation_origin")
        return

    inspector = sa.inspect(op.get_bind())
    evidence_columns = {
        item["name"]
        for item in inspector.get_columns("question_recognition_evidence")
    }
    if "confirmation_origin" in evidence_columns:
        op.drop_column("question_recognition_evidence", "confirmation_origin")
    region_columns = {
        item["name"] for item in inspector.get_columns("student_answer_regions")
    }
    if "confirmation_origin" in region_columns:
        op.drop_column("student_answer_regions", "confirmation_origin")
