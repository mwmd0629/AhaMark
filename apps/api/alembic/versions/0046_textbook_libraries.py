"""Add teacher-owned textbook libraries and assignment selections.

Revision ID: 0046_textbook_libraries
Revises: 0045_textbook_question_only_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_textbook_libraries"
down_revision: str | None = "0045_textbook_question_only_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "textbook_libraries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("volume_label", sa.String(80)),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ready"),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('ready','archived')", name="ck_textbook_library_status"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("owner_id", "source_key", name="uq_textbook_library_owner_source"),
    )
    op.create_index("ix_textbook_libraries_owner_id", "textbook_libraries", ["owner_id"])
    op.create_index("ix_textbook_libraries_title", "textbook_libraries", ["title"])
    op.create_index("ix_textbook_libraries_status", "textbook_libraries", ["status"])

    op.create_table(
        "textbook_library_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(160), nullable=False),
        sa.Column("detected_number", sa.String(40), nullable=False),
        sa.Column("exercise_label", sa.String(120)),
        sa.Column("pdf_page_number", sa.Integer(), nullable=False),
        sa.Column("printed_page_number", sa.Integer()),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("ocr_confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="manual_required"),
        sa.Column("warning_codes", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('suggested','manual_required','excluded')",
            name="ck_textbook_library_question_status",
        ),
        sa.CheckConstraint(
            "ocr_confidence >= 0 AND ocr_confidence <= 1",
            name="ck_textbook_library_question_confidence",
        ),
        sa.ForeignKeyConstraint(["library_id"], ["textbook_libraries.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("library_id", "source_key", name="uq_textbook_library_question_key"),
    )
    for column in ("library_id", "exercise_label", "status"):
        op.create_index(
            f"ix_textbook_library_questions_{column}",
            "textbook_library_questions",
            [column],
        )

    op.create_table(
        "assignment_textbook_library_selections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("selected_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["library_id"], ["textbook_libraries.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["selected_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "assignment_id", "library_id", name="uq_assignment_textbook_library_selection"
        ),
    )
    for column in ("owner_id", "assignment_id", "library_id"):
        op.create_index(
            f"ix_assignment_textbook_library_selections_{column}",
            "assignment_textbook_library_selections",
            [column],
        )

    with op.batch_alter_table("textbook_source_match_candidates") as batch_op:
        batch_op.alter_column("source_file_analysis_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.alter_column("source_page_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column("library_question_id", sa.Uuid()))
        batch_op.create_foreign_key(
            "fk_textbook_match_library_question",
            "textbook_library_questions",
            ["library_question_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_textbook_source_match_candidates_library_question_id",
            ["library_question_id"],
        )
        batch_op.create_check_constraint(
            "ck_textbook_match_source_kind",
            "(library_question_id IS NOT NULL AND source_file_analysis_id IS NULL "
            "AND source_page_id IS NULL) OR "
            "(library_question_id IS NULL AND source_file_analysis_id IS NOT NULL "
            "AND source_page_id IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("textbook_source_match_candidates") as batch_op:
        batch_op.drop_constraint("ck_textbook_match_source_kind", type_="check")
        batch_op.drop_index("ix_textbook_source_match_candidates_library_question_id")
        batch_op.drop_constraint("fk_textbook_match_library_question", type_="foreignkey")
        batch_op.drop_column("library_question_id")
        batch_op.alter_column("source_page_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.alter_column("source_file_analysis_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("assignment_textbook_library_selections")
    op.drop_table("textbook_library_questions")
    op.drop_table("textbook_libraries")
