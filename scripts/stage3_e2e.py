"""Create one explicitly synthetic stage3_e2e validation chain in an isolated database."""

import hashlib
import json
import time
import uuid

from app.db.session import SessionLocal
from app.math_validation.engine import ENGINE_VERSION
from app.models import (
    MathValidationJob,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    StudentAnswer,
    Submission,
    SubmissionRecognitionJob,
    now_utc,
)
from sqlalchemy import select

from workers.tasks.math_validation import run_math_validation


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def main() -> None:
    marker = f"stage3_e2e_{uuid.uuid4().hex[:10]}"
    with SessionLocal() as db:
        answer = db.scalar(select(StudentAnswer).order_by(StudentAnswer.created_at))
        if answer is None:
            raise RuntimeError("isolated E2E database has no student answer")
        submission = db.get(Submission, answer.submission_id)
        if submission is None:
            raise RuntimeError("submission missing")
        recognition = SubmissionRecognitionJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            status="completed",
            provider="stage3_e2e",
            provider_version="synthetic-v1",
            idempotency_key=marker,
            input_hash=digest(marker),
            output_hash=digest([marker, "output"]),
            completed_at=now_utc(),
        )
        db.add(recognition)
        db.flush()
        evidence = QuestionRecognitionEvidence(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            student_answer_id=answer.id,
            recognition_job_id=recognition.id,
            status="confirmed",
            block_sources=[{"structured_values": {"exact_result": "1/2"}, "marker": marker}],
            normalized_text="1/2",
            provider_versions={"stage3_e2e": "synthetic-v1"},
            input_hash=digest([marker, "evidence"]),
            output_hash=digest([marker, "1/2"]),
            recognition_version=1,
            confirmed_revision=1,
            requires_review=False,
            confirmed_by=submission.owner_id,
            confirmed_at=now_utc(),
        )
        db.add(evidence)
        db.flush()
        reference = ReferenceAnswerVersion(
            question_id=answer.question_id,
            source_type="teacher_authored",
            raw_content="1/2",
            normalized_content="1/2",
            structured_content={"criteria": {"exact_result": "2/4"}},
            content_hash=digest([marker, "reference"]),
            version=1,
            provenance={"marker": marker, "synthetic": True},
            created_by=submission.owner_id,
            status="confirmed",
            teacher_confirmed_at=now_utc(),
        )
        db.add(reference)
        db.flush()
        rubric = StructuredRubricVersion(
            question_id=answer.question_id,
            question_version=answer.question_version_reference,
            reference_answer_version_id=reference.id,
            rubric_version=1,
            title="stage3_e2e exact rational",
            total_points=5,
            status="confirmed",
            content_hash=digest([marker, "rubric"]),
            created_by=submission.owner_id,
            confirmed_by=submission.owner_id,
            confirmed_at=now_utc(),
        )
        db.add(rubric)
        db.flush()
        criterion = RubricCriterion(
            rubric_version_id=rubric.id,
            stable_key="exact_result",
            title="Exact rational result",
            max_points=5,
            display_order=0,
            criterion_type="final_answer",
            required=True,
            validation_mode="deterministic",
            validation_rule={
                "answer_type": "exact_scalar",
                "domain": "rational",
                "tolerance": 0,
                "limits": {"timeout_ms": 500},
            },
        )
        db.add(criterion)
        db.flush()
        job = MathValidationJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            question_id=answer.question_id,
            student_answer_id=answer.id,
            recognition_evidence_id=evidence.id,
            scoring_input_version=f"{evidence.input_hash}:1:1",
            reference_answer_version_id=reference.id,
            rubric_version_id=rubric.id,
            engine_version=ENGINE_VERSION,
            config_version="safe-math-limits-v1",
            idempotency_key=marker,
            input_hash=digest([evidence.input_hash, rubric.content_hash, reference.content_hash]),
        )
        db.add(job)
        db.commit()
        job_id = str(job.id)
    async_result = run_math_validation.delay(job_id, 1)
    for _ in range(30):
        time.sleep(0.5)
        with SessionLocal() as db:
            current = db.get(MathValidationJob, uuid.UUID(job_id))
            if current and current.status in {"completed", "failed", "stale"}:
                print(
                    json.dumps(
                        {
                            "marker": marker,
                            "job_id": job_id,
                            "task_id": async_result.id,
                            "status": current.status,
                            "engine_version": current.engine_version,
                            "scoring_input_version": current.scoring_input_version,
                        }
                    )
                )
                if current.status != "completed":
                    raise RuntimeError(f"validation ended as {current.status}")
                return
    raise TimeoutError(f"validation job {job_id} did not finish")


if __name__ == "__main__":
    main()
