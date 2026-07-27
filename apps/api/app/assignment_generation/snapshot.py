import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Assignment, PaperPage, PaperVersion, StoredFile


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def source_snapshot_payload(db: Session, assignment: Assignment) -> dict[str, Any]:
    settings = get_settings()
    paper = None
    if assignment.active_paper_version_id:
        paper = db.get(PaperVersion, assignment.active_paper_version_id)
    if paper is None:
        paper = db.scalar(
            select(PaperVersion)
            .where(PaperVersion.assignment_id == assignment.id)
            .order_by(PaperVersion.version.desc())
            .limit(1)
        )
    pages: list[dict[str, Any]] = []
    if paper is not None:
        for page, stored in db.execute(
            select(PaperPage, StoredFile)
            .join(StoredFile, StoredFile.id == PaperPage.stored_file_id)
            .where(PaperPage.paper_version_id == paper.id)
            .order_by(PaperPage.page_number, PaperPage.id)
        ):
            pages.append(
                {
                    "id": page.id,
                    "file_id": stored.id,
                    "file_checksum": stored.checksum,
                    "file_status": stored.status,
                    "page_number": page.page_number,
                    "source_page_number": page.source_page_number,
                    "rotation": page.rotation,
                    "status": page.status,
                }
            )
    return {
        "assignment": {
            "id": assignment.id,
            "title": assignment.title,
            "subject": assignment.subject,
            "grade": assignment.grade,
            "description": assignment.description,
            "instructions": assignment.instructions,
            "total_score": assignment.total_score,
            "due_at": assignment.due_at,
        },
        "paper_version": (
            {"id": paper.id, "version": paper.version, "status": paper.status} if paper else None
        ),
        "pages": pages,
        "config": {
            "provider_config_version": settings.assignment_generation_provider_config_version,
            "prompt_version": settings.assignment_generation_prompt_version,
            "schema_version": settings.assignment_generation_schema_version,
        },
    }


def source_snapshot_hash(db: Session, assignment: Assignment) -> str:
    return canonical_hash(source_snapshot_payload(db, assignment))
