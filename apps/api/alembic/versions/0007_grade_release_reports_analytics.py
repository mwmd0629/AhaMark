"""immutable grade releases, reports, analytics, and teaching insights

Revision ID: 0007_grade_release_reports_analytics
Revises: 0006_submissions_grading_review
"""

from alembic import op
from app import models as _models  # noqa: F401
from app.db.base import Base
from sqlalchemy.schema import CreateIndex, CreateTable

revision = "0007_grade_release_reports_analytics"
down_revision = "0006_submissions_grading_review"
branch_labels = None
depends_on = None

TABLES = [
    "grade_releases",
    "grade_release_items",
    "report_jobs",
    "analytics_snapshots",
    "teaching_insights",
]


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        table = Base.metadata.tables[name]
        bind.execute(CreateTable(table))
        for index in table.indexes:
            bind.execute(CreateIndex(index))


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
