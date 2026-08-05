from __future__ import annotations

import uuid
from decimal import Decimal

from app.api.assignment_central_review import (
    _answer_content_payload,
    _criterion_payload,
    _rubric_content_payload,
)
from app.models import (
    Assignment,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricSet,
    StructuredRubricSetItem,
    StructuredRubricVersion,
    now_utc,
)
from app.question_versions import question_version_token
from app.semantic_content import semantic_hash
from sqlalchemy.orm import Session


def activate_structured_rubric_set(
    db: Session,
    assignment: Assignment,
    questions: list[Question],
    *,
    actor_id: uuid.UUID,
    answers: dict[uuid.UUID, str] | None = None,
    set_status: str = "active",
    set_version: int = 1,
    answer_version: int = 1,
    rubric_version: int = 1,
    criterion_validation_mode: str = "manual_only",
) -> tuple[StructuredRubricSet, dict[uuid.UUID, StructuredRubricSetItem]]:
    """Build an explicit immutable Structured Set fixture without using legacy projection."""
    if assignment.active_paper_version_id is None:
        raise AssertionError("assignment must have an active paper version")
    answer_values = answers or {}
    item_sources: list[
        tuple[
            Question,
            ReferenceAnswerVersion,
            StructuredRubricVersion,
            tuple[RubricCriterion, ...],
        ]
    ] = []
    for question in questions:
        if question.max_score is None:
            raise AssertionError("question must have max_score")
        answer_text = answer_values.get(question.id, "1. 测试题")
        answer = ReferenceAnswerVersion(
            question_id=question.id,
            source_type="teacher",
            raw_content=answer_text,
            normalized_content=answer_text,
            structured_content={"alternative_answers": []},
            content_hash=semantic_hash({"answer": answer_text}),
            version=answer_version,
            provenance={"fixture": "structured-only"},
            created_by=actor_id,
            status="confirmed",
            teacher_confirmed_at=now_utc(),
        )
        db.add(answer)
        db.flush()
        rubric = StructuredRubricVersion(
            question_id=question.id,
            question_version=question_version_token(question),
            reference_answer_version_id=answer.id,
            rubric_version=rubric_version,
            title=f"第 {question.question_number} 题评分标准",
            total_points=question.max_score,
            status="confirmed",
            content_hash="pending",
            created_by=actor_id,
            confirmed_by=actor_id,
            confirmed_at=now_utc(),
        )
        db.add(rubric)
        db.flush()
        criterion = RubricCriterion(
            rubric_version_id=rubric.id,
            stable_key="answer",
            title="答案正确",
            description="答案与固定参考答案一致",
            max_points=question.max_score,
            display_order=1,
            criterion_type="answer",
            required=True,
            dependencies=[],
            expected_evidence={"source": "student_answer"},
            validation_mode=criterion_validation_mode,
            manual_review_policy={},
            partial_credit_policy={},
            validation_rule=(
                {
                    "answer_type": "exact_scalar",
                    "domain": "rational",
                    "limits": {"timeout_ms": 500},
                }
                if criterion_validation_mode == "deterministic"
                else {}
            ),
            metadata_={"fixture": "structured-only"},
        )
        db.add(criterion)
        db.flush()
        rubric.content_hash = semantic_hash(_rubric_content_payload(db, rubric))
        item_sources.append((question, answer, rubric, (criterion,)))

    source_snapshot_hash = semantic_hash(
        {
            "assignment_id": str(assignment.id),
            "paper_version_id": str(assignment.active_paper_version_id),
            "questions": [str(question.id) for question in questions],
        }
    )
    rubric_set = StructuredRubricSet(
        owner_id=assignment.owner_id,
        assignment_id=assignment.id,
        paper_version_id=assignment.active_paper_version_id,
        version=set_version,
        status=set_status,
        content_hash=uuid.uuid4().hex * 2,
        source_snapshot_hash=source_snapshot_hash,
        total_points=sum((Decimal(question.max_score) for question in questions), Decimal("0")),
        created_by=actor_id,
        confirmed_by=actor_id if set_status == "active" else None,
        confirmed_at=now_utc() if set_status == "active" else None,
        activated_at=now_utc() if set_status == "active" else None,
    )
    db.add(rubric_set)
    db.flush()
    items: dict[uuid.UUID, StructuredRubricSetItem] = {}
    for display_order, (question, answer, rubric, criteria) in enumerate(item_sources, start=1):
        item = StructuredRubricSetItem(
            rubric_set_id=rubric_set.id,
            question_id=question.id,
            question_version=rubric.question_version,
            reference_answer_version_id=answer.id,
            structured_rubric_version_id=rubric.id,
            answer_content_hash=semantic_hash(_answer_content_payload(answer)),
            rubric_content_hash=semantic_hash(_rubric_content_payload(db, rubric)),
            criteria_hash=semantic_hash([_criterion_payload(value) for value in criteria]),
            display_order=display_order,
            max_points=question.max_score,
        )
        db.add(item)
        items[question.id] = item
    db.flush()
    rubric_set.content_hash = semantic_hash(
        {
            "assignment_id": str(rubric_set.assignment_id),
            "paper_version_id": str(rubric_set.paper_version_id),
            "source_snapshot_hash": rubric_set.source_snapshot_hash,
            "total_points": str(rubric_set.total_points),
            "items": [
                {
                    "question_id": str(item.question_id),
                    "question_version": item.question_version,
                    "reference_answer_version_id": str(item.reference_answer_version_id),
                    "structured_rubric_version_id": str(item.structured_rubric_version_id),
                    "answer_content_hash": item.answer_content_hash,
                    "rubric_content_hash": item.rubric_content_hash,
                    "criteria_hash": item.criteria_hash,
                    "display_order": item.display_order,
                    "max_points": str(item.max_points),
                }
                for item in items.values()
            ],
        }
    )
    assignment.active_structured_rubric_set_id = rubric_set.id
    db.commit()
    return rubric_set, items
