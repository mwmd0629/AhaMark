import hashlib
import json
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.ai_grading.guards import (
    GuardViolation,
    public_status,
    require_answer_relation,
    require_confirmed_answer,
    require_submission_mutable,
    validate_evidence_refs,
    validate_score,
    validate_validation_link,
)
from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.core.config import get_settings
from app.db.session import get_db
from app.math_validation.stale import stale_for_ai_versions
from app.models import (
    AICriterionSuggestion,
    AIFeedbackDraft,
    AIProviderInvocation,
    AIScoringJob,
    AISuggestionReview,
    CriterionValidationResult,
    MathValidationJob,
    Question,
    QuestionRecognitionEvidence,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    now_utc,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/ai-grading", tags=["ai-grading"])
Db = Annotated[Session, Depends(get_db)]


class CreateJob(BaseModel):
    student_answer_id: uuid.UUID
    rubric_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    criterion_stable_key: str | None = None


class ReviewInput(BaseModel):
    action: Literal["accepted", "modified", "rejected"]
    selected_points: float | None = None
    reason: str = Field(min_length=1, max_length=2000)


class FeedbackInput(BaseModel):
    student_feedback: str = Field(max_length=4000)
    teacher_summary: str = Field(max_length=4000)


def _owned_job(db: Session, actor_id: uuid.UUID, job_id: uuid.UUID) -> AIScoringJob:
    job = db.scalar(
        select(AIScoringJob).where(AIScoringJob.id == job_id, AIScoringJob.owner_id == actor_id)
    )
    if not job:
        raise ApiProblem(404, "AI_JOB_NOT_FOUND", "AI scoring job not found")
    return job


def _assert_submission_mutable(db: Session, submission_id: uuid.UUID) -> None:
    submission = db.get(Submission, submission_id)
    if not submission:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "Submission not found")
    try:
        require_submission_mutable(submission)
    except GuardViolation as exc:
        raise exc.problem(409) from exc


def _enqueue(job: AIScoringJob, db: Session, criterion_key: str | None) -> None:
    from workers.tasks.ai_grading import run_ai_grading

    task = run_ai_grading.delay(str(job.id), job.generation, criterion_key)
    job.celery_task_id = task.id
    db.commit()


