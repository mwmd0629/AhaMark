"""Add teacher-reviewed hierarchical question structures.

Revision ID: 0038_question_structure_reviews
Revises: 0037_rubric_templates
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_question_structure_reviews"
down_revision: str | None = "0037_rubric_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_structure_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("paper_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("score_policy", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.Uuid()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_question_structure_review_version"),
        sa.CheckConstraint("edit_version >= 0", name="ck_question_structure_review_edit_version"),
        sa.CheckConstraint(
            "status IN ('draft','confirmed')",
            name="ck_question_structure_review_status",
        ),
        sa.CheckConstraint(
            "score_policy IN ('unconfirmed','equal_weight','manual','template')",
            name="ck_question_structure_review_score_policy",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assignment_id",
            "version",
            name="uq_question_structure_review_version",
        ),
    )
    for column in ("owner_id", "assignment_id", "paper_version_id", "status"):
        op.create_index(
            f"ix_question_structure_reviews_{column}",
            "question_structure_reviews",
            [column],
        )

    op.create_table(
        "question_structure_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("display_number", sa.String(40), nullable=False),
        sa.Column("parent_number", sa.String(40)),
        sa.Column("sub_number", sa.String(40)),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("max_score", sa.Numeric(10, 2)),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("display_order > 0", name="ck_question_structure_item_order"),
        sa.CheckConstraint(
            "action IN ('keep','remove')",
            name="ck_question_structure_item_action",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_question_structure_item_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["question_structure_reviews.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_id",
            "question_id",
            name="uq_question_structure_item_question",
        ),
        sa.UniqueConstraint(
            "review_id",
            "display_order",
            name="uq_question_structure_item_order",
        ),
    )
    op.create_index(
        "ix_question_structure_items_review_id",
        "question_structure_items",
        ["review_id"],
    )
    op.create_index(
        "ix_question_structure_items_question_id",
        "question_structure_items",
        ["question_id"],
    )


def downgrade() -> None:
    op.drop_table("question_structure_items")
    op.drop_table("question_structure_reviews")
