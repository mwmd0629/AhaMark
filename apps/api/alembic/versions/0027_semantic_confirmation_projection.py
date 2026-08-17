"""Add semantic confirmation fingerprints and projection integrity evidence.

Revision ID: 0027_semantic_projection
Revises: 0026_idempotent_materialization
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_semantic_projection"
down_revision: str | None = "0026_idempotent_materialization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY_LOSS_REPORT_HASH = "6e0873083fa065f4f3343e12ab82720eea0bd1a66ad1687742dcd38dc1b1ddf2"


def upgrade() -> None:
    with op.batch_alter_table("assignment_explicit_confirmations") as batch:
        batch.add_column(sa.Column("fingerprint_schema_version", sa.String(40)))
        batch.add_column(sa.Column("paper_version_id", sa.Uuid()))
        batch.add_column(sa.Column("question_scope_hash", sa.String(64)))
        batch.add_column(sa.Column("confirmation_origin", sa.String(24)))
        batch.create_foreign_key(
            "fk_assignment_confirmation_paper_version",
            "paper_versions",
            ["paper_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_assignment_explicit_confirmations_paper_version_id",
            ["paper_version_id"],
        )

    confirmation = sa.table(
        "assignment_explicit_confirmations",
        sa.column("confirmation_origin", sa.String()),
    )
    op.execute(confirmation.update().values(confirmation_origin="legacy_origin"))

    with op.batch_alter_table("assignment_rubric_publication_bindings") as batch:
        batch.add_column(sa.Column("source_semantic_hash", sa.String(64)))
        batch.add_column(sa.Column("target_legacy_hash", sa.String(64)))
        batch.add_column(sa.Column("projection_profile", sa.String(64)))
        batch.add_column(sa.Column("projection_version", sa.String(40)))
        batch.add_column(sa.Column("loss_report", sa.JSON()))
        batch.add_column(sa.Column("loss_report_hash", sa.String(64)))

    binding = sa.table(
        "assignment_rubric_publication_bindings",
        sa.column("source_binding_hash", sa.String()),
        sa.column("source_semantic_hash", sa.String()),
        sa.column("projection_profile", sa.String()),
        sa.column("projection_version", sa.String()),
        sa.column("loss_report", sa.JSON()),
        sa.column("loss_report_hash", sa.String()),
    )
    op.execute(
        binding.update().values(
            source_semantic_hash=binding.c.source_binding_hash,
            projection_profile="legacy-unverified",
            projection_version="pre-semantic-v1",
            loss_report=sa.literal_column("'[]'"),
            loss_report_hash=EMPTY_LOSS_REPORT_HASH,
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("assignment_rubric_publication_bindings") as batch:
        batch.drop_column("loss_report_hash")
        batch.drop_column("loss_report")
        batch.drop_column("projection_version")
        batch.drop_column("projection_profile")
        batch.drop_column("target_legacy_hash")
        batch.drop_column("source_semantic_hash")

    with op.batch_alter_table("assignment_explicit_confirmations") as batch:
        batch.drop_index("ix_assignment_explicit_confirmations_paper_version_id")
        batch.drop_constraint(
            "fk_assignment_confirmation_paper_version", type_="foreignkey"
        )
        batch.drop_column("confirmation_origin")
        batch.drop_column("question_scope_hash")
        batch.drop_column("paper_version_id")
        batch.drop_column("fingerprint_schema_version")