def job_json(db: Session, j: AIScoringJob) -> dict[str, Any]:
    suggestions = db.scalars(
        select(AICriterionSuggestion)
        .where(AICriterionSuggestion.ai_scoring_job_id == j.id)
        .order_by(AICriterionSuggestion.created_at)
    ).all()
    reviews = (
        db.scalars(
            select(AISuggestionReview)
            .where(AISuggestionReview.suggestion_id.in_([item.id for item in suggestions]))
            .order_by(AISuggestionReview.created_at.desc())
        ).all()
        if suggestions
        else []
    )
    reviews_by_suggestion: dict[uuid.UUID, AISuggestionReview] = {}
    for review in reviews:
        reviews_by_suggestion.setdefault(review.suggestion_id, review)
    invocations = db.scalars(
        select(AIProviderInvocation).where(AIProviderInvocation.ai_scoring_job_id == j.id)
    ).all()
    draft = db.scalar(select(AIFeedbackDraft).where(AIFeedbackDraft.ai_scoring_job_id == j.id))
    recognition = db.get(QuestionRecognitionEvidence, j.recognition_evidence_id)
    regions = db.scalars(
        select(StudentAnswerRegion)
        .where(
            StudentAnswerRegion.student_answer_id == j.student_answer_id,
            StudentAnswerRegion.status == "confirmed",
        )
        .order_by(StudentAnswerRegion.created_at)
    ).all()
    validation_job = (
        db.get(MathValidationJob, j.math_validation_job_id) if j.math_validation_job_id else None
    )
    validation_results = (
        db.scalars(
            select(CriterionValidationResult)
            .where(CriterionValidationResult.validation_job_id == validation_job.id)
            .order_by(CriterionValidationResult.created_at)
        ).all()
        if validation_job
        else []
    )
    return {
        "id": str(j.id),
        "student_answer_id": str(j.student_answer_id),
        "status": j.status,
        "generation": j.generation,
        "provider": j.provider,
        "model": j.model,
        "prompt_version": j.prompt_version,
        "schema_version": j.schema_version,
        "stale": j.stale_at is not None,
        "error_code": j.error_code,
        "scoring_input_version": j.scoring_input_version,
        "rubric_version_id": str(j.rubric_version_id),
        "reference_answer_version_id": str(j.reference_answer_version_id),
        "evidence": (
            [
                {
                    "id": f"recognition:{recognition.id}",
                    "kind": "recognition",
                    "status": recognition.status,
                    "stale": recognition.stale_at is not None,
                    "version": recognition.recognition_version,
                    "confirmed_revision": recognition.confirmed_revision,
                    "target_id": "answer-recognition-workspace",
                }
            ]
            if recognition
            else []
        )
        + [
            {
                "id": f"region:{region.id}",
                "kind": "region",
                "status": region.status,
                "stale": region.status in {"stale", "superseded"},
                "version": region.region_version,
                "submission_page_id": str(region.submission_page_id),
                "coordinates": {
                    "x": str(region.x),
                    "y": str(region.y),
                    "width": str(region.width),
                    "height": str(region.height),
                },
                "target_id": f"answer-region-{region.id}",
            }
            for region in regions
        ],
        "validation": {
            "job_id": str(validation_job.id),
            "status": validation_job.status,
            "generation": validation_job.generation,
            "stale": validation_job.stale_at is not None,
            "rubric_version_id": str(validation_job.rubric_version_id),
            "reference_answer_version_id": str(validation_job.reference_answer_version_id),
            "results": [
                {
                    "id": str(result.id),
                    "criterion_id": str(result.criterion_id),
                    "generation": result.generation,
                    "result": result.result,
                    "comparison_method": result.comparison_method,
                    "stale": result.stale_at is not None,
                    "diagnostics": result.diagnostics,
                }
                for result in validation_results
            ],
        }
        if validation_job
        else None,
        "usage": {
            "input_tokens": j.input_tokens,
            "output_tokens": j.output_tokens,
            "images": j.image_count,
            "estimated_cost": str(j.estimated_cost) if j.estimated_cost is not None else None,
        },
        "suggestions": [
            {
                "id": str(x.id),
                "criterion_id": str(x.criterion_id),
                "criterion_stable_key": x.criterion_stable_key,
                "status": public_status(
                    x.status,
                    points=x.suggested_points,
                    stale=bool(j.stale_at),
                    error_code=j.error_code,
                ),
                "reason": x.manual_review_reason or x.reasoning_summary,
                "error_codes": sorted(
                    set(x.error_codes or []) | ({j.error_code} if j.error_code else set())
                ),
                "evidence_ids": x.evidence_refs,
                "validation_refs": x.validation_refs,
                "requires_review": x.requires_review,
                "suggested_points": str(x.suggested_points)
                if x.suggested_points is not None
                else None,
                "max_points": str(x.max_points),
                "score": str(x.suggested_points) if x.suggested_points is not None else None,
                "max_score": str(x.max_points),
                "confidence": str(x.confidence) if x.confidence is not None else None,
                "evidence_refs": x.evidence_refs,
                "missing_steps": x.missing_steps,
                "detected_errors": x.detected_errors,
                "manual_review_reason": x.manual_review_reason,
                "student_feedback": x.student_feedback,
                "teacher_note": x.teacher_note,
                "deterministic_conflict": x.deterministic_conflict,
                "review": (
                    {
                        "id": str(reviews_by_suggestion[x.id].id),
                        "action": reviews_by_suggestion[x.id].action,
                        "selected_points": (
                            str(reviews_by_suggestion[x.id].selected_points)
                            if reviews_by_suggestion[x.id].selected_points is not None
                            else None
                        ),
                        "reason": reviews_by_suggestion[x.id].reason,
                        "created_at": reviews_by_suggestion[x.id].created_at.isoformat(),
                    }
                    if x.id in reviews_by_suggestion
                    else None
                ),
            }
            for x in suggestions
        ],
        "feedback": {
            "student_feedback": draft.student_feedback,
            "teacher_summary": draft.teacher_summary,
            "disposition": draft.teacher_disposition,
        }
        if draft
        else None,
        "invocations": [
            {
                "provider": x.provider,
                "endpoint_mode": x.endpoint_mode,
                "model": x.model,
                "request_id": x.provider_request_id,
                "latency_ms": x.latency_ms,
                "status": x.response_status,
                "error_code": x.error_code,
                "started_at": x.started_at.isoformat() if x.started_at else None,
                "completed_at": x.completed_at.isoformat() if x.completed_at else None,
            }
            for x in invocations
        ],
    }


