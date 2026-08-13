from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai_tutor.jobs import wrong_question_job_input_hash
from app.ai_tutor.providers import provider_from_settings
from app.ai_tutor.schema import TutorConversationTurn, TutorEvidence, WrongQuestionTutorInput
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations.openai_client import StructuredProviderResult, canonical_json_hash
from app.models import (
    GradeRelease,
    GradeReleaseItem,
    Student,
    StudentAccountLink,
    Submission,
    SubmissionScoreSnapshot,
    WrongQuestionAIJob,
    WrongQuestionMessage,
    WrongQuestionThread,
    now_utc,
)
from sqlalchemy import select

from workers.celery_app import celery_app
from workers.task_context import run_traced_task

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "discarded_late"}


def _stable_error_code(error: str | None) -> str:
    mapping = {
        "provider_unavailable": "AI_PROVIDER_UNAVAILABLE",
        "provider_external_requests_disabled": "AI_EXTERNAL_REQUESTS_DISABLED",
        "provider_configuration_incomplete": "AI_PROVIDER_CONFIGURATION_INCOMPLETE",
        "provider_authentication_failed": "AI_PROVIDER_AUTHENTICATION_FAILED",
        "provider_permission_denied": "AI_PROVIDER_PERMISSION_DENIED",
        "provider_model_not_found": "AI_PROVIDER_MODEL_NOT_FOUND",
        "provider_timeout": "AI_PROVIDER_TIMEOUT",
        "provider_rate_limited": "AI_PROVIDER_RATE_LIMITED",
        "provider_network_error": "AI_PROVIDER_NETWORK_ERROR",
        "provider_content_filtered": "AI_CONTENT_FILTERED",
        "provider_refusal": "AI_REFUSED",
        "provider_input_invalid": "AI_INPUT_INVALID",
        "provider_schema_invalid": "AI_OUTPUT_INVALID",
    }
    return mapping.get(error or "", "AI_PROVIDER_FAILED")


def _snapshot_detail(
    snapshot: SubmissionScoreSnapshot, answer_id: uuid.UUID
) -> dict[str, Any] | None:
    for raw in snapshot.details or []:
        if str(raw.get("student_answer_id")) == str(answer_id):
            return raw
    return None


def _released_scope(
    db: Any,
    *,
    snapshot_id: uuid.UUID,
    student_id: uuid.UUID,
    assignment_id: uuid.UUID,
    class_id: uuid.UUID,
) -> tuple[GradeReleaseItem, GradeRelease] | None:
    row = db.execute(
        select(GradeReleaseItem, GradeRelease)
        .join(GradeRelease, GradeRelease.id == GradeReleaseItem.grade_release_id)
        .where(
            GradeReleaseItem.student_id == student_id,
            GradeReleaseItem.status == "included",
            GradeRelease.status == "released",
            GradeRelease.release_mode != "internal_only",
            GradeRelease.assignment_id == assignment_id,
            GradeRelease.class_id == class_id,
        )
        .order_by(GradeRelease.version.desc(), GradeRelease.released_at.desc())
        .limit(1)
    ).first()
    if row is None or row[0].score_snapshot_id != snapshot_id:
        return None
    return row[0], row[1]


def _active_student_link(db: Any, *, user_id: uuid.UUID, student_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(StudentAccountLink.id)
            .join(Student, Student.id == StudentAccountLink.student_id)
            .where(
                StudentAccountLink.user_id == user_id,
                StudentAccountLink.student_id == student_id,
                StudentAccountLink.status == "active",
                Student.status == "active",
            )
            .limit(1)
        )
    )


