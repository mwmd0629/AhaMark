"""Add suggestion-only textbook source matches.

Revision ID: 0043_textbook_source_matches
Revises: 0042_answer_candidate_source_binding
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_textbook_source_matches"
down_revision: str | None = "0042_answer_candidate_source_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "textbook_source_match_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("draft_revision_id", sa.Uuid(), nullable=False),
        sa.Column("paper_version_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_question_id", sa.Uuid()),
        sa.Column("answer_candidate_id", sa.Uuid(), nullable=False),
        sa.Column("source_file_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("source_page_id", sa.Uuid(), nullable=False),
        sa.Column("source_recognition_block_id", sa.Uuid()),
        sa.Column("detected_number", sa.String(40)),
        sa.Column("chapter_label", sa.String(120)),
        sa.Column("section_label", sa.String(120)),
        sa.Column("exercise_label", sa.String(120)),
        sa.Column("pdf_page_number", sa.Integer(), nullable=False),
        sa.Column("printed_page_number", sa.Integer()),
        sa.Column("match_version", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="suggested"),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("matching_method", sa.String(64), nullable=False),
        sa.Column("solution_content_hash", sa.String(64), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("warning_codes", sa.JSON(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("match_version > 0", name="ck_textbook_match_version"),
        sa.CheckConstraint("rank > 0", name="ck_textbook_match_rank"),
        sa.CheckConstraint("edit_version >= 0", name="ck_textbook_match_edit_version"),
        sa.CheckConstraint(
            "status IN ('suggested','confirmed','rejected','superseded')",
            name="ck_textbook_match_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_textbook_match_confidence",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["draft_revision_id"], ["assignment_draft_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["answer_candidate_id"],
            ["assignment_answer_draft_candidates.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_file_analysis_id"],
            ["assignment_source_file_analyses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["source_page_id"], ["paper_pages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_recognition_block_id"], ["recognition_blocks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "draft_revision_id",
            "question_id",
            "match_version",
            "rank",
            name="uq_textbook_match_revision_question_version_rank",
        ),
        sa.UniqueConstraint("confirmed_question_id", name="uq_textbook_match_confirmed_question"),
    )
    for column in (
        "owner_id",
        "assignment_id",
        "draft_revision_id",
        "paper_version_id",
        "question_id",
        "answer_candidate_id",
        "source_file_analysis_id",
        "source_page_id",
        "status",
    ):
        op.create_index(
            f"ix_textbook_source_match_candidates_{column}",
            "textbook_source_match_candidates",
            [column],
        )


def downgrade() -> None:
    op.drop_table("textbook_source_match_candidates")
