from datetime import UTC

from app.models import Question


def question_version_token(question: Question) -> str:
    updated_at = question.updated_at
    normalized = (
        updated_at.replace(tzinfo=UTC) if updated_at.tzinfo is None else updated_at.astimezone(UTC)
    )
    return f"{question.paper_version_id}:{normalized.isoformat()}"