@router.post("/jobs", status_code=202)
def create_job(data: CreateJob, db: Db, actor: Actor) -> dict[str, Any]:
    settings = get_settings()
    stale_for_ai_versions(
        db,
        settings.ai_grading_provider,
        settings.ai_grading_model,
        settings.ai_grading_prompt_version,
        settings.ai_grading_schema_version,
        settings.ai_grading_config_version,
    )
    existing = db.scalar(
        select(AIScoringJob).where(
            AIScoringJob.owner_id == actor.id, AIScoringJob.idempotency_key == data.idempotency_key
        )
    )
    if existing:
        return job_json(db, existing)
    answer = db.get(StudentAnswer, data.student_answer_id)
    rubric = db.get(StructuredRubricVersion, data.rubric_version_id)
    if not answer or not rubric or rubric.question_id != answer.question_id:
        raise ApiProblem(404, "AI_GRADING_INPUT_NOT_FOUND", "Answer or rubric not found")
    submission = db.get(Submission, answer.submission_id)
    if not submission or submission.owner_id != actor.id:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "Submission not found")
    question = db.get(Question, answer.question_id)
    try:
        require_answer_relation(answer, submission, question, actor.id)
        require_confirmed_answer(answer)
    except GuardViolation as exc:
        raise exc.problem(422) from exc
    try:
        require_submission_mutable(submission)
    except GuardViolation as exc:
        raise exc.problem(409) from exc
    batch_cost = db.scalar(
        select(func.coalesce(func.sum(AIScoringJob.estimated_cost), 0)).where(
            AIScoringJob.assignment_id == submission.assignment_id,
            AIScoringJob.owner_id == actor.id,
        )
    )
    if batch_cost and float(batch_cost) >= settings.ai_grading_max_cost_per_batch:
        raise ApiProblem(
            429,
            "AI_BATCH_COST_BUDGET_EXCEEDED",
            "The assignment AI grading cost budget has been reached",
        )
    if rubric.status != "confirmed":
        raise ApiProblem(422, "RUBRIC_NOT_CONFIRMED", "Confirmed rubric required")
    evidence = db.scalar(
        select(QuestionRecognitionEvidence)
        .where(
            QuestionRecognitionEvidence.student_answer_id == answer.id,
            QuestionRecognitionEvidence.status == "confirmed",
            QuestionRecognitionEvidence.stale_at.is_(None),
        )
        .order_by(QuestionRecognitionEvidence.recognition_version.desc())
    )
    reference = db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
    if (
        not evidence
        or evidence.owner_id != actor.id
        or evidence.submission_id != submission.id
        or not reference
        or reference.status != "confirmed"
    ):
        raise ApiProblem(
            422, "AI_INPUT_NOT_CONFIRMED", "Confirmed recognition and reference answer required"
        )
    validation = db.scalar(
        select(MathValidationJob)
        .where(
            MathValidationJob.student_answer_id == answer.id,
            MathValidationJob.rubric_version_id == rubric.id,
            MathValidationJob.reference_answer_version_id == reference.id,
            MathValidationJob.status == "completed",
            MathValidationJob.stale_at.is_(None),
        )
        .order_by(MathValidationJob.generation.desc())
    )
    db.scalar(select(StudentAnswer).where(StudentAnswer.id == answer.id).with_for_update())
    generation = (
        db.scalar(
            select(func.max(AIScoringJob.generation)).where(
                AIScoringJob.student_answer_id == answer.id
            )
        )
        or 0
    ) + 1
    s = settings
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "answer": str(answer.id),
                "evidence": evidence.input_hash,
                "rubric": rubric.content_hash,
                "reference": reference.content_hash,
                "validation": str(validation.id) if validation else None,
                "prompt": s.ai_grading_prompt_version,
                "schema": s.ai_grading_schema_version,
                "config": s.ai_grading_config_version,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    job = AIScoringJob(
        owner_id=actor.id,
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
        idempotency_key=data.idempotency_key,
        generation=generation,
        attempt=0,
        provider=s.ai_grading_provider,
        model=s.ai_grading_model,
        endpoint_mode="chat_completions",
        prompt_version=s.ai_grading_prompt_version,
        schema_version=s.ai_grading_schema_version,
        provider_config_version=s.ai_grading_config_version,
        grading_config_version=s.ai_grading_config_version,
        request_hash=request_hash,
        image_count=0,
        image_bytes=0,
        retryable=False,
    )
    db.add(job)
    db.flush()
    audit(db, actor.id, "ai_grading.create", "ai_scoring_job", job.id, {"generation": generation})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = db.scalar(
            select(AIScoringJob).where(
                AIScoringJob.owner_id == actor.id,
                AIScoringJob.idempotency_key == data.idempotency_key,
            )
        )
        if winner:
            return job_json(db, winner)
        raise
    _enqueue(job, db, data.criterion_stable_key)
    return job_json(db, job)


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    job = _owned_job(db, actor.id, job_id)
    return job_json(db, job)


