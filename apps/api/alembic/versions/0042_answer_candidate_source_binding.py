"""Link answer candidates to confirmed reference source bindings.

Revision ID: 0042_answer_candidate_source_binding
Revises: 0041_reference_answer_source_bindings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_answer_candidate_source_binding"
down_revision: str | None = "0041_reference_answer_source_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assignment_answer_draft_candidates") as batch_op:
        batch_op.add_column(sa.Column("source_reference_binding_id", sa.Uuid()))
        batch_op.create_foreign_key(
            "fk_answer_candidate_reference_binding",
            "reference_answer_source_bindings",
            ["source_reference_binding_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_answer_candidate_reference_binding",
            ["source_reference_binding_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("assignment_answer_draft_candidates") as batch_op:
        batch_op.drop_constraint("uq_answer_candidate_reference_binding", type_="unique")
        batch_op.drop_constraint("fk_answer_candidate_reference_binding", type_="foreignkey")
        batch_op.drop_column("source_reference_binding_id")
