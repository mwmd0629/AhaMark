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
    Assignment,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AssignmentReviewSession,
    CriterionValidationResult,
    MathValidationJob,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricSet,
    StructuredRubricSetItem,
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
    db, _storage, _batch_id, submission_id, question_id = workflow(
        criterion_validation_mode="deterministic"
    )
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
    assignment = db.get(Assignment, submission.assignment_id)
    assert assignment is not None and assignment.active_structured_rubric_set_id is not None
    set_item = db.scalar(
        select(StructuredRubricSetItem).where(
            StructuredRubricSetItem.rubric_set_id
            == assignment.active_structured_rubric_set_id,
            StructuredRubricSetItem.question_id == question_uuid,
        )
    )
    assert set_item is not None
    reference = db.get(ReferenceAnswerVersion, set_item.reference_answer_version_id)
    rubric = db.get(StructuredRubricVersion, set_item.structured_rubric_version_id)
    criterion = db.scalar(
        select(RubricCriterion).where(RubricCriterion.rubric_version_id == rubric.id)
    ) if rubric is not None else None
    assert reference is not None and rubric is not None and criterion is not None
    answer.question_version_reference = rubric.question_version
    generation_job = AssignmentGenerationJob(
        owner_id=submission.owner_id,
        assignment_id=assignment.id,
        generation=1,
        status="completed",
        current_stage="validating",
        progress=100,
        idempotency_key=uuid.uuid4().hex,
        request_fingerprint="g" * 64,
        source_snapshot_hash="s" * 64,
        provider_config_version="fixture-v1",
        prompt_version="fixture-v1",
        schema_version="fixture-v1",
    )
    db.add(generation_job)
    db.flush()
    revision = AssignmentDraftRevision(
        owner_id=submission.owner_id,
        assignment_id=assignment.id,
        generation_job_id=generation_job.id,
        revision=1,
        source_snapshot_hash=generation_job.source_snapshot_hash,
        created_by_type="teacher",
        created_by=submission.owner_id,
    )
    db.add(revision)
    db.flush()
    rubric_set = db.get(StructuredRubricSet, assignment.active_structured_rubric_set_id)
    assert rubric_set is not None
    generation_job.source_snapshot_hash = rubric_set.source_snapshot_hash
    revision.source_snapshot_hash = rubric_set.source_snapshot_hash
    review = AssignmentReviewSession(
        owner_id=submission.owner_id,
        assignment_id=assignment.id,
        generation_job_id=generation_job.id,
        draft_revision_id=revision.id,
        generation=1,
        source_snapshot_hash=generation_job.source_snapshot_hash,
        review_version=1,
        status="published",
        risk_ledger_hash="r" * 64,
        expected_assignment_updated_at=assignment.updated_at,
        paper_version_id=assignment.active_paper_version_id,
        structured_set_hash=rubric_set.content_hash,
        structured_rubric_set_id=assignment.active_structured_rubric_set_id,
        created_by=submission.owner_id,
    )
    db.add(review)
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
        structured_rubric_set_id=assignment.active_structured_rubric_set_id,
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
