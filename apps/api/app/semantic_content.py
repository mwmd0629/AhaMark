"""Versioned canonicalization for teacher-visible semantic content."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

SEMANTIC_CONTENT_HASH_VERSION = "semantic-content-v1"

_IDENTITY_KEYS = {
    "actor",
    "actor_id",
    "candidate_id",
    "created_at",
    "created_by",
    "generation",
    "generation_id",
    "id",
    "materialization_key",
    "reviewed_at",
    "reviewed_by",
    "source_snapshot_hash",
    "teacher_reviewed_by",
    "timestamp",
    "updated_at",
    "version",
}
_DROP = object()


def semantic_normalize(value: Any) -> Any:
    """Recursively normalize content while removing technical identity metadata."""
    normalized = _semantic_normalize(value)
    return None if normalized is _DROP else normalized


def _semantic_normalize(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if _is_identity_key(str(key)):
                continue
            normalized = _semantic_normalize(item)
            if normalized is not _DROP:
                out[str(key)] = normalized
        return _DROP if value and not out else out
    if isinstance(value, (list, tuple)):
        list_out = [
            normalized
            for item in value
            if (normalized := _semantic_normalize(item)) is not _DROP
        ]
        return _DROP if value and not list_out else list_out
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if isinstance(value, (datetime, uuid.UUID)):
        return _DROP
    if isinstance(value, str):
        try:
            if str(uuid.UUID(value)) == value.lower():
                return _DROP
        except ValueError:
            pass
        text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line.rstrip() for line in text.split("\n")).rstrip()
    if isinstance(value, Enum):
        return semantic_normalize(value.value)
    return value


def _is_identity_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _IDENTITY_KEYS or lowered.endswith(("_id", "_ids", "_at", "_version"))


def semantic_hash(value: Any) -> str:
    envelope = {
        "hash_version": SEMANTIC_CONTENT_HASH_VERSION,
        "content": semantic_normalize(value),
    }
    return hashlib.sha256(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def reference_answer_semantic_payload(
    *,
    source_type: str,
    source_region: Any,
    raw_content: str,
    normalized_content: str,
    structured_content: Any,
    alternative_answers: Any,
    provenance: Any,
) -> dict[str, Any]:
    return {
        "schema": "reference-answer-content-v1",
        "source_type": source_type,
        "source_region": semantic_normalize(source_region),
        "normalized_content": normalized_content,
        "structured_content": semantic_normalize(structured_content),
        "alternative_answers": semantic_normalize(alternative_answers),
        "provenance": semantic_normalize(provenance),
    }


def structured_rubric_semantic_payload(
    *,
    reference_answer_content_hash: str,
    title: str,
    scoring_mode: str,
    total_points: Any,
    allow_partial_credit: bool,
    domain_requirements: Any,
    validation_config: Any,
    common_error_types: Any,
    feedback_templates: Any,
    manual_required: bool,
    criteria: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "structured-rubric-content-v1",
        "reference_answer_content_hash": reference_answer_content_hash,
        "title": title,
        "scoring_mode": scoring_mode,
        "total_points": str(total_points),
        "allow_partial_credit": allow_partial_credit,
        "domain_requirements": semantic_normalize(domain_requirements),
        "validation_config": semantic_normalize(validation_config),
        "common_error_types": semantic_normalize(common_error_types),
        "feedback_templates": semantic_normalize(feedback_templates),
        "manual_required": manual_required,
        "criteria": semantic_normalize(criteria),
    }
