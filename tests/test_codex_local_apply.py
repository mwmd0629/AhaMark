from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from app.ai_grading.schema import AIGradingOutput, ValidationContext
from app.models import (
    AICriterionSuggestion,
    AIFeedbackDraft,
    AIScoringJob,
    Assignment,
    AuditLog,
    CodexWorkItem,
    GradeRelease,
    GradingCriterionResult,
    GradingEvidence,
    GradingJob,
    GradingResult,
    PaperVersion,
    ProcessingRun,
    ProcessingStep,
    Question,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricSet,
    StructuredRubricSetItem,
    StructuredRubricVersion,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
    VersionStatus,
    now_utc,
)
from app.processing import codex_local
from app.processing.codex_local import apply_work_item
from app.processing.contracts import canonical_hash, canonicalize
from app.processing.orchestrator import reconcile_processing
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_codex_local_work_items import _seed_step, _valid_response
from test_codex_local_work_items import db as sqlite_db

__all__ = ["sqlite_db"]


@pytest.fixture
def submitted_item(
    sqlite_db: Session, monkeypatch: pytest.MonkeyPatch
) -> tuple[CodexWorkItem, ProcessingRun, ProcessingStep]:
    db = sqlite_db
    run, step = _seed_step(db)
    run.submission_count = 1
    run.step_count = 1
    answer = db.get(StudentAnswer, step.student_answer_id)
    submission = db.get(Submission, step.submission_id)
    assert answer is not None and submission is not None
    assignment = db.get(Assignment, submission.assignment_id)
    assert assignment is not None
    paper = PaperVersion(
        assignment_id=assignment.id,
        version=1,
        status=VersionStatus.confirmed,
        source_type="manual",
        created_by=run.owner_id,
        confirmed_at=now_utc(),
    )
    db.add(paper)
    db.flush()
    question = Question(
        id=answer.question_id,
        paper_version_id=paper.id,
        question_number="1",
        display_order=1,
        question_type="short_answer",
        content_text="Synthetic question",
        max_score=Decimal("2"),
    )
    reference = ReferenceAnswerVersion(
        question_id=question.id,
        source_type="teacher_official",
        source_region={},
        raw_content="2",
        normalized_content="2",
        structured_content={},
        content_hash="1" * 64,
        version=1,
        provenance={},
        created_by=run.owner_id,
        status="confirmed",
    )
    db.add_all([question, reference])
    db.flush()
    rubric = StructuredRubricVersion(
        question_id=question.id,
        question_version="synthetic",
        reference_answer_version_id=reference.id,
        rubric_version=1,
        title="Synthetic rubric",
        total_points=Decimal("2"),
        status="confirmed",
        content_hash="2" * 64,
        created_by=run.owner_id,
    )
    db.add(rubric)
    db.flush()
    criterion = RubricCriterion(
        rubric_version_id=rubric.id,
        stable_key="criterion-1",
        title="Correct",
        max_points=Decimal("2"),
        display_order=1,
        criterion_type="correctness",
        required=True,
        dependencies=[],
        expected_evidence={},
        validation_mode="ai",
        validation_rule={},
        manual_review_policy={},
        partial_credit_policy={},
        metadata_={},
    )
    db.add(criterion)
    db.flush()
    rubric_set = StructuredRubricSet(
        owner_id=run.owner_id,
        assignment_id=assignment.id,
        paper_version_id=paper.id,
        version=1,
        status="active",
        content_hash="3" * 64,
        source_snapshot_hash="4" * 64,
        total_points=Decimal("2"),
        created_by=run.owner_id,
        confirmed_by=run.owner_id,
        confirmed_at=now_utc(),
        activated_at=now_utc(),
    )
    db.add(rubric_set)
    db.flush()
    set_item = StructuredRubricSetItem(
        rubric_set_id=rubric_set.id,
        question_id=question.id,
        question_version=rubric.question_version,
        reference_answer_version_id=reference.id,
        structured_rubric_version_id=rubric.id,
        answer_content_hash=reference.content_hash,
        rubric_content_hash=rubric.content_hash,
        criteria_hash="5" * 64,
        display_order=1,
        max_points=Decimal("2"),
    )
    region = StudentAnswerRegion(
        student_answer_id=answer.id,
        submission_page_id=uuid.uuid4(),
        x=Decimal("0.1"),
        y=Decimal("0.1"),
        width=Decimal("0.5"),
        height=Decimal("0.5"),
        status="confirmed",
        confirmed_by=run.owner_id,
        confirmed_at=now_utc(),
    )
    db.add_all([set_item, region])
    db.flush()
    evidence = QuestionRecognitionEvidence(
        owner_id=run.owner_id,
        submission_id=submission.id,
        student_answer_id=answer.id,
        recognition_job_id=uuid.uuid4(),
        status="confirmed",
        block_sources=[{"region_id": str(region.id)}],
        provider_versions={"synthetic": "1"},
        input_hash="6" * 64,
        output_hash="7" * 64,
        recognition_version=1,
        confirmed_revision=1,
        requires_review=False,
    )
    db.add(evidence)
    assignment.active_paper_version_id = paper.id
    assignment.active_structured_rubric_set_id = rubric_set.id
    answer.recognized_text = "2"
    request = canonicalize(
        {
            "schema": "codex-work-request-v1",
            "processing_input": {
                "payload": {
                    "formal": {
                        "structured_rubric": {"id": str(rubric.id)},
                        "reference_answer": {"id": str(reference.id)},
                    },
                    "structured_rubric_set": {
                        "id": str(rubric_set.id),
                        "version": rubric_set.version,
                        "content_hash": rubric_set.content_hash,
                        "item_id": str(set_item.id),
                        "answer_content_hash": set_item.answer_content_hash,
                        "rubric_content_hash": set_item.rubric_content_hash,
                        "criteria_hash": set_item.criteria_hash,
                    },
                }
            },
        }
    )
    raw_response = _valid_response()
    raw_response["criteria"][0]["evidence_refs"] = [str(region.id)]  # type: ignore[index]
    response = canonicalize(AIGradingOutput.model_validate(raw_response).model_dump(mode="json"))
    item = CodexWorkItem(
        processing_step_id=step.id,
        owner_id=run.owner_id,
        grading_batch_id=run.grading_batch_id,
        submission_id=submission.id,
        student_answer_id=answer.id,
        status="submitted",
        generation=run.generation,
        input_version=step.input_version,
        request_hash=canonical_hash(request),
        request_payload=request,
        response_payload=response,
        response_hash=canonical_hash(response),
        provider="codex_local",
        prompt_version=codex_local.PROMPT_VERSION,
        schema_version=codex_local.OUTPUT_SCHEMA,
        config_version=codex_local.CONFIG_VERSION,
        submitted_lease_token_hash="7" * 64,
        submitted_at=now_utc(),
    )
    db.add(item)
    db.commit()
    context = ValidationContext(
        criterion_maxima={"criterion-1": Decimal("2")},
        evidence_ids={str(region.id)},
        criterion_keys={"criterion-1"},
        question_max_points=Decimal("2"),
    )
    monkeypatch.setattr(
        codex_local,
        "_request_components",
        lambda *_args, **_kwargs: (request, step.input_version, context),
    )
    monkeypatch.setattr(
        codex_local,
        "require_current_recognition_evidence",
        lambda *_args, **_kwargs: evidence,
    )
    return item, run, step


