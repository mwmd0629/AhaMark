import hashlib
import json


def wrong_question_job_input_hash(
    *,
    thread_id: str,
    score_snapshot_id: str,
    generation: int,
    content: str,
) -> str:
    """Hash the immutable fields fixed when a student queues a tutor turn."""

    # Keep this serialization stable and shared with the API transaction that
    # creates WrongQuestionAIJob.
    raw = json.dumps(
        {
            "thread_id": thread_id,
            "score_snapshot_id": score_snapshot_id,
            "generation": generation,
            "content": content,
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(raw).hexdigest()
