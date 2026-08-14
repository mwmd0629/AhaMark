"""Add independent reusable rubric templates.

Revision ID: 0037_rubric_templates
Revises: 0036_grading_review_commands
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_rubric_templates"
down_revision: str | None = "0036_grading_review_commands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rubric_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(40)),
        sa.Column("grade", sa.String(40)),
        sa.Column("question_type", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','confirmed','archived')",
            name="ck_rubric_template_status",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, cols in (
        ("ix_rubric_templates_owner_id", ["owner_id"]),
        ("ix_rubric_templates_name", ["name"]),
        ("ix_rubric_templates_subject", ["subject"]),
        ("ix_rubric_templates_grade", ["grade"]),
        ("ix_rubric_templates_question_type", ["question_type"]),
        ("ix_rubric_templates_status", ["status"]),
        ("ix_rubric_template_owner_status", ["owner_id", "status"]),
    ):
        op.create_index(name, "rubric_templates", cols)
    op.create_table(
        "rubric_template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("scoring_basis", sa.String(20), nullable=False),
        sa.Column("total_points", sa.Numeric(12, 4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("version > 0", name="ck_rubric_template_version_positive"),
        sa.CheckConstraint(
            "scoring_basis IN ('proportional','fixed')", name="ck_rubric_template_scoring_basis"
        ),
        sa.CheckConstraint(
            "status IN ('draft','confirmed','archived')", name="ck_rubric_template_version_status"
        ),
        sa.ForeignKeyConstraint(["template_id"], ["rubric_templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version", name="uq_rubric_template_version"),
    )
    for name, cols in (
        ("ix_rubric_template_versions_template_id", ["template_id"]),
        ("ix_rubric_template_versions_status", ["status"]),
        ("ix_rubric_template_versions_content_hash", ["content_hash"]),
    ):
        op.create_index(name, "rubric_template_versions", cols)
    op.create_table(
        "rubric_template_criteria",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("stable_key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("max_points", sa.Numeric(12, 4), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("criterion_type", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("validation_mode", sa.String(24), nullable=False),
        sa.Column("manual_review_policy", sa.JSON(), nullable=False),
        sa.Column("partial_credit_policy", sa.JSON(), nullable=False),
        sa.Column("error_category", sa.String(80)),
        sa.Column("validation_rule", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_version_id"], ["rubric_template_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_version_id", "stable_key", name="uq_template_criterion_key"),
        sa.UniqueConstraint(
            "template_version_id", "display_order", name="uq_template_criterion_order"
        ),
    )
    op.create_index(
        "ix_rubric_template_criteria_template_version_id",
        "rubric_template_criteria",
        ["template_version_id"],
    )
    op.create_table(
        "rubric_template_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("question_version", sa.String(100), nullable=False),
        sa.Column("reference_answer_version_id", sa.Uuid(), nullable=False),
        sa.Column("reference_answer_content_hash", sa.String(64), nullable=False),
        sa.Column("structured_rubric_version_id", sa.Uuid(), nullable=False),
        sa.Column("template_content_hash", sa.String(64), nullable=False),
        sa.Column("conversion", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["template_id"], ["rubric_templates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["template_version_id"], ["rubric_template_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reference_answer_version_id"], ["reference_answer_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["structured_rubric_version_id"], ["structured_rubric_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_template_application_key"),
        sa.UniqueConstraint("structured_rubric_version_id", name="uq_template_application_rubric"),
    )
    for col in (
        "owner_id",
        "template_id",
        "template_version_id",
        "assignment_id",
        "question_id",
        "reference_answer_version_id",
        "structured_rubric_version_id",
    ):
        op.create_index(
            f"ix_rubric_template_applications_{col}", "rubric_template_applications", [col]
        )


def downgrade() -> None:
    op.drop_table("rubric_template_applications")
    op.drop_table("rubric_template_criteria")
    op.drop_table("rubric_template_versions")
    op.drop_table("rubric_templates")