def test_apply_is_atomic_suggestion_only_and_replays_once(
    sqlite_db: Session,
    submitted_item: tuple[CodexWorkItem, ProcessingRun, ProcessingStep],
) -> None:
    db = sqlite_db
    item, _, _ = submitted_item
    applied = apply_work_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        request_hash=item.request_hash,
        response_hash=item.response_hash or "",
    )
    replay = apply_work_item(
        db,
        item_id=item.id,
        worker_id="worker-2",
        request_hash=item.request_hash,
        response_hash=item.response_hash or "",
    )
    assert replay.id == applied.id
    assert applied.status == "applied"
    assert applied.grading_job_id is not None
    assert applied.grading_result_id is not None
    result = db.get(GradingResult, applied.grading_result_id)
    assert result is not None
    assert (
        result.provider,
        result.provider_version,
        result.grading_method,
        result.status,
        result.requires_review,
    ) == ("codex_local", "local", "codex_assisted", "suggested", True)
    assert result.confidence == Decimal("0.8")
    assert db.scalar(select(func.count()).select_from(GradingJob)) == 1
    assert db.scalar(select(func.count()).select_from(GradingResult)) == 1
    assert db.scalar(select(func.count()).select_from(GradingCriterionResult)) == 1
    assert db.scalar(select(func.count()).select_from(GradingEvidence)) == 1
    assert db.scalar(select(func.count()).select_from(AIScoringJob)) == 1
    assert db.scalar(select(func.count()).select_from(AICriterionSuggestion)) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "codex_local.applied")
        )
        == 1
    )
    assert db.scalar(select(func.count()).select_from(TeacherReview)) == 0
    assert db.scalar(select(func.count()).select_from(SubmissionScoreSnapshot)) == 0
    assert db.scalar(select(func.count()).select_from(GradeRelease)) == 0
    reconciled = reconcile_processing(
        db,
        owner_id=item.owner_id,
        batch_id=item.grading_batch_id,
        run_id=item.step.processing_run_id,
        idempotency_key=f"reconcile-{item.id}",
        expected_generation=item.generation,
    )
    assert reconciled.status == "awaiting_teacher_review"
    assert reconciled.completed_step_count == 1
    assert reconciled.pending_codex_count == 0
    db.refresh(item.step)
    assert item.step.status == "succeeded"


