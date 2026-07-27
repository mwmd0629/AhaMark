"""Run a synthetic fake-provider Stage 4 chain without changing formal grades."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from decimal import Decimal

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import (
    AICriterionSuggestion,
    AIScoringJob,
    AISuggestionReview,
    GradeRelease,
    MathValidationJob,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    StructuredRubricVersion,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
)
from sqlalchemy import func, select

from workers.tasks.ai_grading import run_ai_grading


def digest(rows: object) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def protected_hashes(db: object) -> dict[str, str]:
    reviews = db.execute(  # type: ignore[attr-defined]
        select(TeacherReview.id, TeacherReview.final_score).order_by(TeacherReview.id)
    ).all()
    snapshots = db.execute(  # type: ignore[attr-defined]
        select(
            SubmissionScoreSnapshot.id,
            SubmissionScoreSnapshot.total_score,
            SubmissionScoreSnapshot.version,
        ).order_by(SubmissionScoreSnapshot.id)
    ).all()
    releases = db.execute(  # type: ignore[attr-defined]
        select(GradeRelease.id, GradeRelease.status).order_by(GradeRelease.id)
    ).all()
    return {
        "teacher_final_scores": digest(reviews),
        "score_snapshots": digest(snapshots),
        "grade_releases": digest(releases),
    }


def main() -> None:
    settings = get_settings()
    if settings.ai_grading_provider != "fake":
        raise RuntimeError("stage4_e2e requires the deterministic fake provider")
    with SessionLocal() as db:
        answer = db.scalar(
            select(StudentAnswer)
            .join(Submission, Submission.id == StudentAnswer.submission_id)
            .join(
                QuestionRecognitionEvidence,
                QuestionRecognitionEvidence.student_answer_id == StudentAnswer.id,
            )
            .where(
                Submission.finalized_at.is_(None),
                QuestionRecognitionEvidence.status == "confirmed",
                QuestionRecognitionEvidence.stale_at.is_(None),
            )
            .order_by(QuestionRecognitionEvidence.created_at.desc())
        )
        if not answer:
            raise RuntimeError("no non-finalized student answer")
        submission = db.get(Submission, answer.submission_id)
        evidence = db.scalar(
            select(QuestionRecognitionEvidence)
            .where(
                QuestionRecognitionEvidence.student_answer_id == answer.id,
                QuestionRecognitionEvidence.status == "confirmed",
                QuestionRecognitionEvidence.stale_at.is_(None),
            )
            .order_by(QuestionRecognitionEvidence.recognition_version.desc())
        )
        rubric = db.scalar(
            select(StructuredRubricVersion)
            .where(
                StructuredRubricVersion.question_id == answer.question_id,
                StructuredRubricVersion.status == "confirmed",
            )
            .order_by(StructuredRubricVersion.rubric_version.desc())
        )
        if not submission or not evidence or not rubric:
            raise RuntimeError("stage3 confirmed input is missing")
        reference = db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
        if not reference or reference.status != "confirmed":
            raise RuntimeError("confirmed reference answer is missing")
        validation = db.scalar(
            select(MathValidationJob)
            .where(
                MathValidationJob.student_answer_id == answer.id,
                MathValidationJob.rubric_version_id == rubric.id,
                MathValidationJob.stale_at.is_(None),
            )
            .order_by(MathValidationJob.generation.desc())
        )
        before = protected_hashes(db)
        generation = (
            db.scalar(
                select(func.max(AIScoringJob.generation)).where(
                    AIScoringJob.student_answer_id == answer.id
                )
            )
            or 0
        ) + 1
        marker = f"stage4_e2e_{uuid.uuid4().hex[:12]}"
        request_hash = digest(
            [
                marker,
                evidence.input_hash,
                rubric.content_hash,
                reference.content_hash,
                str(validation.id) if validation else None,
            ]
        )
        job = AIScoringJob(
            owner_id=submission.owner_id,
            assignment_id=submission.assignment_id,
            question_id=answer.question_id,
            submission_id=submission.id,
            student_answer_id=answer.id,
            recognition_evidence_id=evidence.id,
            reference_answer_version_id=reference.id,
            rubric_version_id=rubric.id,
            math_validation_job_id=validation.id if validation else None,
            question_version=answer.question_version_reference,
            scoring_input_version=(
                f"{evidence.input_hash}:{evidence.recognition_version}:"
                f"{evidence.confirmed_revision or 0}"
            ),
            status="queued",
            idempotency_key=marker,
            generation=generation,
            attempt=0,
            provider="fake",
            model="test-v1",
            endpoint_mode="deterministic",
            prompt_version=settings.ai_grading_prompt_version,
            schema_version=settings.ai_grading_schema_version,
            provider_config_version=settings.ai_grading_config_version,
            grading_config_version=settings.ai_grading_config_version,
            request_hash=request_hash,
            image_count=0,
            image_bytes=0,
            retryable=False,
        )
        db.add(job)
        db.commit()
        task = run_ai_grading.delay(str(job.id), generation)
        job.celery_task_id = task.id
        db.commit()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            db.expire_all()
            current = db.get(AIScoringJob, job.id)
            if current and current.status in {
                "completed",
                "partially_completed",
                "abstained",
                "failed",
                "stale",
            }:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("AI grading worker timeout")
        suggestions = db.scalars(
            select(AICriterionSuggestion).where(AICriterionSuggestion.ai_scoring_job_id == job.id)
        ).all()
        actions = ["accepted", "modified", "rejected"]
        for index, suggestion in enumerate(suggestions[:3]):
            db.add(
                AISuggestionReview(
                    suggestion_id=suggestion.id,
                    reviewer_id=submission.owner_id,
                    action=actions[index],
                    original_points=suggestion.suggested_points,
                    selected_points=(Decimal("0.5") if actions[index] == "modified" else None),
                    reason=f"{marker}:{actions[index]}:audit-only",
                    scoring_input_version=job.scoring_input_version,
                    rubric_version_id=job.rubric_version_id,
                )
            )
        db.commit()
        after = protected_hashes(db)
        completed_job = db.get(AIScoringJob, job.id)
        assert completed_job is not None
        result = {
            "marker": marker,
            "job_id": str(job.id),
            "celery_task_id": task.id,
            "status": completed_job.status,
            "provider": "fake",
            "suggestion_count": len(suggestions),
            "all_abstained": all(row.suggested_points is None for row in suggestions),
            "protected_hashes_before": before,
            "protected_hashes_after": after,
            "protected_state_unchanged": before == after,
            "automatic_finalize": False,
            "automatic_snapshot": False,
            "automatic_grade_release": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
