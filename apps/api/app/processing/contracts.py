from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from app.semantic_content import semantic_normalize

PROCESSING_INPUT_SCHEMA = "processing-input-v1"
PROCESSING_MANIFEST_SCHEMA = "processing-run-input-v1"
PROCESSING_COMMAND_SCHEMA = "processing-command-v1"


class ProcessingInputError(RuntimeError):
    """Stable domain failure; transport layers may map ``code`` independently."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonicalize(value: Any) -> Any:
    """Canonical JSON values while retaining snapshot identities and versions."""
    if isinstance(value, dict):
        return {str(key): canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return semantic_normalize(value)
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if isinstance(value, str):
        normalized = semantic_normalize(value)
        return value.lower() if normalized is None and _is_uuid(value) else normalized
    return value


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class ProcessingInputSnapshot:
    payload: dict[str, Any]
    input_version: str


def build_run_input_version(snapshots: Iterable[ProcessingInputSnapshot]) -> str:
    ordered = sorted(
        snapshots,
        key=lambda item: (
            item.payload["submission"]["id"],
            item.payload["answer"]["id"],
        ),
    )
    return canonical_hash(
        {
            "schema": PROCESSING_INPUT_SCHEMA,
            "answers": [
                {
                    "submission_id": item.payload["submission"]["id"],
                    "answer_id": item.payload["answer"]["id"],
                    "input_version": item.input_version,
                }
                for item in ordered
            ],
        }
    )


def build_request_hash(
    *,
    run_input_version: str,
    prompt_version: str,
    schema_version: str,
    config_version: str,
) -> str:
    return canonical_hash(
        {
            "operation": "continue_to_teacher_review",
            "mode": "codex_local",
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "config_version": config_version,
            "suggestion_only": True,
            "run_input_version": run_input_version,
        }
    )


def build_command_hash(
    *,
    operation: str,
    batch_id: uuid.UUID,
    source_run_id: uuid.UUID | None = None,
    expected_generation: int | None = None,
    step_ids: Iterable[uuid.UUID] = (),
) -> tuple[str, dict[str, Any]]:
    """Return the stable command hash and the exact canonical audit payload."""
    payload: dict[str, Any] = {
        "schema": PROCESSING_COMMAND_SCHEMA,
        "operation": operation,
        "batch_id": batch_id,
        "source_run_id": source_run_id,
        "expected_generation": expected_generation,
        "step_ids": sorted(str(step_id) for step_id in step_ids),
    }
    return canonical_hash(payload), canonicalize(payload)