def test_reconcile_ignores_superseded_region_with_matching_geometry(
    sqlite_db: Session,
    submitted_item: tuple[CodexWorkItem, ProcessingRun, ProcessingStep],
) -> None:
    db = sqlite_db
    item, _, _ = submitted_item
    current_region = db.scalar(
        select(StudentAnswerRegion).where(
            StudentAnswerRegion.student_answer_id == item.student_answer_id,
            StudentAnswerRegion.status == "confirmed",
        )
    )
    assert current_region is not None
    db.add(
        StudentAnswerRegion(
            student_answer_id=current_region.student_answer_id,
            submission_page_id=current_region.submission_page_id,
            x=current_region.x,
            y=current_region.y,
            width=current_region.width,
            height=current_region.height,
            status="superseded",
        )
    )
    db.commit()

    apply_work_item(
        db,
        item_id=item.id,
        worker_id="worker",
        request_hash=item.request_hash,
        response_hash=item.response_hash or "",
    )
    reconciled = reconcile_processing(
        db,
        owner_id=item.owner_id,
        batch_id=item.grading_batch_id,
        run_id=item.step.processing_run_id,
        idempotency_key=f"reconcile-superseded-{item.id}",
        expected_generation=item.generation,
    )

    assert reconciled.status == "awaiting_teacher_review"
    assert reconciled.completed_step_count == 1
    db.refresh(item.step)
    assert item.step.status == "succeeded"


def test_apply_hash_conflict_writes_no_children(
    sqlite_db: Session,
    submitted_item: tuple[CodexWorkItem, ProcessingRun, ProcessingStep],
) -> None:
    db = sqlite_db
    item, _, _ = submitted_item
    with pytest.raises(codex_local.CodexLocalProblem) as conflict:
        apply_work_item(
            db,
            item_id=item.id,
            worker_id="worker",
            request_hash="f" * 64,
            response_hash=item.response_hash or "",
        )
    db.rollback()
    assert conflict.value.code == "CODEX_APPLY_CONTRACT_CONFLICT"
    assert db.scalar(select(func.count()).select_from(GradingJob)) == 0
    assert db.scalar(select(func.count()).select_from(AIScoringJob)) == 0


