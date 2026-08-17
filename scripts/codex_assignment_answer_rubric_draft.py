"""Materialize reviewed local Codex answer/rubric suggestions for formal draft questions.

This tool is suggestion-only. It never accepts or confirms candidates, activates a
Structured Rubric Set, publishes an assignment, or writes grades.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from app.api.domain import audit
from app.assignment_generation.answer_rubric import AnswerRubricProviderOutput
from app.assignment_generation.materializers import materialize_answer, materialize_rubric
from app.assignment_generation.service import update_risk_summary
from app.assignment_generation.snapshot import canonical_hash
from app.db.session import SessionLocal
from app.models import (
    Assignment,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    GenerationIssue,
    GenerationStageResult,
    Question,
    QuestionStatus,
    now_utc,
)
from sqlalchemy import func, select


def apply_draft(job_id: uuid.UUID, payload_path: Path) -> dict[str, object]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    expected_snapshot = str(payload.pop("source_snapshot_hash"))
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("items must be a non-empty list")
    numbers = [str(item.get("question_number", "")).strip() for item in raw_items]
    if any(not number for number in numbers) or len(numbers) != len(set(numbers)):
        raise ValueError("question numbers must be non-empty and unique")

    with SessionLocal.begin() as db:
        job = db.scalar(
            select(AssignmentGenerationJob)
            .where(AssignmentGenerationJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise ValueError("generation job not found")
        revision = db.scalar(
            select(AssignmentDraftRevision)
            .where(AssignmentDraftRevision.generation_job_id == job.id)
            .with_for_update()
        )
        if revision is None:
            raise ValueError("draft revision not found")
        if job.status not in {"partial", "review_required"}:
            raise ValueError(f"job status does not accept Codex draft: {job.status}")
        if (
            job.source_snapshot_hash != expected_snapshot
            or revision.source_snapshot_hash != expected_snapshot
        ):
            raise ValueError("source snapshot changed; refusing stale Codex draft")
        assignment = db.get(Assignment, job.assignment_id)
        if assignment is None or assignment.active_paper_version_id is None:
            raise ValueError("active paper version not found")
        questions = list(
            db.scalars(
                select(Question).where(
                    Question.paper_version_id == assignment.active_paper_version_id,
                    Question.status == QuestionStatus.active,
                )
            )
        )
        by_number = {question.question_number: question for question in questions}
        if len(by_number) != len(questions) or set(numbers) != set(by_number):
            raise ValueError("payload must cover every active question exactly once")

        created = 0
        for item in raw_items:
            number = str(item["question_number"]).strip()
            output = AnswerRubricProviderOutput.model_validate(item["output"])
            question = by_number[number]
            answer = materialize_answer(
                db,
                job,
                revision,
                question,
                output,
                {
                    "provider": "codex_local",
                    "model": "local-reviewed-document-draft",
                    "synthetic_evidence": True,
                    "source_kind": "teacher_uploaded_reference_answer",
                },
            )
            materialize_rubric(db, job, revision, question, answer, output)
            created += 1

        next_generation = (
            db.scalar(
                select(func.max(GenerationStageResult.stage_generation)).where(
                    GenerationStageResult.job_id == job.id,
                    GenerationStageResult.stage == "generating_rubrics",
                )
            )
            or 0
        ) + 1
        result_payload = {
            "kind": "answer_rubric_candidates",
            "stage": "generating_rubrics",
            "capability": "codex_local",
            "question_count": created,
            "created": created,
            "manual_required": created,
            "draft_only": True,
            "teacher_confirmation_required": True,
        }
        now = now_utc()
        db.add(
            GenerationStageResult(
                job_id=job.id,
                stage="generating_rubrics",
                stage_generation=next_generation,
                status="completed",
                expected_teacher_edit_version=revision.teacher_edit_version,
                input_hash=canonical_hash(
                    {"source_snapshot_hash": expected_snapshot, "payload": payload}
                ),
                output_hash=canonical_hash(result_payload),
                result_payload=result_payload,
                started_at=now,
                completed_at=now,
            )
        )
        for issue in db.scalars(
            select(GenerationIssue).where(
                GenerationIssue.job_id == job.id,
                GenerationIssue.stage == "generating_rubrics",
                GenerationIssue.code.in_({"PROVIDER_UNAVAILABLE", "CODEX_DRAFT_PENDING"}),
                GenerationIssue.resolution_status == "open",
            )
        ):
            issue.resolution_status = "resolved"
            issue.resolved_at = now
            issue.resolution_note = "当前本地 Codex 任务已生成答案与评分标准候选；仍须教师确认。"
        draft_payload = dict(revision.draft_payload or {})
        draft_payload["stages"] = {
            **dict(draft_payload.get("stages") or {}),
            "generating_rubrics": result_payload,
        }
        revision.draft_payload = draft_payload
        revision.status = "review_required"
        job.status = "review_required"
        job.current_stage = "generating_rubrics"
        job.progress = 100
        job.retryable = False
        job.error_code = None
        job.error_message = None
        audit(
            db,
            job.owner_id,
            "assignment_generation.codex_answer_rubric_draft",
            "assignment_generation_job",
            job.id,
            {
                "revision_id": str(revision.id),
                "source_snapshot_hash": expected_snapshot,
                "question_count": created,
                "draft_only": True,
                "teacher_confirmation_required": True,
            },
        )
        db.flush()
        update_risk_summary(db, revision)
        return {
            "job_id": str(job.id),
            "revision_id": str(revision.id),
            "status": job.status,
            "created": created,
            "risk_summary": revision.risk_summary,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id", type=uuid.UUID)
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    print(json.dumps(apply_draft(args.job_id, args.payload), ensure_ascii=False))


if __name__ == "__main__":
    main()
