"""Make Structured Rubric sets the only runtime rubric authority.

Revision ID: 0034_structured_rubric_authority
Revises: 0033_joint_exam_class_authorization
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_structured_rubric_authority"
down_revision: str | None = "0033_joint_exam_class_authorization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.Uuid()
JSON = sa.JSON()
LEGACY_VERSION_STATUS_VALUES = (
    "draft",
    "processing",
    "ready",
    "confirmed",
    "superseded",
    "failed",
)


def _require_empty_tables(*table_names: str, direction: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Hold the lock through Alembic's transaction so no writer can insert rows
        # after the safety check and before the destructive DDL runs.
        bind.execute(sa.text(f"LOCK TABLE {', '.join(table_names)} IN ACCESS EXCLUSIVE MODE"))
    migration_context = getattr(op, "get_context", lambda: None)()
    if bool(getattr(migration_context, "as_sql", False)):
        if bind.dialect.name != "postgresql":
            raise RuntimeError("0034 offline safety SQL is only supported for PostgreSQL")
        for table_name in table_names:
            op.execute(
                sa.text(
                    "DO $$ BEGIN "
                    f"IF EXISTS (SELECT 1 FROM {table_name}) THEN "
                    f"RAISE EXCEPTION '0034 {direction} refuses non-empty {table_name}'; "
                    "END IF; END $$"
                )
            )
        return
    for table_name in table_names:
        count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        if count:
            raise RuntimeError(
                f"0034 {direction} refuses to discard {count} rows from {table_name}; "
                "AhaMark Structured-only requires a fresh database"
            )


def _create_legacy_tables_for_downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # 0003 created this type and paper_versions still uses it at 0034, so
        # downgrade must reuse the original type instead of attempting CREATE TYPE.
        version_status: sa.types.TypeEngine[Any] = postgresql.ENUM(
            *LEGACY_VERSION_STATUS_VALUES,
            name="versionstatus",
            create_type=False,
        )
    else:
        version_status = sa.Enum(*LEGACY_VERSION_STATUS_VALUES, name="versionstatus")
    op.create_table(
        "rubric_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "assignment_id",
            UUID,
            sa.ForeignKey("assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", version_status, nullable=False),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("assignment_id", "version", name="uq_rubric_version"),
    )
    for column in ("assignment_id", "status"):
        op.create_index(f"ix_rubric_versions_{column}", "rubric_versions", [column])
    op.create_table(
        "question_rubrics",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "rubric_version_id",
            UUID,
            sa.ForeignKey("rubric_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("standard_answer", sa.Text()),
        sa.Column("alternative_answers", JSON, nullable=False),
        sa.Column("scoring_notes", sa.Text()),
        sa.Column("allow_step_score", sa.Boolean(), nullable=False),
        sa.Column("unit_requirement", sa.Text()),
        sa.Column("format_requirement", sa.Text()),
        sa.Column("precision_requirement", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rubric_version_id", "question_id", name="uq_question_rubric"),
    )
    for column in ("rubric_version_id", "question_id"):
        op.create_index(f"ix_question_rubrics_{column}", "question_rubrics", [column])
    op.create_table(
        "rubric_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "question_rubric_id",
            UUID,
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rubric_items_question_rubric_id", "rubric_items", ["question_rubric_id"])
    op.create_table(
        "assignment_rubric_publication_bindings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "assignment_id",
            UUID,
            sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "review_session_id",
            UUID,
            sa.ForeignKey("assignment_review_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "paper_version_id",
            UUID,
            sa.ForeignKey("paper_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "legacy_rubric_version_id",
            UUID,
            sa.ForeignKey("rubric_versions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source_binding_hash", sa.String(64), nullable=False),
        sa.Column("source_semantic_hash", sa.String(64)),
        sa.Column("target_legacy_hash", sa.String(64)),
        sa.Column("projection_profile", sa.String(64)),
        sa.Column("projection_version", sa.String(40)),
        sa.Column("loss_report", JSON),
        sa.Column("loss_report_hash", sa.String(64)),
        sa.Column("mapping", JSON, nullable=False),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "assignment_id", "binding_version", name="uq_assignment_binding_version"
        ),
        sa.UniqueConstraint(
            "review_session_id", "source_binding_hash", name="uq_review_binding_source"
        ),
        sa.CheckConstraint("binding_version > 0", name="ck_assignment_binding_version_positive"),
    )
    for column in ("owner_id", "assignment_id", "review_session_id", "status"):
        op.create_index(
            f"ix_assignment_rubric_publication_bindings_{column}",
            "assignment_rubric_publication_bindings",
            [column],
        )


def upgrade() -> None:
    _require_empty_tables(
        "rubric_versions",
        "assignment_rubric_publication_bindings",
        "assignment_review_sessions",
        "math_validation_jobs",
        "ai_scoring_jobs",
        direction="upgrade",
    )

    op.create_table(
        "structured_rubric_sets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "assignment_id",
            UUID,
            sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "paper_version_id",
            UUID,
            sa.ForeignKey("paper_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("total_points", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("assignment_id", "version", name="uq_structured_rubric_set_version"),
        sa.UniqueConstraint("assignment_id", "content_hash", name="uq_structured_rubric_set_hash"),
        sa.CheckConstraint("version > 0", name="ck_structured_rubric_set_version_positive"),
    )
    for column in ("owner_id", "assignment_id", "paper_version_id", "status", "content_hash"):
        op.create_index(f"ix_structured_rubric_sets_{column}", "structured_rubric_sets", [column])
    op.create_table(
        "structured_rubric_set_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "rubric_set_id",
            UUID,
            sa.ForeignKey("structured_rubric_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id", UUID, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("question_version", sa.String(100), nullable=False),
        sa.Column(
            "reference_answer_version_id",
            UUID,
            sa.ForeignKey("reference_answer_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "structured_rubric_version_id",
            UUID,
            sa.ForeignKey("structured_rubric_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("answer_content_hash", sa.String(64), nullable=False),
        sa.Column("rubric_content_hash", sa.String(64), nullable=False),
        sa.Column("criteria_hash", sa.String(64), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("max_points", sa.Numeric(10, 2), nullable=False),
        sa.UniqueConstraint("rubric_set_id", "question_id", name="uq_structured_set_question"),
        sa.UniqueConstraint("rubric_set_id", "display_order", name="uq_structured_set_order"),
    )
    for column in (
        "rubric_set_id",
        "question_id",
        "reference_answer_version_id",
        "structured_rubric_version_id",
    ):
        op.create_index(
            f"ix_structured_rubric_set_items_{column}", "structured_rubric_set_items", [column]
        )

    with op.batch_alter_table("assignments") as batch:
        batch.drop_constraint("fk_assignments_active_rubric", type_="foreignkey")
        batch.drop_column("active_rubric_version_id")
        batch.add_column(sa.Column("active_structured_rubric_set_id", UUID))
        batch.create_foreign_key(
            "fk_assignments_active_structured_rubric_set",
            "structured_rubric_sets",
            ["active_structured_rubric_set_id"],
            ["id"],
            ondelete="RESTRICT",
            use_alter=True,
        )

    with op.batch_alter_table("grading_jobs") as batch:
        batch.drop_column("rubric_version_id")
        batch.add_column(sa.Column("structured_rubric_set_id", UUID, nullable=False))
        batch.create_foreign_key(
            "fk_grading_jobs_structured_rubric_set",
            "structured_rubric_sets",
            ["structured_rubric_set_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.add_column(sa.Column("structured_rubric_version_id", UUID, nullable=False))
        batch.create_foreign_key(
            "fk_grading_jobs_structured_rubric_version",
            "structured_rubric_versions",
            ["structured_rubric_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_grading_jobs_structured_rubric_set_id", ["structured_rubric_set_id"])
        batch.create_index(
            "ix_grading_jobs_structured_rubric_version_id", ["structured_rubric_version_id"]
        )
    with op.batch_alter_table("grading_results") as batch:
        batch.drop_index("ix_grading_results_rubric_version_id")
        batch.drop_column("rubric_version_id")
        batch.add_column(sa.Column("structured_rubric_set_id", UUID, nullable=False))
        batch.create_foreign_key(
            "fk_grading_results_structured_rubric_set",
            "structured_rubric_sets",
            ["structured_rubric_set_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.add_column(sa.Column("structured_rubric_version_id", UUID, nullable=False))
        batch.create_foreign_key(
            "fk_grading_results_structured_rubric_version",
            "structured_rubric_versions",
            ["structured_rubric_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_grading_results_structured_rubric_set_id", ["structured_rubric_set_id"]
        )
        batch.create_index(
            "ix_grading_results_structured_rubric_version_id", ["structured_rubric_version_id"]
        )
    with op.batch_alter_table("grading_criterion_results") as batch:
        batch.drop_column("rubric_item_id")
        batch.add_column(sa.Column("criterion_id", UUID, nullable=False))
        batch.create_foreign_key(
            "fk_grading_criterion_results_criterion",
            "rubric_criteria",
            ["criterion_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_grading_criterion_results_criterion_id", ["criterion_id"])
    with op.batch_alter_table("submission_score_snapshots") as batch:
        batch.drop_column("rubric_version_id")
        batch.add_column(sa.Column("structured_rubric_set_id", UUID, nullable=False))
        batch.create_foreign_key(
            "fk_submission_score_snapshots_structured_rubric_set",
            "structured_rubric_sets",
            ["structured_rubric_set_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_submission_score_snapshots_structured_rubric_set_id",
            ["structured_rubric_set_id"],
        )
    for table_name in ("math_validation_jobs", "ai_scoring_jobs"):
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(sa.Column("structured_rubric_set_id", UUID, nullable=False))
            batch.create_foreign_key(
                f"fk_{table_name}_structured_rubric_set",
                "structured_rubric_sets",
                ["structured_rubric_set_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch.create_index(
                f"ix_{table_name}_structured_rubric_set_id",
                ["structured_rubric_set_id"],
            )

    op.drop_index("uq_assignment_review_active", table_name="assignment_review_sessions")
    with op.batch_alter_table("assignment_review_sessions") as batch:
        batch.alter_column("structured_binding_hash", new_column_name="structured_set_hash")
        batch.drop_column("legacy_rubric_version_id")
        batch.add_column(sa.Column("structured_rubric_set_id", UUID))
        batch.create_foreign_key(
            "fk_assignment_review_sessions_structured_rubric_set",
            "structured_rubric_sets",
            ["structured_rubric_set_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "uq_assignment_review_active",
        "assignment_review_sessions",
        ["assignment_id"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('draft','in_review','changes_required','ready_for_set','ready_to_publish')"
        ),
        postgresql_where=sa.text(
            "status IN ('draft','in_review','changes_required','ready_for_set','ready_to_publish')"
        ),
    )
    with op.batch_alter_table("assignment_publish_readiness_snapshots") as batch:
        batch.drop_column("binding_id")
        batch.drop_column("legacy_rubric_version_id")
        batch.add_column(sa.Column("structured_rubric_set_id", UUID, nullable=False))
        batch.create_foreign_key(
            "fk_assignment_readiness_structured_rubric_set",
            "structured_rubric_sets",
            ["structured_rubric_set_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.drop_table("assignment_rubric_publication_bindings")
    op.drop_table("rubric_items")
    op.drop_table("question_rubrics")
    op.drop_table("rubric_versions")


def downgrade() -> None:
    _require_empty_tables(
        "structured_rubric_sets",
        "assignment_review_sessions",
        direction="downgrade",
    )
    _create_legacy_tables_for_downgrade()

    with op.batch_alter_table("assignment_publish_readiness_snapshots") as batch:
        batch.drop_column("structured_rubric_set_id")
        batch.add_column(sa.Column("legacy_rubric_version_id", UUID, nullable=False))
        batch.create_foreign_key(
            "assignment_publish_readiness_snap_legacy_rubric_version_id_fkey",
            "rubric_versions",
            ["legacy_rubric_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.add_column(sa.Column("binding_id", UUID, nullable=False))
        batch.create_foreign_key(
            "assignment_publish_readiness_snapshots_binding_id_fkey",
            "assignment_rubric_publication_bindings",
            ["binding_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.drop_index("uq_assignment_review_active", table_name="assignment_review_sessions")
    with op.batch_alter_table("assignment_review_sessions") as batch:
        batch.drop_column("structured_rubric_set_id")
        batch.add_column(sa.Column("legacy_rubric_version_id", UUID))
        batch.create_foreign_key(
            "assignment_review_sessions_legacy_rubric_version_id_fkey",
            "rubric_versions",
            ["legacy_rubric_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.alter_column("structured_set_hash", new_column_name="structured_binding_hash")
    op.create_index(
        "uq_assignment_review_active",
        "assignment_review_sessions",
        ["assignment_id"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('draft','in_review','changes_required',"
            "'ready_for_binding','ready_to_publish')"
        ),
        postgresql_where=sa.text(
            "status IN ('draft','in_review','changes_required',"
            "'ready_for_binding','ready_to_publish')"
        ),
    )
    for table_name in ("ai_scoring_jobs", "math_validation_jobs"):
        with op.batch_alter_table(table_name) as batch:
            batch.drop_index(f"ix_{table_name}_structured_rubric_set_id")
            batch.drop_constraint(f"fk_{table_name}_structured_rubric_set", type_="foreignkey")
            batch.drop_column("structured_rubric_set_id")
    with op.batch_alter_table("submission_score_snapshots") as batch:
        batch.drop_index("ix_submission_score_snapshots_structured_rubric_set_id")
        batch.drop_column("structured_rubric_set_id")
        batch.add_column(sa.Column("rubric_version_id", UUID, nullable=False))
        batch.create_foreign_key(
            "submission_score_snapshots_rubric_version_id_fkey",
            "rubric_versions",
            ["rubric_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("grading_criterion_results") as batch:
        batch.drop_index("ix_grading_criterion_results_criterion_id")
        batch.drop_column("criterion_id")
        batch.add_column(sa.Column("rubric_item_id", UUID, nullable=False))
        batch.create_foreign_key(
            "grading_criterion_results_rubric_item_id_fkey",
            "rubric_items",
            ["rubric_item_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("grading_results") as batch:
        batch.drop_index("ix_grading_results_structured_rubric_version_id")
        batch.drop_index("ix_grading_results_structured_rubric_set_id")
        batch.drop_column("structured_rubric_version_id")
        batch.drop_column("structured_rubric_set_id")
        batch.add_column(sa.Column("rubric_version_id", UUID, nullable=False))
        batch.create_foreign_key(
            "grading_results_rubric_version_id_fkey",
            "rubric_versions",
            ["rubric_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_grading_results_rubric_version_id", ["rubric_version_id"])
    with op.batch_alter_table("grading_jobs") as batch:
        batch.drop_index("ix_grading_jobs_structured_rubric_version_id")
        batch.drop_index("ix_grading_jobs_structured_rubric_set_id")
        batch.drop_column("structured_rubric_version_id")
        batch.drop_column("structured_rubric_set_id")
        batch.add_column(sa.Column("rubric_version_id", UUID, nullable=False))
        batch.create_foreign_key(
            "grading_jobs_rubric_version_id_fkey",
            "rubric_versions",
            ["rubric_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("assignments") as batch:
        batch.drop_constraint("fk_assignments_active_structured_rubric_set", type_="foreignkey")
        batch.drop_column("active_structured_rubric_set_id")
        batch.add_column(sa.Column("active_rubric_version_id", UUID))
        batch.create_foreign_key(
            "fk_assignments_active_rubric",
            "rubric_versions",
            ["active_rubric_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.drop_table("structured_rubric_set_items")
    op.drop_table("structured_rubric_sets")