def _prepare_payload(
    db: Any,
    *,
    job: WrongQuestionAIJob,
    thread: WrongQuestionThread,
    message: WrongQuestionMessage,
    snapshot: SubmissionScoreSnapshot,
    answer_id: uuid.UUID,
    release: GradeRelease,
) -> WrongQuestionTutorInput | None:
    detail = _snapshot_detail(snapshot, answer_id)
    if (
        detail is None
        or "question_text" not in detail
        or "student_answer_text" not in detail
        or not str(detail.get("question_text") or "").strip()
    ):
        return None
    feedback = ""
    if release.release_mode != "score_only":
        feedback = str(detail.get("final_feedback", detail.get("feedback")) or "")
    awarded: Decimal | None = None
    if release.release_mode != "feedback_only":
        try:
            awarded = Decimal(str(detail.get("score")))
        except (InvalidOperation, TypeError, ValueError):
            awarded = None
    maximum: Decimal | None = None
    if release.release_mode != "feedback_only":
        try:
            maximum = Decimal(str(detail.get("max_score")))
        except (InvalidOperation, TypeError, ValueError):
            maximum = None

    question_text = str(detail.get("question_text") or "")
    answer_text = str(detail.get("student_answer_text") or "")
    opaque_prefix = job.input_hash[:24]
    evidence: list[TutorEvidence] = []
    if answer_text.strip():
        evidence.append(
            TutorEvidence(
                evidence_id=f"answer:{opaque_prefix}",
                kind="student_answer",
                text=answer_text[:4000],
            )
        )
    if feedback.strip():
        evidence.append(
            TutorEvidence(
                evidence_id=f"feedback:{opaque_prefix}",
                kind="published_feedback",
                text=feedback[:4000],
            )
        )
    prior = db.scalars(
        select(WrongQuestionMessage)
        .where(WrongQuestionMessage.thread_id == thread.id)
        .order_by(WrongQuestionMessage.created_at, WrongQuestionMessage.id)
    ).all()
    turns = [
        TutorConversationTurn(role=item.role, content=item.content[:4000])
        for item in prior
        if item.id != message.id and item.role in {"student", "assistant"} and item.content.strip()
    ][-get_settings().ai_tutor_max_conversation_messages :]
    return WrongQuestionTutorInput(
        question_id=f"question:{opaque_prefix}",
        score_snapshot_id=f"snapshot:{opaque_prefix}",
        question_text=question_text[:12000],
        student_answer_text=answer_text[:12000],
        published_feedback=feedback[:4000],
        awarded_points=awarded,
        max_points=maximum,
        evidence=evidence,
        conversation=turns,
        student_question=message.content,
    )


