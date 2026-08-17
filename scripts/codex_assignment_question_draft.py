"""Materialize a local Codex question-extraction draft into an existing job.

This tool is intentionally suggestion-only. It never accepts candidates,
creates final questions, confirms answer sources, generates final rubrics, or
publishes an assignment.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from app.api.domain import audit
from app.assignment_generation.materializers import materialize_questions
from app.assignment_generation.question_extraction import ExtractionOutput
from app.assignment_generation.service import update_risk_summary
from app.assignment_generation.snapshot import canonical_hash
from app.db.session import SessionLocal
from app.models import (
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AssignmentQuestionExtractionCandidate,
    GenerationIssue,
    GenerationStageResult,
    now_utc,
)
from pydantic import ValidationError
from sqlalchemy import func, select


def load_payload(payload_path: Path) -> tuple[str, ExtractionOutput, dict[str, Any]]:
    """Load and validate an ASCII-safe-error draft before opening a database transaction."""

    try:
        raw = payload_path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("INVALID_UTF8_PAYLOAD") from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("INVALID_JSON_PAYLOAD") from None
    if not isinstance(payload, dict):
        raise ValueError("INVALID_QUESTION_DRAFT_PAYLOAD")
    payload = dict(payload)
    expected_snapshot = payload.pop("source_snapshot_hash", None)
    if not isinstance(expected_snapshot, str) or not expected_snapshot:
        raise ValueError("INVALID_QUESTION_DRAFT_PAYLOAD")
    try:
        extraction = ExtractionOutput.model_validate(payload)
    except ValidationError as exc:
        if "CHARACTER_ENCODING_CORRUPTION_DETECTED" in str(exc):
            raise ValueError("CHARACTER_ENCODING_CORRUPTION_DETECTED") from None
        raise ValueError("INVALID_QUESTION_DRAFT_PAYLOAD") from None
    return expected_snapshot, extraction, payload


def apply_draft(job_id: uuid.UUID, payload_path: Path) -> dict[str, object]:
    expected_snapshot, extraction, payload = load_payload(payload_path)

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

        counts = materialize_questions(db, job, revision, extraction)
        latest_version = db.scalar(
            select(func.max(AssignmentQuestionExtractionCandidate.candidate_version)).where(
                AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id
            )
        )
        if latest_version is not None:
            for candidate in db.scalars(
                select(AssignmentQuestionExtractionCandidate).where(
                    AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
                    AssignmentQuestionExtractionCandidate.candidate_version == latest_version,
                )
            ):
                candidate.extraction_method = "codex_local"

        next_stage_generation = (
            db.scalar(
                select(func.max(GenerationStageResult.stage_generation)).where(
                    GenerationStageResult.job_id == job.id,
                    GenerationStageResult.stage == "extracting_questions",
                )
            )
            or 0
        ) + 1
        result_payload = {
            "kind": "question_extraction_candidates",
            "stage": "extracting_questions",
            "capability": "codex_local",
            "created": counts["created"],
            "manual_required": counts["manual_required"],
            "draft_only": True,
            "teacher_confirmation_required": True,
        }
        now = now_utc()
        db.add(
            GenerationStageResult(
                job_id=job.id,
                stage="extracting_questions",
                stage_generation=next_stage_generation,
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
                GenerationIssue.stage == "extracting_questions",
                GenerationIssue.code.in_({"PROVIDER_UNAVAILABLE", "CODEX_DRAFT_PENDING"}),
                GenerationIssue.resolution_status == "open",
            )
        ):
            issue.resolution_status = "resolved"
            issue.resolved_at = now
            issue.resolution_note = "当前本地 Codex 任务已生成题目候选；仍须教师确认。"

        draft_payload = dict(revision.draft_payload or {})
        stages = dict(draft_payload.get("stages") or {})
        stages["extracting_questions"] = result_payload
        draft_payload["stages"] = stages
        draft_payload["notice"] = "本地 Codex 已生成题目建议；教师确认前不会写入正式题目。"
        revision.draft_payload = draft_payload
        revision.status = "review_required"
        job.status = "review_required"
        job.current_stage = "extracting_questions"
        job.progress = 100
        job.retryable = False
        job.error_code = None
        job.error_message = None
        audit(
            db,
            job.owner_id,
            "assignment_generation.codex_question_draft",
            "assignment_generation_job",
            job.id,
            {
                "revision_id": str(revision.id),
                "source_snapshot_hash": expected_snapshot,
                "candidate_count": counts["created"],
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
            "created": counts["created"],
            "risk_summary": revision.risk_summary,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id", type=uuid.UUID)
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    try:
        result = apply_draft(args.job_id, args.payload)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, ensure_ascii=True), file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
