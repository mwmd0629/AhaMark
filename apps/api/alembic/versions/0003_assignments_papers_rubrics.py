"""assignments, paper versions, questions, rubrics and recognition candidates"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_assignments_papers_rubrics"
down_revision = "0002_classes_students_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    u = postgresql.UUID(as_uuid=True)
    assignment_status = sa.Enum(
        "draft", "published", "grading", "completed", "archived", name="assignmentstatus"
    )
    version_status = sa.Enum(
        "draft", "processing", "ready", "confirmed", "superseded", "failed", name="versionstatus"
    )
    question_status = sa.Enum("active", "removed", name="questionstatus")
    recognition_status = sa.Enum(
        "queued",
        "converting",
        "preprocessing",
        "recognizing",
        "structuring",
        "completed",
        "failed",
        name="recognitionstatus",
    )

    def ts() -> list[sa.Column[object]]:
        return [
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ]

    op.create_table(
        "assignments",
        sa.Column("id", u, primary_key=True),
        sa.Column("owner_id", u, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(40)),
        sa.Column("grade", sa.String(40)),
        sa.Column("description", sa.Text()),
        sa.Column("instructions", sa.Text()),
        sa.Column("status", assignment_status, nullable=False),
        sa.Column("total_score", sa.Numeric(10, 2)),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("copied_from_id", u, sa.ForeignKey("assignments.id", ondelete="SET NULL")),
        sa.Column("active_paper_version_id", u),
        sa.Column("active_rubric_version_id", u),
        *ts(),
    )
    for c in ["owner_id", "title", "subject", "grade", "status"]:
        op.create_index(f"ix_assignments_{c}", "assignments", [c])
    op.create_table(
        "assignment_classes",
        sa.Column("id", u, primary_key=True),
        sa.Column(
            "assignment_id", u, sa.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("class_id", u, sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("assignment_id", "class_id", name="uq_assignment_class"),
    )
    op.create_index("ix_assignment_classes_assignment_id", "assignment_classes", ["assignment_id"])
    op.create_index("ix_assignment_classes_class_id", "assignment_classes", ["class_id"])
    op.create_table(
        "paper_versions",
        sa.Column("id", u, primary_key=True),
        sa.Column(
            "assignment_id", u, sa.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", version_status, nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("created_by", u, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("assignment_id", "version", name="uq_paper_version"),
    )
    op.create_index("ix_paper_versions_assignment_id", "paper_versions", ["assignment_id"])
    op.create_index("ix_paper_versions_status", "paper_versions", ["status"])
    op.create_table(
        "paper_pages",
        sa.Column("id", u, primary_key=True),
        sa.Column(
            "paper_version_id",
            u,
            sa.ForeignKey("paper_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stored_file_id",
            u,
            sa.ForeignKey("stored_files.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_page_number", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("preview_storage_key", sa.String(512)),
        sa.Column("thumbnail_storage_key", sa.String(512)),
        *ts(),
        sa.UniqueConstraint("paper_version_id", "page_number", name="uq_paper_page_number"),
    )
    for c in ["paper_version_id", "stored_file_id", "status"]:
        op.create_index(f"ix_paper_pages_{c}", "paper_pages", [c])
    op.create_table(
        "questions",
        sa.Column("id", u, primary_key=True),
        sa.Column(
            "paper_version_id",
            u,
            sa.ForeignKey("paper_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_question_id", u, sa.ForeignKey("questions.id", ondelete="SET NULL")),
        sa.Column("question_number", sa.String(40), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(30), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("content_latex", sa.Text()),
        sa.Column("max_score", sa.Numeric(10, 2), nullable=False),
        sa.Column("difficulty", sa.String(20)),
        sa.Column("status", question_status, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        *ts(),
    )
    for c in ["paper_version_id", "parent_question_id", "display_order", "status"]:
        op.create_index(f"ix_questions_{c}", "questions", [c])
    op.create_table(
        "question_regions",
        sa.Column("id", u, primary_key=True),
        sa.Column(
            "question_id", u, sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "paper_page_id", u, sa.ForeignKey("paper_pages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("region_type", sa.String(30), nullable=False),
        sa.Column("x", sa.Numeric(8, 6), nullable=False),
        sa.Column("y", sa.Numeric(8, 6), nullable=False),
        sa.Column("width", sa.Numeric(8, 6), nullable=False),
        sa.Column("height", sa.Numeric(8, 6), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5)),
        *ts(),
    )
    op.create_index("ix_question_regions_question_id", "question_regions", ["question_id"])
    op.create_index("ix_question_regions_paper_page_id", "question_regions", ["paper_page_id"])
    op.create_table(
        "rubric_versions",
        sa.Column("id", u, primary_key=True),
        sa.Column(
            "assignment_id", u, sa.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", version_status, nullable=False),
        sa.Column("created_by", u, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("assignment_id", "version", name="uq_rubric_version"),
    )
    op.create_index("ix_rubric_versions_assignment_id", "rubric_versions", ["assignment_id"])
    op.create_index("ix_rubric_versions_status", "rubric_versions", ["status"])
    op.create_table(
        "question_rubrics",
        sa.Column("id", u, primary_key=True),
        sa.Column(
            "rubric_version_id",
            u,
            sa.ForeignKey("rubric_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id", u, sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("standard_answer", sa.Text()),
        sa.Column("alternative_answers", sa.JSON(), nullable=False),
        sa.Column("scoring_notes", sa.Text()),
        sa.Column("allow_step_score", sa.Boolean(), nullable=False),
        sa.Column("unit_requirement", sa.Text()),
        sa.Column("format_requirement", sa.Text()),
        sa.Column("precision_requirement", sa.Text()),
        *ts(),
        sa.UniqueConstraint("rubric_version_id", "question_id", name="uq_question_rubric"),
    )
    op.create_index(
        "ix_question_rubrics_rubric_version_id", "question_rubrics", ["rubric_version_id"]
    )
    op.create_index("ix_question_rubrics_question_id", "question_rubrics", ["question_id"])
    op.create_table(
        "rubric_items",
        sa.Column("id", u, primary_key=True),
        sa.Column(
            "question_rubric_id",
            u,
            sa.ForeignKey("question_rubrics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("points", sa.Numeric(10, 2), nullable=False),
        sa.Column("item_type", sa.String(30), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("deduction_rule", sa.Text()),
        *ts(),
    )
    op.create_index("ix_rubric_items_question_rubric_id", "rubric_items", ["question_rubric_id"])
    op.create_table(
        "knowledge_points",
        sa.Column("id", u, primary_key=True),
        sa.Column("owner_id", u, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("subject", sa.String(40)),
        sa.Column("grade", sa.String(40)),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("parent_id", u, sa.ForeignKey("knowledge_points.id", ondelete="SET NULL")),
        *ts(),
        sa.UniqueConstraint("owner_id", "subject", "grade", "name", name="uq_knowledge_point"),
    )
    op.create_index("ix_knowledge_points_owner_id", "knowledge_points", ["owner_id"])
    op.create_table(
        "question_knowledge_points",
        sa.Column(
            "question_id", u, sa.ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "knowledge_point_id",
            u,
            sa.ForeignKey("knowledge_points.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "recognition_jobs",
        sa.Column("id", u, primary_key=True),
        sa.Column("owner_id", u, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "paper_version_id",
            u,
            sa.ForeignKey("paper_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", recognition_status, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("candidate_result", postgresql.JSONB(), nullable=False),
        sa.Column("error_message", sa.Text()),
        *ts(),
    )
    for c in ["owner_id", "paper_version_id", "status"]:
        op.create_index(f"ix_recognition_jobs_{c}", "recognition_jobs", [c])
    op.create_foreign_key(
        "fk_assignments_active_paper",
        "assignments",
        "paper_versions",
        ["active_paper_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assignments_active_rubric",
        "assignments",
        "rubric_versions",
        ["active_rubric_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_assignments_active_rubric", "assignments", type_="foreignkey")
    op.drop_constraint("fk_assignments_active_paper", "assignments", type_="foreignkey")
    for table in [
        "recognition_jobs",
        "question_knowledge_points",
        "knowledge_points",
        "rubric_items",
        "question_rubrics",
        "rubric_versions",
        "question_regions",
        "questions",
        "paper_pages",
        "paper_versions",
        "assignment_classes",
        "assignments",
    ]:
        op.drop_table(table)
    for name in ["recognitionstatus", "questionstatus", "versionstatus", "assignmentstatus"]:
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
