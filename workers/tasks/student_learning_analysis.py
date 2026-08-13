from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations.openai_client import StructuredProviderResult
from app.models import (
    Assignment,
    ClassStudent,
    GradeRelease,
    GradeReleaseItem,
    KnowledgePoint,
    MembershipStatus,
    SchoolClass,
    Student,
    StudentAccountLink,
    StudentLearningAnalysis,
    SubmissionScoreSnapshot,
    TeachingResource,
    now_utc,
)
from app.student_learning.jobs import student_learning_source_hash
from app.student_learning.providers import provider_from_settings
from app.student_learning.schema import (
    LearningEvidence,
    LearningResourceRef,
    ReleasedResultSummary,
    StudentLearningAnalysisInput,
)
from sqlalchemy import select

from workers.celery_app import celery_app
from workers.task_context import run_traced_task


def _active_student_link(db: Any, analysis: StudentLearningAnalysis) -> bool:
    return bool(
        db.scalar(
            select(StudentAccountLink.id)
            .join(Student, Student.id == StudentAccountLink.student_id)
            .where(
                StudentAccountLink.user_id == analysis.user_id,
                StudentAccountLink.student_id == analysis.student_id,
                StudentAccountLink.status == "active",
                Student.status == "active",
            )
            .limit(1)
        )
    )


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


def _released_rows(db: Any, analysis: StudentLearningAnalysis) -> list[tuple[Any, ...]]:
    release_ids: list[uuid.UUID] = []
    try:
        release_ids = [uuid.UUID(str(value)) for value in analysis.source_grade_release_ids]
    except (TypeError, ValueError):
        return []
    if not release_ids:
        return []
    return cast(
        list[tuple[Any, ...]],
        db.execute(
            select(GradeRelease, GradeReleaseItem, SubmissionScoreSnapshot, Assignment)
            .join(GradeReleaseItem, GradeReleaseItem.grade_release_id == GradeRelease.id)
            .join(
                SubmissionScoreSnapshot,
                SubmissionScoreSnapshot.id == GradeReleaseItem.score_snapshot_id,
            )
            .join(Assignment, Assignment.id == GradeRelease.assignment_id)
            .where(
                GradeRelease.id.in_(release_ids),
                GradeRelease.status == "released",
                GradeRelease.release_mode != "internal_only",
                GradeReleaseItem.status == "included",
                GradeReleaseItem.student_id == analysis.student_id,
                SubmissionScoreSnapshot.student_id == analysis.student_id,
                SubmissionScoreSnapshot.status == "complete",
            )
            .order_by(GradeRelease.released_at, GradeRelease.version)
        ).all(),
    )


def _authorized_resources(
    db: Any,
    analysis: StudentLearningAnalysis,
    rows: list[tuple[Any, ...]],
) -> list[TeachingResource]:
    class_ids = {release.class_id for release, *_rest in rows}
    owner_ids = {assignment.owner_id for *_prefix, assignment in rows}
    if not class_ids or not owner_ids:
        return []
    return list(
        db.scalars(
            select(TeachingResource)
            .join(SchoolClass, SchoolClass.id == TeachingResource.class_id)
            .join(ClassStudent, ClassStudent.class_id == SchoolClass.id)
            .join(Student, Student.id == ClassStudent.student_id)
            .where(
                TeachingResource.class_id.in_(class_ids),
                TeachingResource.owner_id.in_(owner_ids),
                TeachingResource.owner_id == SchoolClass.owner_id,
                TeachingResource.status == "published",
                ClassStudent.student_id == analysis.student_id,
                ClassStudent.status == MembershipStatus.active,
                Student.status == "active",
                Student.owner_id == SchoolClass.owner_id,
                SchoolClass.status == "active",
            )
            .order_by(TeachingResource.id)
        ).all()
    )


def _current_source_hash(
    db: Any,
    analysis: StudentLearningAnalysis,
    rows: list[tuple[Any, ...]],
) -> str:
    released_snapshots = sorted(
        [
            {
                "grade_release_id": str(release.id),
                "score_snapshot_id": str(snapshot.id),
                "score_snapshot_version": snapshot.version,
            }
            for release, _item, snapshot, _assignment in rows
        ],
        key=lambda item: item["grade_release_id"],
    )
    resource_versions: list[dict[str, object]] = [
        {
            "resource_id": str(item.id),
            "updated_at": item.updated_at.isoformat(),
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "title": item.title,
            "resource_type": item.resource_type,
        }
        for item in _authorized_resources(db, analysis, rows)
    ]
    return student_learning_source_hash(
        student_id=str(analysis.student_id),
        released_snapshots=released_snapshots,
        resource_versions=resource_versions,
    )


