from app.integrations.openai_client import canonical_json_hash


def student_learning_source_hash(
    *,
    student_id: str,
    released_snapshots: list[dict[str, object]],
    resource_versions: list[dict[str, object]] | None = None,
) -> str:
    """Build the cache key used when scheduling a personal analysis.

    Each item should contain a grade release id, immutable score snapshot id and
    snapshot version. The caller should sort the list by release id first.
    """

    return canonical_json_hash(
        {
            "student_id": student_id,
            "released_snapshots": released_snapshots,
            "resource_versions": resource_versions or [],
            "schema_version": "student-learning-input-v2",
        }
    )