@pytest.mark.parametrize("tamper", ["missing", "feedback"])
def test_applied_replay_requires_complete_untampered_strict_child(
    sqlite_db: Session,
    submitted_item: tuple[CodexWorkItem, ProcessingRun, ProcessingStep],
    tamper: str,
) -> None:
    db = sqlite_db
    item, _, _ = submitted_item
    applied = apply_work_item(
        db,
        item_id=item.id,
        worker_id="worker",
        request_hash=item.request_hash,
        response_hash=item.response_hash or "",
    )
    strict_job = db.scalar(
        select(AIScoringJob).where(AIScoringJob.student_answer_id == item.student_answer_id)
    )
    assert strict_job is not None
    feedback = db.scalar(
        select(AIFeedbackDraft).where(AIFeedbackDraft.ai_scoring_job_id == strict_job.id)
    )
    assert feedback is not None
    if tamper == "missing":
        db.delete(feedback)
        for row in db.scalars(
            select(AICriterionSuggestion).where(
                AICriterionSuggestion.ai_scoring_job_id == strict_job.id
            )
        ):
            db.delete(row)
        db.delete(strict_job)
    else:
        feedback.teacher_summary = "tampered"
    db.commit()
    with pytest.raises(codex_local.CodexLocalProblem) as conflict:
        apply_work_item(
            db,
            item_id=applied.id,
            worker_id="worker",
            request_hash=item.request_hash,
            response_hash=item.response_hash or "",
        )
    assert conflict.value.code == "CODEX_APPLY_CONTRACT_CONFLICT"


@pytest.mark.parametrize(
    "drift",
    ["formal", "evidence", "finalized", "teacher_review", "strict"],
)
def test_reconcile_current_drift_never_advances_to_teacher_review(
    sqlite_db: Session,
    submitted_item: tuple[CodexWorkItem, ProcessingRun, ProcessingStep],
    drift: str,
) -> None:
    db = sqlite_db
    item, run, step = submitted_item
    apply_work_item(
        db,
        item_id=item.id,
        worker_id="worker",
        request_hash=item.request_hash,
        response_hash=item.response_hash or "",
    )
    answer = db.get(StudentAnswer, item.student_answer_id)
    submission = db.get(Submission, item.submission_id)
    assert answer is not None and submission is not None
    if drift == "formal":
        rubric = db.scalar(
            select(StructuredRubricVersion).where(
                StructuredRubricVersion.question_id == answer.question_id
            )
        )
        assert rubric is not None
        rubric.content_hash = "f" * 64
    elif drift == "evidence":
        evidence = db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.student_answer_id == answer.id
            )
        )
        assert evidence is not None
        evidence.input_hash = "e" * 64
    elif drift == "finalized":
        submission.status = "finalized"
        submission.finalized_at = now_utc()
    elif drift == "teacher_review":
        assert item.grading_result_id is not None
        db.add(
            TeacherReview(
                grading_result_id=item.grading_result_id,
                student_answer_id=answer.id,
                reviewer_id=item.owner_id,
                decision="accepted",
            )
        )
    else:
        strict_job = db.scalar(
            select(AIScoringJob).where(AIScoringJob.student_answer_id == answer.id)
        )
        assert strict_job is not None
        strict_job.response_hash = "0" * 64
    db.commit()
    reconciled = reconcile_processing(
        db,
        owner_id=item.owner_id,
        batch_id=item.grading_batch_id,
        run_id=run.id,
        idempotency_key=f"reconcile-drift-{drift}-{item.id}",
        expected_generation=run.generation,
    )
    assert reconciled.status != "awaiting_teacher_review"
    db.refresh(step)
    assert step.status == "stale"
    assert step.error_code == "CODEX_APPLY_CONTRACT_CONFLICT"
