"""submission collection, grading, review, and immutable score snapshots

Revision ID: 0006_submissions_grading_review
Revises: 0005_nullable_question_score
"""

from collections.abc import Iterable

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_submissions_grading_review"
down_revision = "0005_nullable_question_score"
branch_labels = None
depends_on = None

TABLES = [
    "grading_batches",
    "submissions",
    "submission_file_matches",
    "submission_pages",
    "student_answers",
    "student_answer_regions",
    "submission_recognition_jobs",
    "grading_jobs",
    "grading_results",
    "grading_criterion_results",
    "grading_evidence",
    "teacher_reviews",
    "score_revisions",
    "submission_score_snapshots",
]

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _id_column() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _create_table(
    name: str,
    *elements: sa.Column | sa.Constraint,
    indexes: Iterable[str] = (),
) -> None:
    """Create the revision-0006 schema without consulting live ORM metadata."""

    op.create_table(name, *elements)
    for column in indexes:
        op.create_index(f"ix_{name}_{column}", name, [column])


def upgrade() -> None:
    _create_table(
        "grading_batches",
        _id_column(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("submission_count", sa.Integer(), nullable=False),
        sa.Column("recognized_count", sa.Integer(), nullable=False),
        sa.Column("graded_count", sa.Integer(), nullable=False),
        sa.Column("reviewed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        indexes=("owner_id", "assignment_id", "class_id", "status"),
    )

    _create_table(
        "submissions",
        _id_column(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("grading_batch_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recognized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grading_batch_id"], ["grading_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "grading_batch_id", "student_id", "attempt_number", name="uq_submission_attempt"
        ),
        indexes=(
            "owner_id",
            "grading_batch_id",
            "assignment_id",
            "class_id",
            "student_id",
            "status",
        ),
    )

    _create_table(
        "submission_file_matches",
        _id_column(),
        sa.Column("grading_batch_id", sa.Uuid(), nullable=False),
        sa.Column("stored_file_id", sa.Uuid(), nullable=False),
        sa.Column("suggested_student_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_student_id", sa.Uuid(), nullable=True),
        sa.Column("match_method", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["grading_batch_id"], ["grading_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["stored_file_id"], ["stored_files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["suggested_student_id"], ["students.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_student_id"], ["students.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("stored_file_id"),
        indexes=("grading_batch_id", "status"),
    )

    _create_table(
        "submission_pages",
        _id_column(),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("stored_file_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_page_number", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("rendered_storage_key", sa.String(length=512), nullable=True),
        sa.Column("processed_storage_key", sa.String(length=512), nullable=True),
        sa.Column("thumbnail_storage_key", sa.String(length=512), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["stored_file_id"], ["stored_files.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("submission_id", "page_number", name="uq_submission_page"),
        indexes=("submission_id", "stored_file_id", "status"),
    )

    _create_table(
        "student_answers",
        _id_column(),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("question_version_reference", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("recognized_text", sa.Text(), nullable=True),
        sa.Column("recognized_latex", sa.Text(), nullable=True),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("corrected_latex", sa.Text(), nullable=True),
        sa.Column("recognition_confidence", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("recognition_provider", sa.String(length=80), nullable=True),
        sa.Column("recognition_provider_version", sa.String(length=80), nullable=True),
        sa.Column("is_blank", sa.Boolean(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("submission_id", "question_id", name="uq_submission_question_answer"),
        indexes=("submission_id", "question_id", "status", "requires_review"),
    )

    _create_table(
        "student_answer_regions",
        _id_column(),
        sa.Column("student_answer_id", sa.Uuid(), nullable=False),
        sa.Column("submission_page_id", sa.Uuid(), nullable=False),
        sa.Column("region_type", sa.String(length=30), nullable=False),
        sa.Column("x", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("y", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("width", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("height", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=6, scale=5), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["student_answer_id"], ["student_answers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["submission_page_id"], ["submission_pages.id"], ondelete="RESTRICT"
        ),
        indexes=("student_answer_id", "submission_page_id"),
    )

    _create_table(
        "submission_recognition_jobs",
        _id_column(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("provider_version", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_submission_recognition_idempotency"
        ),
        indexes=("owner_id", "submission_id", "status"),
    )

    _create_table(
        "grading_jobs",
        _id_column(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("grading_batch_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=True),
        sa.Column("rubric_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("provider_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("config_version", sa.String(length=80), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grading_batch_id"], ["grading_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rubric_version_id"], ["rubric_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_grading_idempotency"),
        indexes=("owner_id", "grading_batch_id", "submission_id", "question_id", "status"),
    )

    _create_table(
        "grading_results",
        _id_column(),
        sa.Column("grading_job_id", sa.Uuid(), nullable=False),
        sa.Column("student_answer_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("rubric_version_id", sa.Uuid(), nullable=False),
        sa.Column("grading_method", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("provider_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("max_score", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("recognized_answer_snapshot", sa.Text(), nullable=True),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.Column("student_feedback", sa.Text(), nullable=True),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["grading_job_id"], ["grading_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_answer_id"], ["student_answers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rubric_version_id"], ["rubric_versions.id"], ondelete="RESTRICT"),
        indexes=(
            "grading_job_id",
            "student_answer_id",
            "question_id",
            "rubric_version_id",
            "requires_review",
            "status",
        ),
    )

    _create_table(
        "grading_criterion_results",
        _id_column(),
        sa.Column("grading_result_id", sa.Uuid(), nullable=False),
        sa.Column("rubric_item_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("awarded_points", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("max_points", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["grading_result_id"], ["grading_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rubric_item_id"], ["rubric_items.id"], ondelete="RESTRICT"),
        indexes=("grading_result_id",),
    )

    _create_table(
        "grading_evidence",
        _id_column(),
        sa.Column("grading_result_id", sa.Uuid(), nullable=False),
        sa.Column("student_answer_id", sa.Uuid(), nullable=False),
        sa.Column("submission_page_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_type", sa.String(length=30), nullable=False),
        sa.Column("quote", sa.String(length=500), nullable=True),
        sa.Column("x", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("y", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("width", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("height", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["grading_result_id"], ["grading_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_answer_id"], ["student_answers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["submission_page_id"], ["submission_pages.id"], ondelete="RESTRICT"
        ),
        indexes=("grading_result_id",),
    )

    _create_table(
        "teacher_reviews",
        _id_column(),
        sa.Column("grading_result_id", sa.Uuid(), nullable=True),
        sa.Column("student_answer_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("final_score", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("final_feedback", sa.Text(), nullable=True),
        sa.Column("final_error_type", sa.String(length=80), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["grading_result_id"], ["grading_results.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_answer_id"], ["student_answers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("student_answer_id", name="uq_answer_review"),
        indexes=("student_answer_id",),
    )

    _create_table(
        "score_revisions",
        _id_column(),
        sa.Column("teacher_review_id", sa.Uuid(), nullable=False),
        sa.Column("student_answer_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("previous_score", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("new_score", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("previous_feedback", sa.Text(), nullable=True),
        sa.Column("new_feedback", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["teacher_review_id"], ["teacher_reviews.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_answer_id"], ["student_answers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        indexes=("teacher_review_id", "student_answer_id"),
    )

    _create_table(
        "submission_score_snapshots",
        _id_column(),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("paper_version_id", sa.Uuid(), nullable=False),
        sa.Column("rubric_version_id", sa.Uuid(), nullable=False),
        sa.Column("total_score", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("max_score", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("details", JSON_DOCUMENT, nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rubric_version_id"], ["rubric_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["generated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("submission_id", "version", name="uq_snapshot_version"),
        indexes=("submission_id", "assignment_id", "student_id", "status"),
    )


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
