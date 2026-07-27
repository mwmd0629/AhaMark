"""freeze the recognition evidence input-version contract

Revision ID: 0014_recognition_scoring_input_version
Revises: 0013_answer_recognition_evidence
"""

from collections.abc import Sequence

revision: str = "0014_recognition_scoring_input_version"
down_revision: str | None = "0013_answer_recognition_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # QuestionRecognitionEvidence already owns the immutable input hash,
    # recognition version, confirmation revision, and source block references.
    # Keeping that contract on the evidence object avoids coupling this phase to
    # a future grading implementation.
    pass


def downgrade() -> None:
    pass