def _analysis_payload(
    db: Any,
    analysis: StudentLearningAnalysis,
    rows: list[tuple[Any, ...]],
) -> StudentLearningAnalysisInput:
    results: list[ReleasedResultSummary] = []
    evidence: list[LearningEvidence] = []
    owner_ids = {assignment.owner_id for _release, _item, _snapshot, assignment in rows}
    knowledge_point_ids: set[uuid.UUID] = set()
    for _release, _item, snapshot, _assignment in rows:
        for raw in snapshot.details or []:
            for value in raw.get("knowledge_point_ids") or []:
                try:
                    knowledge_point_ids.add(uuid.UUID(str(value)))
                except (TypeError, ValueError):
                    continue
    knowledge_points = {
        item.id: item
        for item in db.scalars(
            select(KnowledgePoint).where(
                KnowledgePoint.id.in_(knowledge_point_ids),
                KnowledgePoint.owner_id.in_(owner_ids),
            )
        ).all()
    }
    for release_index, (release, _item, snapshot, assignment) in enumerate(rows, start=1):
        results.append(
            ReleasedResultSummary(
                grade_release_id=f"release:{release_index}",
                assignment_title=assignment.title,
                published_at=(release.released_at or release.updated_at).isoformat(),
                awarded_points=(
                    Decimal(snapshot.total_score)
                    if release.release_mode != "feedback_only" and snapshot.total_score is not None
                    else None
                ),
                max_points=(
                    Decimal(snapshot.max_score) if release.release_mode != "feedback_only" else None
                ),
            )
        )
        for question_index, raw in enumerate(snapshot.details or [], start=1):
            feedback = ""
            if release.release_mode != "score_only":
                feedback = str(raw.get("final_feedback", raw.get("feedback")) or "")
            score_text = ""
            if release.release_mode != "feedback_only":
                try:
                    score_text = (
                        f"Published score {Decimal(str(raw.get('score')))} / "
                        f"{Decimal(str(raw.get('max_score')))}."
                    )
                except (InvalidOperation, TypeError, ValueError):
                    score_text = ""
            summary = " ".join(value for value in (score_text, feedback) if value).strip()
            if not summary:
                continue
            knowledge_labels: list[str] = []
            for value in raw.get("knowledge_point_ids") or []:
                try:
                    point = knowledge_points.get(uuid.UUID(str(value)))
                except (TypeError, ValueError):
                    point = None
                if point is None:
                    continue
                context = " · ".join(part for part in (point.subject, point.grade) if part)
                knowledge_labels.append(f"{point.name}（{context}）" if context else point.name)
            evidence.append(
                LearningEvidence(
                    evidence_id=f"evidence:{release_index}:{question_index}",
                    knowledge_point="；".join(knowledge_labels)[:200],
                    summary=summary[:2000],
                )
            )
    resources = _authorized_resources(db, analysis, rows)
    resources.sort(key=lambda item: (item.sort_order, str(item.id)))
    resource_refs = [
        LearningResourceRef(
            resource_id=f"resource:{index}",
            title=item.title,
            resource_type=item.resource_type,
        )
        for index, item in enumerate(resources, start=1)
    ]
    return StudentLearningAnalysisInput(
        source_hash=hashlib.sha256(f"{analysis.id}:{analysis.source_hash}".encode()).hexdigest(),
        released_results=results,
        evidence=evidence,
        available_resources=resource_refs,
    )


