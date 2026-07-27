"""Central review, explicit binding, and teacher-only publication readiness.

Revision ID: 0022_assignment_central_review_publish
Revises: 0021_assignment_answer_rubric_generation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_assignment_central_review_publish"
down_revision: str | None = "0021_assignment_answer_rubric_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid()
JSON = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "assignment_review_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "assignment_id",
            UUID,
            sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "generation_job_id",
            UUID,
            sa.ForeignKey("assignment_generation_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "draft_revision_id",
            UUID,
            sa.ForeignKey("assignment_draft_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("risk_ledger_hash", sa.String(64), nullable=False),
        sa.Column("blocking_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("info_count", sa.Integer(), nullable=False),
        sa.Column("expected_assignment_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "paper_version_id",
            UUID,
            sa.ForeignKey("paper_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("structured_binding_hash", sa.String(64), nullable=False),
        sa.Column(
            "legacy_rubric_version_id",
            UUID,
            sa.ForeignKey("rubric_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("review_version > 0", name="ck_assignment_review_version_positive"),
        sa.CheckConstraint("generation > 0", name="ck_assignment_review_generation_positive"),
    )
    op.create_index(
        "ix_assignment_review_sessions_owner_id", "assignment_review_sessions", ["owner_id"]
    )
    op.create_index(
        "ix_assignment_review_sessions_assignment_id",
        "assignment_review_sessions",
        ["assignment_id"],
    )
    op.create_index(
        "ix_assignment_review_sessions_status", "assignment_review_sessions", ["status"]
    )
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
    op.create_table(
        "assignment_review_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "review_session_id",
            UUID,
            sa.ForeignKey("assignment_review_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("section", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("field_name", sa.String(64)),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("issue_code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("eligibility", sa.Boolean(), nullable=False),
        sa.Column("teacher_action", sa.String(24)),
        sa.Column("teacher_note", sa.Text()),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "review_session_id", "issue_code", "source_hash", name="uq_review_item_source"
        ),
    )
    for column in ("review_session_id", "section", "severity", "status"):
        op.create_index(f"ix_assignment_review_items_{column}", "assignment_review_items", [column])
    op.create_table(
        "assignment_explicit_confirmations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "review_session_id",
            UUID,
            sa.ForeignKey("assignment_review_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assignment_id",
            UUID,
            sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("confirmation_type", sa.String(32), nullable=False),
        sa.Column("confirmed_value", JSON, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("confirmation_version", sa.Integer(), nullable=False),
        sa.Column(
            "confirmed_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("invalidation_reason", sa.String(160)),
        sa.UniqueConstraint(
            "review_session_id",
            "confirmation_type",
            "confirmation_version",
            name="uq_assignment_confirmation_version",
        ),
        sa.CheckConstraint(
            "confirmation_version > 0", name="ck_assignment_confirmation_version_positive"
        ),
    )
    for column in ("review_session_id", "assignment_id", "confirmation_type", "invalidated_at"):
        op.create_index(
            f"ix_assignment_explicit_confirmations_{column}",
            "assignment_explicit_confirmations",
            [column],
        )
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
    op.create_table(
        "assignment_publish_readiness_snapshots",
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
        ),
        sa.Column(
            "binding_id",
            UUID,
            sa.ForeignKey("assignment_rubric_publication_bindings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column(
            "draft_revision_id",
            UUID,
            sa.ForeignKey("assignment_draft_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("risk_ledger_hash", sa.String(64), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("assignment_state_hash", sa.String(64), nullable=False),
        sa.Column("class_ids", JSON, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_score", sa.Numeric(10, 2), nullable=False),
        sa.Column("issue_counts", JSON, nullable=False),
        sa.Column("readiness_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("owner_id", "assignment_id", "review_session_id", "status", "expires_at"):
        op.create_index(
            f"ix_assignment_publish_readiness_snapshots_{column}",
            "assignment_publish_readiness_snapshots",
            [column],
        )


def downgrade() -> None:
    op.drop_table("assignment_publish_readiness_snapshots")
    op.drop_table("assignment_rubric_publication_bindings")
    op.drop_table("assignment_explicit_confirmations")
    op.drop_table("assignment_review_items")
    op.drop_table("assignment_review_sessions")
