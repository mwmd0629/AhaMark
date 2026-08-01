"""Add question-scoped collaborative grading.

Revision ID: 0030_collaborative_grading
Revises: 0029_processing_auto_confirmation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0030_collaborative_grading"
down_revision: str | None = "0029_processing_auto_confirmation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grading_collaborators",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", "user_id", name="uq_grading_collaborator"),
    )
    op.create_index(
        "ix_grading_collaborators_assignment_id", "grading_collaborators", ["assignment_id"]
    )
    op.create_index("ix_grading_collaborators_user_id", "grading_collaborators", ["user_id"])
    op.create_index("ix_grading_collaborators_status", "grading_collaborators", ["status"])

    op.create_table(
        "grading_question_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("grading_batch_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grading_batch_id"], ["grading_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grading_batch_id", "question_id", name="uq_grading_question_assignment"
        ),
    )
    op.create_index(
        "ix_grading_question_assignments_grading_batch_id",
        "grading_question_assignments",
        ["grading_batch_id"],
    )
    op.create_index(
        "ix_grading_question_assignments_question_id",
        "grading_question_assignments",
        ["question_id"],
    )
    op.create_index(
        "ix_grading_question_assignments_assignee_id",
        "grading_question_assignments",
        ["assignee_id"],
    )

    review_columns = (
        set()
        if context.is_offline_mode()
        else {item["name"] for item in sa.inspect(op.get_bind()).get_columns("teacher_reviews")}
    )
    if "review_version" not in review_columns:
        with op.batch_alter_table("teacher_reviews") as batch:
            batch.add_column(
                sa.Column("review_version", sa.Integer(), server_default="1", nullable=False)
            )
            batch.create_check_constraint(
                "ck_teacher_review_version_positive", "review_version > 0"
            )


def downgrade() -> None:
    if context.is_offline_mode():
        with op.batch_alter_table("teacher_reviews") as batch:
            batch.drop_constraint("ck_teacher_review_version_positive", type_="check")
            batch.drop_column("review_version")
    else:
        inspector = sa.inspect(op.get_bind())
        review_columns = {item["name"] for item in inspector.get_columns("teacher_reviews")}
        if "review_version" in review_columns:
            check_names = {
                item["name"]
                for item in inspector.get_check_constraints("teacher_reviews")
                if item.get("name")
            }
            with op.batch_alter_table("teacher_reviews") as batch:
                if "ck_teacher_review_version_positive" in check_names:
                    batch.drop_constraint("ck_teacher_review_version_positive", type_="check")
                batch.drop_column("review_version")
    op.drop_index(
        "ix_grading_question_assignments_assignee_id",
        table_name="grading_question_assignments",
    )
    op.drop_index(
        "ix_grading_question_assignments_question_id",
        table_name="grading_question_assignments",
    )
    op.drop_index(
        "ix_grading_question_assignments_grading_batch_id",
        table_name="grading_question_assignments",
    )
    op.drop_table("grading_question_assignments")
    op.drop_index("ix_grading_collaborators_status", table_name="grading_collaborators")
    op.drop_index("ix_grading_collaborators_user_id", table_name="grading_collaborators")
    op.drop_index("ix_grading_collaborators_assignment_id", table_name="grading_collaborators")
    op.drop_table("grading_collaborators")
