"""Pre-index textbooks and allow solution-only source matches.

Revision ID: 0044_textbook_content_indexes
Revises: 0043_textbook_source_matches
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_textbook_content_indexes"
down_revision: str | None = "0043_textbook_source_matches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "textbook_content_indexes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("draft_revision_id", sa.Uuid(), nullable=False),
        sa.Column("paper_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_file_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("source_page_id", sa.Uuid(), nullable=False),
        sa.Column("source_recognition_block_id", sa.Uuid()),
        sa.Column("source_key", sa.String(80), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("detected_number", sa.String(40)),
        sa.Column("chapter_label", sa.String(120)),
        sa.Column("section_label", sa.String(120)),
        sa.Column("exercise_label", sa.String(120)),
        sa.Column("pdf_page_number", sa.Integer(), nullable=False),
        sa.Column("printed_page_number", sa.Integer()),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("recognition_block_ids", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("index_version > 0", name="ck_textbook_index_version"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["draft_revision_id"], ["assignment_draft_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_file_analysis_id"],
            ["assignment_source_file_analyses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["source_page_id"], ["paper_pages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_recognition_block_id"], ["recognition_blocks.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "source_file_analysis_id",
            "index_version",
            "source_key",
            name="uq_textbook_index_source_version_key",
        ),
    )
    for column in (
        "owner_id",
        "assignment_id",
        "draft_revision_id",
        "paper_version_id",
        "source_file_analysis_id",
        "source_page_id",
    ):
        op.create_index(
            f"ix_textbook_content_indexes_{column}",
            "textbook_content_indexes",
            [column],
        )
    with op.batch_alter_table("textbook_source_match_candidates") as batch_op:
        batch_op.alter_column("question_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.alter_column("answer_candidate_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column("source_reference_binding_id", sa.Uuid()))
        batch_op.add_column(sa.Column("confirmed_source_binding_id", sa.Uuid()))
        batch_op.create_foreign_key(
            "fk_textbook_match_source_binding",
            "reference_answer_source_bindings",
            ["source_reference_binding_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_textbook_match_confirmed_source_binding",
            "reference_answer_source_bindings",
            ["confirmed_source_binding_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_textbook_match_revision_binding_version_rank",
            ["draft_revision_id", "source_reference_binding_id", "match_version", "rank"],
        )
        batch_op.create_unique_constraint(
            "uq_textbook_match_confirmed_source_binding",
            ["confirmed_source_binding_id"],
        )
        batch_op.create_index(
            "ix_textbook_source_match_candidates_source_reference_binding_id",
            ["source_reference_binding_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("textbook_source_match_candidates") as batch_op:
        batch_op.drop_index("ix_textbook_source_match_candidates_source_reference_binding_id")
        batch_op.drop_constraint("uq_textbook_match_confirmed_source_binding", type_="unique")
        batch_op.drop_constraint("uq_textbook_match_revision_binding_version_rank", type_="unique")
        batch_op.drop_constraint("fk_textbook_match_confirmed_source_binding", type_="foreignkey")
        batch_op.drop_constraint("fk_textbook_match_source_binding", type_="foreignkey")
        batch_op.drop_column("confirmed_source_binding_id")
        batch_op.drop_column("source_reference_binding_id")
        batch_op.alter_column("answer_candidate_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.alter_column("question_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("textbook_content_indexes")