def _run_student_learning_analysis(
    analysis_id: str, *, allow_running_resume: bool = False
) -> dict[str, Any]:
    try:
        parsed_id = uuid.UUID(analysis_id)
    except ValueError:
        return {"status": "invalid_analysis_id"}
    settings = get_settings()
    with SessionLocal() as db:
        analysis = db.scalar(
            select(StudentLearningAnalysis)
            .where(StudentLearningAnalysis.id == parsed_id)
            .with_for_update()
        )
        if analysis is None:
            return {"status": "missing"}
        if analysis.status == "complete":
            return {"status": "already_processed"}
        if analysis.status == "running" and allow_running_resume:
            analysis.status = "queued"
        if analysis.status != "queued":
            return {"status": "not_queued"}
        if not _active_student_link(db, analysis):
            analysis.status, analysis.error_code = "failed", "STUDENT_ACCOUNT_REVOKED"
            db.commit()
            return {"status": "failed", "error_code": analysis.error_code}
        rows = _released_rows(db, analysis)
        requested_ids = {str(value) for value in analysis.source_grade_release_ids}
        found_ids = {str(row[0].id) for row in rows}
        if not rows or found_ids != requested_ids:
            analysis.status, analysis.error_code = "failed", "AI_RELEASE_SOURCE_INVALID"
            db.commit()
            return {"status": "failed", "error_code": analysis.error_code}
        if _current_source_hash(db, analysis, rows) != analysis.source_hash:
            analysis.status, analysis.error_code = "failed", "AI_INPUT_STALE"
            db.commit()
            return {"status": "failed", "error_code": analysis.error_code}
        payload = _analysis_payload(db, analysis, rows)
        provider = provider_from_settings(settings)
        source_hash = analysis.source_hash
        student_id = analysis.student_id
        source_release_ids = tuple(str(value) for value in analysis.source_grade_release_ids)
        analysis.status = "running"
        analysis.provider = provider.name
        analysis.model = settings.student_learning_model
        analysis.prompt_version = settings.student_learning_prompt_version
        analysis.schema_version = settings.student_learning_schema_version
        db.commit()

        try:
            response = provider.analyze(
                payload.model_dump(mode="json"),
                safety_subject=str(student_id),
            )
        except Exception:
            response = StructuredProviderResult(None, error="provider_internal_error")

        db.expire_all()
        current = db.scalar(
            select(StudentLearningAnalysis)
            .where(StudentLearningAnalysis.id == parsed_id)
            .with_for_update()
            .execution_options(populate_existing=True)
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
        current_rows = _released_rows(db, current)
        current_release_ids = {str(row[0].id) for row in current_rows}
        if (
            current.status != "running"
            or current.source_hash != source_hash
            or current.student_id != student_id
            or tuple(str(value) for value in current.source_grade_release_ids) != source_release_ids
            or current_release_ids != set(source_release_ids)
            or not _active_student_link(db, current)
            or _current_source_hash(db, current, current_rows) != current.source_hash
        ):
            current.status = "failed"
            current.error_code = "AI_INPUT_STALE"
            db.commit()
            return {"status": "discarded_late"}
        if response.output is None:
            current.status = "failed"
            current.error_code = _stable_error_code(response.error)
            db.commit()
            return {"status": "failed", "error_code": current.error_code}
        current.content = response.output.model_dump(mode="json")
        current.evidence = [item.model_dump(mode="json") for item in payload.evidence]
        current.status = "complete"
        current.error_code = None
        current.generated_at = now_utc()
        db.commit()
        return {"status": "complete", "analysis_id": str(current.id)}


@celery_app.task(
    name="ahamark.student_learning_analysis.run",
    bind=True,
    soft_time_limit=240,
    time_limit=255,
)
def run_student_learning_analysis(self: Any, analysis_id: str) -> dict[str, Any]:
    delivery = self.request.delivery_info or {}
    try:
        return run_traced_task(
            self,
            analysis_id,
            lambda: _run_student_learning_analysis(
                analysis_id, allow_running_resume=bool(delivery.get("redelivered"))
            ),
        )
    except Exception:
        try:
            parsed_id = uuid.UUID(analysis_id)
        except ValueError:
            return {"status": "invalid_analysis_id"}
        with SessionLocal() as db:
            analysis = db.scalar(
                select(StudentLearningAnalysis)
                .where(StudentLearningAnalysis.id == parsed_id)
                .with_for_update()
            )
            if analysis is not None and analysis.status in {"queued", "running"}:
                analysis.status = "failed"
                analysis.error_code = "AI_WORKER_INTERNAL_ERROR"
                db.commit()
        return {"status": "failed", "error_code": "AI_WORKER_INTERNAL_ERROR"}
