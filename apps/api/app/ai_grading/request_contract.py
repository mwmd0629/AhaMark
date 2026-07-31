from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_grading.guards import GuardViolation
from app.models import (
    QuestionRecognitionEvidence,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionRecognitionBlock,
)


def scoring_input_version(evidence: QuestionRecognitionEvidence) -> str:
    return (
        f"{evidence.input_hash}:{evidence.recognition_version}:"
        f"{evidence.confirmed_revision or 0}"
    )


def strict_request_hash(
    *,
    answer: StudentAnswer,
    evidence: QuestionRecognitionEvidence,
    rubric_id: uuid.UUID,
    rubric_content_hash: str,
    reference_id: uuid.UUID,
    reference_content_hash: str,
    validation_id: uuid.UUID | None,
    criterion_stable_key: str | None,
    provider: str,
    model: str | None,
    endpoint_mode: str,
    prompt_version: str,
    schema_version: str,
    provider_config_version: str,
    grading_config_version: str,
) -> str:
    payload = {
        "answer": str(answer.id),
        "answer_version": answer.question_version_reference,
        "evidence_id": str(evidence.id),
        "scoring_input_version": scoring_input_version(evidence),
        "rubric_id": str(rubric_id),
        "rubric_hash": rubric_content_hash,
        "reference_id": str(reference_id),
        "reference_hash": reference_content_hash,
        "validation_id": str(validation_id) if validation_id else None,
        "criterion_stable_key": criterion_stable_key,
        "provider": provider,
        "model": model,
        "endpoint_mode": endpoint_mode,
        "prompt": prompt_version,
        "schema": schema_version,
        "provider_config": provider_config_version,
        "grading_config": grading_config_version,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def require_current_recognition_evidence(
    db: Session,
    *,
    answer: StudentAnswer,
    submission: Submission,
    owner_id: uuid.UUID,
) -> QuestionRecognitionEvidence:
    evidence = db.scalar(
        select(QuestionRecognitionEvidence)
        .where(
            QuestionRecognitionEvidence.student_answer_id == answer.id,
            QuestionRecognitionEvidence.status == "confirmed",
            QuestionRecognitionEvidence.stale_at.is_(None),
        )
        .order_by(
            QuestionRecognitionEvidence.recognition_version.desc(),
            QuestionRecognitionEvidence.created_at.desc(),
        )
    )
    if (
        evidence is None
        or evidence.owner_id != owner_id
        or evidence.submission_id != submission.id
        or not evidence.block_sources
    ):
        raise GuardViolation(
            "AI_INPUT_NOT_CONFIRMED",
            "Current confirmed recognition evidence is required",
        )
    regions = list(
        db.scalars(
            select(StudentAnswerRegion).where(
                StudentAnswerRegion.student_answer_id == answer.id,
                StudentAnswerRegion.status == "confirmed",
            )
        ).all()
    )
    regions_by_id = {str(region.id): region for region in regions}
    source_region_ids: set[str] = set()
    for source in evidence.block_sources:
        if not isinstance(source, dict):
            raise GuardViolation("EVIDENCE_STALE", "Recognition evidence is stale")
        region = regions_by_id.get(str(source.get("region_id")))
        try:
            block_id = uuid.UUID(str(source.get("block_id")))
        except (TypeError, ValueError) as exc:
            raise GuardViolation("EVIDENCE_STALE", "Recognition evidence is stale") from exc
        block = db.get(SubmissionRecognitionBlock, block_id)
        if (
            region is None
            or block is None
            or block.student_answer_region_id != region.id
            or block.stale_at is not None
            or source.get("region_version") != region.region_version
            or source.get("block_recognition_version") != block.recognition_version
        ):
            raise GuardViolation("EVIDENCE_STALE", "Recognition evidence is stale")
        source_region_ids.add(str(region.id))
    if not regions or source_region_ids != set(regions_by_id):
        raise GuardViolation("EVIDENCE_STALE", "Recognition evidence is stale")
    return evidence