def _run_wrong_question_ai(job_id: str, *, allow_running_resume: bool = False) -> dict[str, Any]:
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError:
        return {"status": "invalid_job_id"}
    settings = get_settings()
    with SessionLocal() as db:
        job = db.scalar(
            select(WrongQuestionAIJob)
            .where(WrongQuestionAIJob.id == parsed_job_id)
            .with_for_update()
        )
        if job is None:
            return {"status": "missing"}
        if job.status in TERMINAL_STATUSES:
            return {"status": "already_processed"}
        if job.status == "running" and allow_running_resume:
            job.status = "queued"
        if job.status != "queued":
            return {"status": "not_queued"}
        thread = db.get(WrongQuestionThread, job.thread_id)
        message = db.get(WrongQuestionMessage, job.user_message_id)
        if thread is None or message is None or message.thread_id != job.thread_id:
            job.status, job.error_code = "failed", "AI_INPUT_STALE"
            job.completed_at = now_utc()
            db.commit()
            return {"status": "failed", "error_code": job.error_code}
        expected_hash = wrong_question_job_input_hash(
            thread_id=str(thread.id),
            score_snapshot_id=str(thread.score_snapshot_id),
            generation=job.generation,
            content=message.content,
        )
        snapshot = db.get(SubmissionScoreSnapshot, thread.score_snapshot_id)
        submission = db.get(Submission, snapshot.submission_id) if snapshot else None
        released = _released_scope(
            db,
            snapshot_id=thread.score_snapshot_id,
            student_id=thread.student_id,
            assignment_id=snapshot.assignment_id if snapshot else uuid.UUID(int=0),
            class_id=submission.class_id if submission else uuid.UUID(int=0),
        )
        if (
            expected_hash != job.input_hash
            or thread.status != "open"
            or message.role != "student"
            or snapshot is None
            or snapshot.status != "complete"
            or snapshot.student_id != thread.student_id
            or submission is None
            or not _active_student_link(db, user_id=thread.user_id, student_id=thread.student_id)
            or released is None
        ):
            job.status, job.error_code = "discarded_late", "AI_INPUT_STALE"
            job.completed_at = now_utc()
            db.commit()
            return {"status": "discarded_late"}
        release = released[1]
        payload = _prepare_payload(
            db,
            job=job,
            thread=thread,
            message=message,
            snapshot=snapshot,
            answer_id=thread.student_answer_id,
            release=release,
        )
        if payload is None:
            job.status, job.error_code = "discarded_late", "AI_INPUT_STALE"
            job.completed_at = now_utc()
            db.commit()
            return {"status": "discarded_late"}
        provider = provider_from_settings(settings)
        generation = job.generation
        prepared_message_content = message.content
        message_id = message.id
        thread_id = thread.id
        snapshot_id = snapshot.id
        user_id = thread.user_id
        student_id = thread.student_id
        answer_id = thread.student_answer_id
        prepared_details_hash = canonical_json_hash(snapshot.details or [])
        release_id = release.id
        job.status = "running"
        job.provider = provider.name
        job.model = settings.ai_tutor_model
        job.prompt_version = settings.ai_tutor_prompt_version
        job.schema_version = settings.ai_tutor_schema_version
        job.started_at = now_utc()
        db.commit()

        try:
            response = provider.answer(
                payload.model_dump(mode="json"),
                safety_subject=str(user_id),
            )
        except Exception:
            response = StructuredProviderResult(None, error="provider_internal_error")

        db.expire_all()
        current = db.scalar(
            select(WrongQuestionAIJob)
            .where(WrongQuestionAIJob.id == parsed_job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        current_thread = db.get(WrongQuestionThread, thread_id)
        current_message = db.get(WrongQuestionMessage, message_id)
        current_snapshot = db.get(SubmissionScoreSnapshot, snapshot_id)
        current_submission = (
            db.get(Submission, current_snapshot.submission_id) if current_snapshot else None
        )
        current_released = _released_scope(
            db,
            snapshot_id=snapshot_id,
            student_id=student_id,
            assignment_id=(
                current_snapshot.assignment_id if current_snapshot else uuid.UUID(int=0)
            ),
            class_id=current_submission.class_id if current_submission else uuid.UUID(int=0),
        )
        late = (
            current is None
            or current.status != "running"
            or current.generation != generation
            or current.input_hash != expected_hash
            or current_thread is None
            or current_thread.status != "open"
            or current_thread.score_snapshot_id != snapshot_id
            or current_thread.student_id != student_id
            or current_thread.student_answer_id != answer_id
            or current_message is None
            or current_message.content != prepared_message_content
            or current_snapshot is None
            or current_snapshot.status != "complete"
            or canonical_json_hash(current_snapshot.details or []) != prepared_details_hash
            or current_submission is None
            or not _active_student_link(db, user_id=user_id, student_id=student_id)
            or current_released is None
            or current_released[1].id != release_id
            or wrong_question_job_input_hash(
                thread_id=str(thread_id),
                score_snapshot_id=str(snapshot_id),
                generation=generation,
                content=current_message.content if current_message else "",
            )
            != expected_hash
        )
        if current is None:
            db.rollback()
            return {"status": "discarded_late"}
        current.provider_request_id = response.request_id
        current.request_hash = response.request_hash
        current.response_hash = response.response_hash
        current.input_tokens = response.input_tokens
        current.output_tokens = response.output_tokens
        current.attempts = response.attempts
        current.retryable = response.retryable
        current.completed_at = now_utc()
        if late:
            current.status, current.error_code = "discarded_late", "AI_INPUT_STALE"
            db.commit()
            return {"status": "discarded_late"}
        if response.output is None:
            current.status = "failed"
            current.error_code = _stable_error_code(response.error)
            current.error_message = "AI tutor did not return a valid response."
            db.commit()
            return {
                "status": "failed",
                "error_code": current.error_code,
                "retryable": current.retryable,
            }

        request_id = response.request_id or f"local:{current.id}"
        current.provider_request_id = request_id
        db.add(
            WrongQuestionMessage(
                thread_id=current.thread_id,
                role="assistant",
                content=response.output.explanation,
                structured_payload=response.output.model_dump(mode="json"),
                provider_request_id=request_id,
            )
        )
        current.status = "completed"
        current.error_code = None
        current.error_message = None
        db.commit()
        return {"status": "completed", "job_id": str(current.id)}


@celery_app.task(
    name="ahamark.wrong_question_ai.run",
    bind=True,
    soft_time_limit=180,
    time_limit=195,
)
def run_wrong_question_ai(self: Any, job_id: str) -> dict[str, Any]:
    delivery = self.request.delivery_info or {}
    try:
        return run_traced_task(
            self,
            job_id,
            lambda: _run_wrong_question_ai(
                job_id, allow_running_resume=bool(delivery.get("redelivered"))
            ),
        )
    except Exception:
        try:
            parsed_id = uuid.UUID(job_id)
        except ValueError:
            return {"status": "invalid_job_id"}
        with SessionLocal() as db:
            job = db.scalar(
                select(WrongQuestionAIJob)
                .where(WrongQuestionAIJob.id == parsed_id)
                .with_for_update()
            )
            if job is not None and job.status in {"queued", "running"}:
                job.status = "failed"
                job.error_code = "AI_WORKER_INTERNAL_ERROR"
                job.error_message = "The AI worker failed before producing a durable result."
                job.retryable = True
                job.completed_at = now_utc()
                db.commit()
        return {"status": "failed", "error_code": "AI_WORKER_INTERNAL_ERROR"}