@router.get("/student-answers/{answer_id}/jobs")
def answer_jobs(answer_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AIScoringJob)
        .where(
            AIScoringJob.student_answer_id == answer_id,
            AIScoringJob.owner_id == actor.id,
        )
        .order_by(AIScoringJob.generation.desc())
    ).all()
    return [job_json(db, row) for row in rows]


@router.get("/student-answers/{answer_id}/current")
def current_suggestions(answer_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    job = db.scalar(
        select(AIScoringJob)
        .where(
            AIScoringJob.student_answer_id == answer_id,
            AIScoringJob.owner_id == actor.id,
            AIScoringJob.stale_at.is_(None),
            AIScoringJob.status.in_(
                ["completed", "partially_completed", "abstained", "review_pending"]
            ),
        )
        .order_by(AIScoringJob.generation.desc())
    )
    if not job:
        raise ApiProblem(404, "CURRENT_AI_SUGGESTION_NOT_FOUND", "No current suggestion")
    return job_json(db, job)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    source = _owned_job(db, actor.id, job_id)
    return create_job(
        CreateJob(
            student_answer_id=source.student_answer_id,
            rubric_version_id=source.rubric_version_id,
            idempotency_key=f"retry:{source.id}:{uuid.uuid4().hex}",
        ),
        db,
        actor,
    )


@router.post("/jobs/{job_id}/criteria/{criterion_key}/retry", status_code=202)
def retry_criterion(job_id: uuid.UUID, criterion_key: str, db: Db, actor: Actor) -> dict[str, Any]:
    source = _owned_job(db, actor.id, job_id)
    exists = db.scalar(
        select(RubricCriterion.id).where(
            RubricCriterion.rubric_version_id == source.rubric_version_id,
            RubricCriterion.stable_key == criterion_key,
        )
    )
    if not exists:
        raise ApiProblem(404, "RUBRIC_CRITERION_NOT_FOUND", "Criterion not found")
    return create_job(
        CreateJob(
            student_answer_id=source.student_answer_id,
            rubric_version_id=source.rubric_version_id,
            idempotency_key=(f"criterion-retry:{source.id}:{criterion_key}:{uuid.uuid4().hex}"),
            criterion_stable_key=criterion_key,
        ),
        db,
        actor,
    )


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    job = _owned_job(db, actor.id, job_id)
    _assert_submission_mutable(db, job.submission_id)
    if job.status not in {"completed", "partially_completed", "abstained", "failed", "stale"}:
        job.status = "cancelled"
        job.cancelled_at = now_utc()
    audit(db, actor.id, "ai_grading.cancel", "ai_scoring_job", job.id, {})
    db.commit()
    return job_json(db, job)


@router.post("/suggestions/{suggestion_id}/review")
def review(suggestion_id: uuid.UUID, data: ReviewInput, db: Db, actor: Actor) -> dict[str, Any]:
    row = db.execute(
        select(AICriterionSuggestion, AIScoringJob)
        .join(AIScoringJob, AIScoringJob.id == AICriterionSuggestion.ai_scoring_job_id)
        .where(AICriterionSuggestion.id == suggestion_id, AIScoringJob.owner_id == actor.id)
    ).one_or_none()
    if not row:
        raise ApiProblem(404, "AI_SUGGESTION_NOT_FOUND", "Suggestion not found")
    suggestion, job = row
    _assert_submission_mutable(db, job.submission_id)
    if job.stale_at or job.status == "stale":
        raise ApiProblem(409, "AI_SUGGESTION_STALE", "Stale suggestion cannot be adopted")
    existing_review = db.scalar(
        select(AISuggestionReview).where(AISuggestionReview.suggestion_id == suggestion.id)
    )
    if existing_review:
        raise ApiProblem(
            409,
            "AI_SUGGESTION_ALREADY_REVIEWED",
            "This suggestion already has a teacher disposition",
        )
    criterion = db.get(RubricCriterion, suggestion.criterion_id)
    if not criterion or criterion.rubric_version_id != job.rubric_version_id:
        raise ApiProblem(409, "CRITERION_SET_INVALID", "Criterion is not part of current rubric")
    recognition_evidence = db.get(QuestionRecognitionEvidence, job.recognition_evidence_id)
    block_sources = recognition_evidence.block_sources if recognition_evidence else []
    known_evidence = {
        f"recognition:{job.recognition_evidence_id}",
        *(str(source.get("block_id") or source.get("id")) for source in block_sources),
    }
    known_evidence.update(
        f"region:{region_id}"
        for region_id in db.scalars(
            select(StudentAnswerRegion.id).where(
                StudentAnswerRegion.student_answer_id == job.student_answer_id,
                StudentAnswerRegion.status == "confirmed",
            )
        )
    )
    known_evidence.discard("None")
    try:
        validate_evidence_refs(suggestion.evidence_refs, known_evidence)
    except GuardViolation as exc:
        raise exc.problem(422) from exc
    if job.math_validation_job_id:
        validation_job = db.get(MathValidationJob, job.math_validation_job_id)
        validation_results = db.scalars(
            select(CriterionValidationResult).where(
                CriterionValidationResult.validation_job_id == job.math_validation_job_id,
                CriterionValidationResult.criterion_id == suggestion.criterion_id,
                CriterionValidationResult.generation
                == (validation_job.generation if validation_job else -1),
                CriterionValidationResult.stale_at.is_(None),
            )
        ).all()
        validation_result = validation_results[0] if len(validation_results) == 1 else None
        if set(suggestion.validation_refs) != {str(item.id) for item in validation_results}:
            raise ApiProblem(
                409,
                "VALIDATION_STALE",
                "Suggestion validation references no longer match current results",
            )
        if data.action != "rejected":
            try:
                validate_validation_link(
                    validation_job,
                    validation_result,
                    answer_id=job.student_answer_id,
                    rubric_id=job.rubric_version_id,
                    reference_id=job.reference_answer_version_id,
                    criterion_id=suggestion.criterion_id,
                )
            except GuardViolation as exc:
                raise exc.problem(409 if exc.status == "stale" else 422) from exc
    elif suggestion.validation_refs:
        raise ApiProblem(409, "VALIDATION_STALE", "Unexpected validation references")
    suggestion_status = public_status(
        suggestion.status,
        points=suggestion.suggested_points,
        stale=bool(job.stale_at),
        error_code=job.error_code,
    )
    if data.action == "accepted" and (
        suggestion_status != "scored"
        or suggestion.suggested_points is None
        or not suggestion.requires_review
    ):
        raise ApiProblem(
            422,
            "AI_SUGGESTION_NOT_ADOPTABLE",
            "Only a current scored suggestion may be accepted",
        )
    if data.action == "accepted":
        selected = suggestion.suggested_points
    elif data.action == "modified":
        selected = data.selected_points
    else:
        selected = None
    step_raw = (criterion.partial_credit_policy or {}).get("step")
    try:
        validate_score(
            Decimal(str(selected)) if selected is not None else None,
            Decimal(str(criterion.max_points)),
            Decimal(str(step_raw)) if step_raw else None,
        )
    except GuardViolation as exc:
        raise exc.problem(422) from exc
    entry = AISuggestionReview(
        suggestion_id=suggestion.id,
        reviewer_id=actor.id,
        action=data.action,
        original_points=suggestion.suggested_points,
        selected_points=selected,
        reason=data.reason,
        scoring_input_version=job.scoring_input_version,
        rubric_version_id=job.rubric_version_id,
    )
    db.add(entry)
    audit(
        db,
        actor.id,
        "ai_grading.review",
        "ai_criterion_suggestion",
        suggestion.id,
        {"action": data.action},
    )
    db.commit()
    return {
        "id": str(entry.id),
        "action": entry.action,
        "selected_points": str(entry.selected_points)
        if entry.selected_points is not None
        else None,
        "note": "Saved as teacher draft only; no final score or release was changed.",
    }


@router.put("/jobs/{job_id}/feedback")
def edit_feedback(job_id: uuid.UUID, data: FeedbackInput, db: Db, actor: Actor) -> dict[str, Any]:
    job = _owned_job(db, actor.id, job_id)
    _assert_submission_mutable(db, job.submission_id)
    draft = db.scalar(select(AIFeedbackDraft).where(AIFeedbackDraft.ai_scoring_job_id == job.id))
    if not draft:
        draft = AIFeedbackDraft(ai_scoring_job_id=job.id)
        db.add(draft)
    draft.student_feedback = data.student_feedback
    draft.teacher_summary = data.teacher_summary
    draft.edited_by = actor.id
    draft.teacher_disposition = "edited"
    audit(db, actor.id, "ai_grading.feedback_edit", "ai_scoring_job", job.id, {})
    db.commit()
    return {"status": "draft", "published": False}


@router.get("/assignments/{assignment_id}/summary")
def assignment_summary(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    jobs = db.scalars(
        select(AIScoringJob).where(
            AIScoringJob.assignment_id == assignment_id,
            AIScoringJob.owner_id == actor.id,
        )
    ).all()
    status_counts: dict[str, int] = {}
    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    for job in jobs:
        status_counts[job.status] = status_counts.get(job.status, 0) + 1
        total_cost += float(job.estimated_cost or 0)
        total_input_tokens += job.input_tokens or 0
        total_output_tokens += job.output_tokens or 0
    manual_count = db.scalar(
        select(func.count(AICriterionSuggestion.id))
        .join(AIScoringJob, AIScoringJob.id == AICriterionSuggestion.ai_scoring_job_id)
        .where(
            AIScoringJob.assignment_id == assignment_id,
            AIScoringJob.owner_id == actor.id,
            AIScoringJob.stale_at.is_(None),
            (
                AICriterionSuggestion.status.in_(["manual_review", "abstained"])
                | AICriterionSuggestion.deterministic_conflict.is_(True)
                | (
                    AICriterionSuggestion.confidence.is_not(None)
                    & (AICriterionSuggestion.confidence < 0.7)
                )
            ),
        )
    )
    return {
        "assignment_id": str(assignment_id),
        "jobs": len(jobs),
        "status_counts": status_counts,
        "manual_review_criteria": manual_count or 0,
        "usage": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "estimated_cost": f"{total_cost:.6f}",
        },
    }


@router.get("/assignments/{assignment_id}/manual-review")
def assignment_manual_review(
    assignment_id: uuid.UUID, db: Db, actor: Actor
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AIScoringJob)
        .join(
            AICriterionSuggestion,
            AICriterionSuggestion.ai_scoring_job_id == AIScoringJob.id,
        )
        .where(
            AIScoringJob.assignment_id == assignment_id,
            AIScoringJob.owner_id == actor.id,
            AIScoringJob.stale_at.is_(None),
            (
                AICriterionSuggestion.status.in_(["manual_review", "abstained"])
                | AICriterionSuggestion.deterministic_conflict.is_(True)
                | (
                    AICriterionSuggestion.confidence.is_not(None)
                    & (AICriterionSuggestion.confidence < 0.7)
                )
            ),
        )
        .distinct()
        .order_by(AIScoringJob.created_at.desc())
    ).all()
    return [job_json(db, row) for row in rows]
