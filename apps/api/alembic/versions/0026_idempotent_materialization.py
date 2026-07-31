"""Add idempotent Answer and Structured Rubric materialization lineage.

Revision ID: 0026_idempotent_materialization
Revises: 0025_ai_grading_audit_contract
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_idempotent_materialization"
down_revision: str | None = "0025_ai_grading_audit_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reference_answer_versions") as batch:
        batch.add_column(sa.Column("origin_answer_candidate_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("materialization_key", sa.String(length=64), nullable=True))
        batch.create_foreign_key(
            "fk_reference_answer_origin_candidate",
            "assignment_answer_draft_candidates",
            ["origin_answer_candidate_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_reference_answer_origin_candidate", ["origin_answer_candidate_id"]
        )
        batch.create_unique_constraint(
            "uq_reference_answer_materialization_key", ["materialization_key"]
        )

    op.execute(
        sa.text(
            """
            UPDATE reference_answer_versions
            SET origin_answer_candidate_id = (
                SELECT candidate.id
                FROM assignment_answer_draft_candidates AS candidate
                WHERE candidate.materialized_reference_answer_id = reference_answer_versions.id
            )
            WHERE origin_answer_candidate_id IS NULL
              AND EXISTS (
                SELECT 1
                FROM assignment_answer_draft_candidates AS candidate
                WHERE candidate.materialized_reference_answer_id = reference_answer_versions.id
              )
            """
        )
    )

    with op.batch_alter_table("structured_rubric_versions") as batch:
        batch.add_column(sa.Column("origin_rubric_candidate_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("materialization_key", sa.String(length=64), nullable=True))
        batch.create_foreign_key(
            "fk_structured_rubric_origin_candidate",
            "assignment_rubric_draft_candidates",
            ["origin_rubric_candidate_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_structured_rubric_origin_candidate", ["origin_rubric_candidate_id"]
        )
        batch.create_unique_constraint(
            "uq_structured_rubric_materialization_key", ["materialization_key"]
        )

    op.execute(
        sa.text(
            """
            UPDATE structured_rubric_versions
            SET origin_rubric_candidate_id = (
                SELECT candidate.id
                FROM assignment_rubric_draft_candidates AS candidate
                WHERE candidate.materialized_structured_rubric_id =
                      structured_rubric_versions.id
            )
            WHERE origin_rubric_candidate_id IS NULL
              AND EXISTS (
                SELECT 1
                FROM assignment_rubric_draft_candidates AS candidate
                WHERE candidate.materialized_structured_rubric_id =
                      structured_rubric_versions.id
              )
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("structured_rubric_versions") as batch:
        batch.drop_constraint("uq_structured_rubric_materialization_key", type_="unique")
        batch.drop_constraint("uq_structured_rubric_origin_candidate", type_="unique")
        batch.drop_constraint("fk_structured_rubric_origin_candidate", type_="foreignkey")
        batch.drop_column("materialization_key")
        batch.drop_column("origin_rubric_candidate_id")

    with op.batch_alter_table("reference_answer_versions") as batch:
        batch.drop_constraint("uq_reference_answer_materialization_key", type_="unique")
        batch.drop_constraint("uq_reference_answer_origin_candidate", type_="unique")
        batch.drop_constraint("fk_reference_answer_origin_candidate", type_="foreignkey")
        batch.drop_column("materialization_key")
        batch.drop_column("origin_answer_candidate_id")
