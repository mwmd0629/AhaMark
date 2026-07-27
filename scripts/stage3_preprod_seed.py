"""Create one explicitly synthetic Stage 3 fixture, but no validation job."""

import hashlib
import json
import uuid

from app.db.session import SessionLocal
from app.models import (
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    StudentAnswer,
    Submission,
    SubmissionRecognitionJob,
    now_utc,
)
from sqlalchemy import func, select


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def main() -> None:
    marker = f"stage3_e2e_{uuid.uuid4().hex[:10]}"
    with SessionLocal() as db:
        answer = db.scalar(
            select(StudentAnswer)
            .join(Submission, Submission.id == StudentAnswer.submission_id)
            .where(Submission.status != "finalized")
            .order_by(StudentAnswer.created_at)
        )
        if answer is None:
            raise RuntimeError("isolated preproduction database has no student answer")
        submission = db.get(Submission, answer.submission_id)
        if submission is None:
            raise RuntimeError("submission missing")
        recognition = SubmissionRecognitionJob(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            status="completed",
            provider="stage3_e2e",
            provider_version="synthetic-v2",
            idempotency_key=marker,
            input_hash=digest([marker, "recognition"]),
            output_hash=digest([marker, "recognition-output"]),
            completed_at=now_utc(),
        )
        db.add(recognition)
        db.flush()
        next_evidence_version = (
            db.scalar(
                select(
                    func.coalesce(func.max(QuestionRecognitionEvidence.recognition_version), 0)
                ).where(QuestionRecognitionEvidence.student_answer_id == answer.id)
            )
            or 0
        ) + 1
        evidence = QuestionRecognitionEvidence(
            owner_id=submission.owner_id,
            submission_id=submission.id,
            student_answer_id=answer.id,
            recognition_job_id=recognition.id,
            status="confirmed",
            block_sources=[{"structured_values": {"exact_result": "1/2"}, "marker": marker}],
            normalized_text="1/2",
            provider_versions={"stage3_e2e": "synthetic-v2"},
            input_hash=digest([marker, "evidence"]),
            output_hash=digest([marker, "1/2"]),
            recognition_version=next_evidence_version,
            confirmed_revision=1,
            requires_review=False,
            confirmed_by=submission.owner_id,
            confirmed_at=now_utc(),
        )
        db.add(evidence)
        db.flush()
        next_reference = (
            db.scalar(
                select(ReferenceAnswerVersion.version)
                .where(ReferenceAnswerVersion.question_id == answer.question_id)
                .order_by(ReferenceAnswerVersion.version.desc())
            )
            or 0
        ) + 1
        reference = ReferenceAnswerVersion(
            question_id=answer.question_id,
            source_type="teacher_authored",
            raw_content="1/2",
            normalized_content="1/2",
            structured_content={"criteria": {"exact_result": "2/4"}},
            content_hash=digest([marker, "reference"]),
            version=next_reference,
            provenance={"marker": marker, "synthetic": True},
            created_by=submission.owner_id,
            status="confirmed",
            teacher_confirmed_at=now_utc(),
        )
        db.add(reference)
        db.flush()
        next_rubric = (
            db.scalar(
                select(StructuredRubricVersion.rubric_version)
                .where(StructuredRubricVersion.question_id == answer.question_id)
                .order_by(StructuredRubricVersion.rubric_version.desc())
            )
            or 0
        ) + 1
        rubric = StructuredRubricVersion(
            question_id=answer.question_id,
            question_version=answer.question_version_reference,
            reference_answer_version_id=reference.id,
            rubric_version=next_rubric,
            title="stage3_e2e HTTPS exact rational",
            total_points=5,
            status="confirmed",
            content_hash=digest([marker, "rubric"]),
            created_by=submission.owner_id,
            confirmed_by=submission.owner_id,
            confirmed_at=now_utc(),
        )
        db.add(rubric)
        db.flush()
        db.add(
            RubricCriterion(
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
        )
        db.commit()
        print(
            json.dumps(
                {
                    "marker": marker,
                    "synthetic": True,
                    "student_answer_id": str(answer.id),
                    "rubric_version_id": str(rubric.id),
                    "reference_answer_version_id": str(reference.id),
                    "recognition_evidence_id": str(evidence.id),
                    "submission_id": str(submission.id),
                    "question_id": str(answer.question_id),
                }
            )
        )


if __name__ == "__main__":
    main()
