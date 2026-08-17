import uuid
from typing import Annotated

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.assignment_generation.snapshot import source_snapshot_hash
from app.assignment_generation.textbook_sources import auto_match_available_solutions
from app.db.session import get_db
from app.models import (
    Assignment,
    AssignmentDraftRevision,
    AssignmentStatus,
    AssignmentTextbookLibrarySelection,
    TextbookLibrary,
    TextbookLibraryQuestion,
    TextbookSourceMatchCandidate,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["textbook-libraries"])
Db = Annotated[Session, Depends(get_db)]


class TextbookLibrarySelectionInput(BaseModel):
    draft_revision_id: uuid.UUID
    expected_draft_revision_edit_version: int = Field(ge=0)
    expected_source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    library_ids: list[uuid.UUID] = Field(max_length=20)


def _library_json(db: Session, row: TextbookLibrary) -> dict[str, object]:
    counts: dict[str, int] = {}
    for status, count in db.execute(
        select(TextbookLibraryQuestion.status, func.count(TextbookLibraryQuestion.id))
        .where(TextbookLibraryQuestion.library_id == row.id)
        .group_by(TextbookLibraryQuestion.status)
    ):
        counts[status] = int(count)
    return {
        "id": str(row.id),
        "title": row.title,
        "volume_label": row.volume_label,
        "question_count": row.question_count,
        "usable_question_count": int(counts.get("suggested", 0)),
        "review_question_count": int(counts.get("manual_required", 0)),
        "status": row.status,
    }


@router.get("/textbook-libraries")
def list_textbook_libraries(db: Db, actor: Actor) -> list[dict[str, object]]:
    rows = list(
        db.scalars(
            select(TextbookLibrary)
            .where(TextbookLibrary.owner_id == actor.id, TextbookLibrary.status == "ready")
            .order_by(TextbookLibrary.title, TextbookLibrary.volume_label, TextbookLibrary.id)
        )
    )
    return [_library_json(db, row) for row in rows]


@router.get("/assignments/{assignment_id}/textbook-library-selections")
def list_textbook_library_selections(assignment_id: uuid.UUID, db: Db, actor: Actor) -> list[str]:
    assignment = db.scalar(
        select(Assignment.id).where(
            Assignment.id == assignment_id,
            Assignment.owner_id == actor.id,
        )
    )
    if assignment is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    return [
        str(value)
        for value in db.scalars(
            select(AssignmentTextbookLibrarySelection.library_id)
            .where(
                AssignmentTextbookLibrarySelection.assignment_id == assignment_id,
                AssignmentTextbookLibrarySelection.owner_id == actor.id,
            )
            .order_by(AssignmentTextbookLibrarySelection.library_id)
        )
    ]


@router.put("/assignments/{assignment_id}/textbook-library-selections")
def replace_textbook_library_selections(
    assignment_id: uuid.UUID,
    data: TextbookLibrarySelectionInput,
    db: Db,
    actor: Actor,
) -> dict[str, object]:
    assignment = db.scalar(
        select(Assignment)
        .where(Assignment.id == assignment_id, Assignment.owner_id == actor.id)
        .with_for_update()
    )
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(
            AssignmentDraftRevision.id == data.draft_revision_id,
            AssignmentDraftRevision.assignment_id == assignment_id,
            AssignmentDraftRevision.owner_id == actor.id,
        )
        .with_for_update()
    )
    if assignment is None or revision is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业或草稿不存在")
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "只能为草稿作业选择教材")
    if (
        revision.teacher_edit_version != data.expected_draft_revision_edit_version
        or revision.source_snapshot_hash != data.expected_source_snapshot
        or source_snapshot_hash(db, assignment) != data.expected_source_snapshot
    ):
        raise ApiProblem(409, "TEXTBOOK_LIBRARY_SELECTION_STALE", "作业内容已变化，请刷新后重试")
    requested_ids = list(dict.fromkeys(data.library_ids))
    libraries = list(
        db.scalars(
            select(TextbookLibrary).where(
                TextbookLibrary.id.in_(requested_ids),
                TextbookLibrary.owner_id == actor.id,
                TextbookLibrary.status == "ready",
            )
        )
    )
    if len(libraries) != len(requested_ids):
        raise ApiProblem(422, "TEXTBOOK_LIBRARY_INVALID", "所选教材不存在或不可用")
    db.execute(
        delete(AssignmentTextbookLibrarySelection).where(
            AssignmentTextbookLibrarySelection.assignment_id == assignment.id
        )
    )
    for library in libraries:
        db.add(
            AssignmentTextbookLibrarySelection(
                owner_id=actor.id,
                assignment_id=assignment.id,
                library_id=library.id,
                selected_by=actor.id,
            )
        )
    db.execute(
        update(TextbookSourceMatchCandidate)
        .where(
            TextbookSourceMatchCandidate.assignment_id == assignment.id,
            TextbookSourceMatchCandidate.library_question_id.is_not(None),
            TextbookSourceMatchCandidate.status == "suggested",
        )
        .values(status="superseded")
    )
    db.flush()
    match_result = auto_match_available_solutions(
        db,
        assignment=assignment,
        revision=revision,
    )
    revision.teacher_edit_version += 1
    audit(
        db,
        actor.id,
        "textbook_library_selection.replace",
        "assignment",
        assignment.id,
        {
            "library_ids": [str(row.id) for row in libraries],
            "created_matches": match_result["created"],
        },
    )
    db.commit()
    return {
        "selected_library_ids": [str(row.id) for row in libraries],
        "created_matches": match_result["created"],
        "draft_revision_edit_version": revision.teacher_edit_version,
    }
