import uuid
from decimal import Decimal

import pytest
from app.math_validation.stale import (
    stale_for_answer,
    stale_for_engine_versions,
    stale_for_question,
    stale_for_reference,
    stale_for_rubric,
)
from app.models import (
    CriterionValidationResult,
    MathValidationJob,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    StudentAnswer,
    Submission,
    SubmissionRecognitionJob,
    TeacherReview,
)
from sqlalchemy import select
from test_submission_workflow import workflow

from workers.tasks.math_validation import run_math_validation


def validation_fixture() -> tuple[object, MathValidationJob, CriterionValidationResult]:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    question_uuid = uuid.UUID(str(question_id))
    answer = db.scalar(select(StudentAnswer).where(StudentAnswer.submission_id == submission_id))
    submission = db.get(Submission, submission_id)
    assert submission is not None
    if answer is None:
        answer = StudentAnswer(
            submission_id=submission_id,
            question_id=question_uuid,
            question_version_reference="test-v1",
        )
        db.add(answer)
        db.flush()
    reference = ReferenceAnswerVersion(
        question_id=question_uuid,
        source_type="teacher_authored",
        raw_content="1",
        normalized_content="1",
        content_hash="r" * 64,
        version=1,
        created_by=submission.owner_id,
        status="confirmed",
    )
    db.add(reference)
    db.flush()
    rubric = StructuredRubricVersion(
        question_id=question_uuid,
        question_version=answer.question_version_reference,
        reference_answer_version_id=reference.id,
        rubric_version=1,
        title="test",
        total_points=Decimal("5"),
        status="confirmed",
        content_hash="u" * 64,
        created_by=submission.owner_id,
    )
    db.add(rubric)
    db.flush()
    criterion = RubricCriterion(
        rubric_version_id=rubric.id,
        stable_key="result",
        title="result",
        max_points=Decimal("5"),
        display_order=0,
        criterion_type="final_answer",
        validation_mode="deterministic",
        validation_rule={
            "answer_type": "exact_scalar",
            "domain": "rational",
            "limits": {"timeout_ms": 500},
        },
    )
    db.add(criterion)
    db.flush()
    recognition_job = SubmissionRecognitionJob(
        owner_id=submission.owner_id,
        submission_id=submission.id,
        status="completed",
        provider="fake",
        provider_version="test-v1",
        idempotency_key=uuid.uuid4().hex,
        input_hash="e" * 64,
    )
    db.add(recognition_job)
    db.flush()
    recognition_evidence = QuestionRecognitionEvidence(
        owner_id=submission.owner_id,
        submission_id=submission.id,
        student_answer_id=answer.id,
        recognition_job_id=recognition_job.id,
        status="confirmed",
        normalized_text="1",
        input_hash="e" * 64,
        recognition_version=1,
        confirmed_revision=1,
        requires_review=False,
        confirmed_by=submission.owner_id,
    )
    db.add(recognition_evidence)
    db.flush()
    job = MathValidationJob(
        owner_id=submission.owner_id,
        submission_id=submission.id,
        question_id=question_uuid,
        student_answer_id=answer.id,
        recognition_evidence_id=recognition_evidence.id,
        scoring_input_version="evidence:1:1",
        reference_answer_version_id=reference.id,
        rubric_version_id=rubric.id,
        engine_version="ahamark-safe-math-2",
        config_version="safe-math-limits-v2",
        idempotency_key=uuid.uuid4().hex,
        input_hash="i" * 64,
        status="completed",
    )
    db.add(job)
    db.flush()
    result = CriterionValidationResult(
        validation_job_id=job.id,
        criterion_id=criterion.id,
        generation=1,
        result="verified_pass",
        suggested_points=Decimal("5"),
        confidence=Decimal("1"),
        comparison_method="exact_fraction",
        input_hash="a" * 64,
        output_hash="b" * 64,
        duration_ms=1,
        engine_version=job.engine_version,
    )
    db.add(result)
    db.commit()
    return db, job, result


def assert_stale(job: MathValidationJob, result: CriterionValidationResult, reason: str) -> None:
    assert job.status == "stale" and job.stale_at is not None
    assert job.error_code == reason and job.generation == 2
    assert result.result == "verified_pass" and result.stale_at is not None
    assert result.diagnostics["stale_reason"] == reason


@pytest.mark.parametrize("source", ["answer", "question", "reference", "rubric", "engine"])
def test_five_stale_sources_preserve_audit_results(source: str) -> None:
    db, job, result = validation_fixture()
    if source == "answer":
        stale_for_answer(db, job.student_answer_id, "RECOGNITION_REVISION_CHANGED")
        reason = "RECOGNITION_REVISION_CHANGED"
    elif source == "question":
        stale_for_question(db, job.question_id, "QUESTION_CONTENT_CHANGED")
        reason = "QUESTION_CONTENT_CHANGED"
    elif source == "reference":
        stale_for_reference(db, job.reference_answer_version_id, "REFERENCE_ANSWER_CONFIRMED")
        reason = "REFERENCE_ANSWER_CONFIRMED"
    elif source == "rubric":
        stale_for_rubric(db, job.rubric_version_id, "RUBRIC_VERSION_CONFIRMED")
        reason = "RUBRIC_VERSION_CONFIRMED"
    else:
        stale_for_engine_versions(db, "new-engine", "new-config")
        reason = "VALIDATION_ENGINE_OR_CONFIG_CHANGED"
    db.commit()
    assert_stale(job, result, reason)
    assert db.get(CriterionValidationResult, result.id) is not None
    db.close()


def test_late_worker_cannot_restore_stale_or_change_teacher_score() -> None:
    db, job, result = validation_fixture()
    review_before = db.scalar(select(TeacherReview).limit(1))
    score_before = review_before.final_score if review_before else None
    old_generation = job.generation
    stale_for_answer(db, job.student_answer_id, "SCORING_INPUT_CHANGED")
    db.commit()
    response = run_math_validation.run(str(job.id), old_generation)
    db.refresh(job)
    db.refresh(result)
    assert response["status"] == "discarded_late"
    assert_stale(job, result, "SCORING_INPUT_CHANGED")
    review_after = db.scalar(select(TeacherReview).limit(1))
    assert (review_after.final_score if review_after else None) == score_before
    db.close()
