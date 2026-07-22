"""submission collection, grading, review, and immutable score snapshots

Revision ID: 0006_submissions_grading_review
Revises: 0005_nullable_question_score
"""

from alembic import op
from app import models as _models  # noqa: F401 -- registers metadata
from app.db.base import Base
from sqlalchemy.schema import CreateIndex, CreateTable

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


def upgrade() -> None:
    for name in TABLES:
        table = Base.metadata.tables[name]
        op.execute(CreateTable(table))
        for index in table.indexes:
            op.execute(CreateIndex(index))


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
