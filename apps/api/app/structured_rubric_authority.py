from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.assignment_central_review import validate_current_structured_set_under_locks
from app.models import (
    Assignment,
    AssignmentReviewSession,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricSet,
    StructuredRubricSetItem,
    StructuredRubricVersion,
)


class StructuredRubricAuthorityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActiveStructuredRubricAuthority:
    rubric_set: StructuredRubricSet
    item: StructuredRubricSetItem
    reference: ReferenceAnswerVersion
    rubric: StructuredRubricVersion
    criteria: tuple[RubricCriterion, ...]


def require_active_structured_rubric(
    db: Session,
    *,
    assignment: Assignment,
    question_id: uuid.UUID,
    owner_id: uuid.UUID,
    lock: bool = False,
) -> ActiveStructuredRubricAuthority:
    if assignment.owner_id != owner_id or assignment.active_structured_rubric_set_id is None:
        raise StructuredRubricAuthorityError(
            "STRUCTURED_SET_REQUIRED", "Assignment has no active Structured Rubric Set"
        )
    session = db.scalar(
        select(AssignmentReviewSession)
        .where(
            AssignmentReviewSession.assignment_id == assignment.id,
            AssignmentReviewSession.owner_id == owner_id,
            AssignmentReviewSession.structured_rubric_set_id
            == assignment.active_structured_rubric_set_id,
            AssignmentReviewSession.invalidated_at.is_(None),
            AssignmentReviewSession.status == "published",
        )
        .order_by(AssignmentReviewSession.review_version.desc())
    )
    if session is None:
        raise StructuredRubricAuthorityError(
            "STRUCTURED_SET_REQUIRED", "Published Structured Rubric Set review is required"
        )
    validation = validate_current_structured_set_under_locks(
        db,
        session,
        rubric_set_id=assignment.active_structured_rubric_set_id,
        lock=lock,
        require_confirmed=True,
        require_current_selection=False,
    )
    rubric_set = validation.rubric_set
    if not validation.current or rubric_set is None:
        raise StructuredRubricAuthorityError(
            "STRUCTURED_SET_STALE", validation.reason or "Active Structured Rubric Set is stale"
        )
    item = db.scalar(
        select(StructuredRubricSetItem).where(
            StructuredRubricSetItem.rubric_set_id == rubric_set.id,
            StructuredRubricSetItem.question_id == question_id,
        )
    )
    if item is None:
        raise StructuredRubricAuthorityError(
            "STRUCTURED_SET_STALE", "Question is absent from the active Structured Rubric Set"
        )
    reference = db.get(ReferenceAnswerVersion, item.reference_answer_version_id)
    rubric = db.get(StructuredRubricVersion, item.structured_rubric_version_id)
    criteria = tuple(
        db.scalars(
            select(RubricCriterion)
            .where(RubricCriterion.rubric_version_id == item.structured_rubric_version_id)
            .order_by(RubricCriterion.display_order, RubricCriterion.id)
        )
    )
    if reference is None or rubric is None or not criteria:
        raise StructuredRubricAuthorityError(
            "STRUCTURED_SET_STALE", "Active Structured Rubric Set formals are missing"
        )
    return ActiveStructuredRubricAuthority(rubric_set, item, reference, rubric, criteria)


def require_job_authority(
    authority: ActiveStructuredRubricAuthority,
    *,
    structured_rubric_set_id: uuid.UUID,
    rubric_version_id: uuid.UUID,
    reference_answer_version_id: uuid.UUID,
) -> None:
    if (
        structured_rubric_set_id != authority.rubric_set.id
        or rubric_version_id != authority.rubric.id
        or reference_answer_version_id != authority.reference.id
    ):
        raise StructuredRubricAuthorityError(
            "STRUCTURED_SET_STALE", "Job no longer matches the active Structured Rubric Set item"
        )
