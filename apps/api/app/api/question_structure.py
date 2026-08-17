import hashlib
import json
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.db.session import get_db
from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentRubricDraftCandidate,
    AssignmentStatus,
    PaperPage,
    Question,
    QuestionRegion,
    QuestionStatus,
    QuestionStructureItem,
    QuestionStructureReview,
    ReferenceAnswerVersion,
    StructuredRubricVersion,
    StudentAnswer,
    now_utc,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["question-structure"])
Db = Annotated[Session, Depends(get_db)]

_NUMBER_RE = re.compile(r"^\s*(\d+)\s*(?:[（(]\s*(\d+)\s*[）)])?\s*$")


class StructureItemInput(BaseModel):
    question_id: uuid.UUID
    display_number: str = Field(min_length=1, max_length=40)
    display_order: int = Field(gt=0)
    action: Literal["keep", "remove"] = "keep"
    max_score: Decimal | None = Field(None, gt=0)
    source_kind: Literal["pdf_text", "ocr", "manual", "existing"] = "manual"
    confidence: Decimal | None = Field(None, ge=0, le=1)


class StructureSaveInput(BaseModel):
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    score_policy: Literal["unconfirmed", "equal_weight", "manual", "template"]
    items: list[StructureItemInput] = Field(min_length=1)


class StructureConfirmInput(BaseModel):
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    explicit_confirmation: Literal[True]


class StructureSplitPartInput(BaseModel):
    display_number: str = Field(min_length=1, max_length=40)
    region_ids: list[uuid.UUID] = Field(min_length=1)


class StructureSplitInput(BaseModel):
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_question_id: uuid.UUID
    parts: list[StructureSplitPartInput] = Field(min_length=2)
    explicit_confirmation: Literal[True]


class StructureMergeInput(BaseModel):
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_ids: list[uuid.UUID] = Field(min_length=2)
    display_number: str = Field(min_length=1, max_length=40)
    explicit_confirmation: Literal[True]


def _owned_assignment(
    db: Session, assignment_id: uuid.UUID, actor_id: uuid.UUID, *, lock: bool = False
) -> Assignment:
    query = select(Assignment).where(
        Assignment.id == assignment_id,
        Assignment.owner_id == actor_id,
    )
    if lock:
        query = query.with_for_update()
    assignment = db.scalar(query)
    if assignment is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    if assignment.active_paper_version_id is None:
        raise ApiProblem(409, "ACTIVE_PAPER_REQUIRED", "请先整理试卷页面")
    return assignment


def _latest_review(
    db: Session, assignment: Assignment, *, lock: bool = False
) -> QuestionStructureReview | None:
    query = (
        select(QuestionStructureReview)
        .where(
            QuestionStructureReview.assignment_id == assignment.id,
            QuestionStructureReview.paper_version_id == assignment.active_paper_version_id,
        )
        .order_by(QuestionStructureReview.version.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def _question_map(db: Session, assignment: Assignment) -> dict[uuid.UUID, Question]:
    rows = db.scalars(
        select(Question).where(Question.paper_version_id == assignment.active_paper_version_id)
    ).all()
    return {row.id: row for row in rows}


def _parts(value: str) -> tuple[str, str | None, str | None]:
    match = _NUMBER_RE.fullmatch(value)
    if match is None:
        raise ApiProblem(
            422,
            "QUESTION_NUMBER_INVALID",
            "题号应使用 1、1(1) 或 12(2) 这类层级格式",
        )
    parent, sub = match.groups()
    return (f"{parent}({sub})" if sub else parent, parent if sub else None, sub)


def _item_payload(
    *,
    question_id: uuid.UUID,
    display_number: str,
    display_order: int,
    action: str,
    max_score: Decimal | None,
    source_kind: str,
    confidence: Decimal | None,
) -> dict[str, object]:
    normalized, parent, sub = _parts(display_number)
    return {
        "question_id": str(question_id),
        "display_number": normalized,
        "parent_number": parent,
        "sub_number": sub,
        "display_order": display_order,
        "action": action,
        "max_score": str(max_score) if max_score is not None else None,
        "source_kind": source_kind,
        "confidence": str(confidence) if confidence is not None else None,
    }


def _content_hash(score_policy: str, items: list[dict[str, object]]) -> str:
    stable_keys = (
        "question_id",
        "display_number",
        "parent_number",
        "sub_number",
        "display_order",
        "action",
        "max_score",
        "source_kind",
        "confidence",
    )
    payload = {
        "score_policy": score_policy,
        "items": sorted(
            [{key: item.get(key) for key in stable_keys} for item in items],
            key=lambda item: int(str(item["display_order"])),
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _review_items(db: Session, review_id: uuid.UUID) -> list[QuestionStructureItem]:
    return list(
        db.scalars(
            select(QuestionStructureItem)
            .where(QuestionStructureItem.review_id == review_id)
            .order_by(QuestionStructureItem.display_order, QuestionStructureItem.id)
        ).all()
    )


def _serialized_items(rows: list[QuestionStructureItem]) -> list[dict[str, object]]:
    return [
        _item_payload(
            question_id=row.question_id,
            display_number=row.display_number,
            display_order=row.display_order,
            action=row.action,
            max_score=Decimal(row.max_score) if row.max_score is not None else None,
            source_kind=row.source_kind,
            confidence=Decimal(row.confidence) if row.confidence is not None else None,
        )
        for row in rows
    ]


def _with_region_details(db: Session, items: list[dict[str, object]]) -> list[dict[str, object]]:
    question_ids = [uuid.UUID(str(item["question_id"])) for item in items]
    grouped: dict[uuid.UUID, list[dict[str, object]]] = {
        question_id: [] for question_id in question_ids
    }
    if question_ids:
        rows = db.execute(
            select(QuestionRegion, PaperPage)
            .join(PaperPage, PaperPage.id == QuestionRegion.paper_page_id)
            .where(QuestionRegion.question_id.in_(question_ids))
            .order_by(
                PaperPage.page_number,
                QuestionRegion.y,
                QuestionRegion.x,
                QuestionRegion.id,
            )
        ).all()
        for region, page in rows:
            grouped.setdefault(region.question_id, []).append(
                {
                    "id": str(region.id),
                    "paper_page_id": str(region.paper_page_id),
                    "page_number": page.page_number,
                    "x": str(region.x),
                    "y": str(region.y),
                    "width": str(region.width),
                    "height": str(region.height),
                    "source": region.source,
                    "confidence": str(region.confidence) if region.confidence is not None else None,
                }
            )
    result: list[dict[str, object]] = []
    for item in items:
        value = dict(item)
        regions = grouped.get(uuid.UUID(str(item["question_id"])), [])
        pages = {int(str(region["page_number"])) for region in regions}
        value["regions"] = regions
        value["region_count"] = len(regions)
        value["page_count"] = len(pages)
        value["spans_pages"] = len(pages) > 1
        result.append(value)
    return result


def _initial_items(db: Session, assignment: Assignment) -> list[dict[str, object]]:
    questions = db.scalars(
        select(Question)
        .where(
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
        .order_by(Question.display_order, Question.question_number, Question.id)
    ).all()
    return [
        _item_payload(
            question_id=row.id,
            display_number=row.question_number,
            display_order=index,
            action="keep",
            max_score=Decimal(row.max_score) if row.max_score is not None else None,
            source_kind="existing",
            confidence=None,
        )
        for index, row in enumerate(questions, start=1)
    ]


def _response(
    db: Session,
    review: QuestionStructureReview | None,
    items: list[dict[str, object]],
    *,
    score_policy: str,
) -> dict[str, object]:
    content_hash = review.content_hash if review is not None else _content_hash(score_policy, items)
    kept = [item for item in items if item["action"] == "keep"]
    return {
        "id": str(review.id) if review is not None else None,
        "version": review.version if review is not None else 0,
        "edit_version": review.edit_version if review is not None else 0,
        "status": review.status if review is not None else "unreviewed",
        "score_policy": score_policy,
        "content_hash": content_hash,
        "items": _with_region_details(db, items),
        "answer_unit_count": len(kept),
        "has_missing_scores": any(item["max_score"] is None for item in kept),
        "can_confirm": bool(kept) and score_policy != "unconfirmed",
    }


@router.get("/assignments/{assignment_id}/question-structure")
def get_question_structure(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, object]:
    assignment = _owned_assignment(db, assignment_id, actor.id)
    review = _latest_review(db, assignment)
    if review is None:
        items = _initial_items(db, assignment)
        return _response(db, None, items, score_policy="unconfirmed")
    return _response(
        db,
        review,
        _serialized_items(_review_items(db, review.id)),
        score_policy=review.score_policy,
    )


def _validate_save(
    data: StructureSaveInput,
    questions: dict[uuid.UUID, Question],
    expected_ids: set[uuid.UUID],
) -> list[dict[str, object]]:
    ids = [item.question_id for item in data.items]
    if len(ids) != len(set(ids)) or set(ids) != expected_ids:
        raise ApiProblem(409, "QUESTION_STRUCTURE_SET_CHANGED", "题目清单已经变化，请刷新后重试")
    if not set(ids).issubset(questions):
        raise ApiProblem(409, "QUESTION_STRUCTURE_SET_CHANGED", "题目清单包含过期题目")
    orders = sorted(item.display_order for item in data.items)
    if orders != list(range(1, len(data.items) + 1)):
        raise ApiProblem(422, "QUESTION_ORDER_INVALID", "题目顺序必须连续且不能重复")
    payload = [
        _item_payload(
            question_id=item.question_id,
            display_number=item.display_number,
            display_order=item.display_order,
            action=item.action,
            max_score=item.max_score,
            source_kind=item.source_kind,
            confidence=item.confidence,
        )
        for item in data.items
    ]
    kept_numbers = [str(item["display_number"]) for item in payload if item["action"] == "keep"]
    if len(kept_numbers) != len(set(kept_numbers)):
        raise ApiProblem(422, "QUESTION_NUMBER_DUPLICATE", "保留的作答单元题号不能重复")
    if not kept_numbers:
        raise ApiProblem(422, "QUESTION_STRUCTURE_EMPTY", "至少保留一个作答单元")
    return payload


@router.put("/assignments/{assignment_id}/question-structure")
def save_question_structure(
    assignment_id: uuid.UUID,
    data: StructureSaveInput,
    db: Db,
    actor: Actor,
) -> dict[str, object]:
    assignment = _owned_assignment(db, assignment_id, actor.id, lock=True)
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_NOT_DRAFT", "只有草稿作业可以调整题目清单")
    review = _latest_review(db, assignment, lock=True)
    questions = _question_map(db, assignment)
    if review is None:
        current_items = _initial_items(db, assignment)
        current_hash = _content_hash("unconfirmed", current_items)
        expected_ids = {uuid.UUID(str(item["question_id"])) for item in current_items}
        version = 1
    else:
        current_rows = _review_items(db, review.id)
        current_hash = review.content_hash
        expected_ids = {row.question_id for row in current_rows}
        version = review.version
    active_ids = {
        question.id for question in questions.values() if question.status == QuestionStatus.active
    }
    if not active_ids.issubset(expected_ids):
        raise ApiProblem(409, "QUESTION_STRUCTURE_SET_CHANGED", "题目清单已经变化，请刷新后重试")
    if data.expected_content_hash != current_hash:
        raise ApiProblem(409, "QUESTION_STRUCTURE_STALE", "题目清单已在其他页面更新")
    payload = _validate_save(data, questions, expected_ids)
    content_hash = _content_hash(data.score_policy, payload)
    if review is None or review.status == "confirmed":
        review = QuestionStructureReview(
            owner_id=actor.id,
            assignment_id=assignment.id,
            paper_version_id=assignment.active_paper_version_id,
            version=version if review is None else version + 1,
            edit_version=1,
            status="draft",
            score_policy=data.score_policy,
            content_hash=content_hash,
        )
        db.add(review)
        db.flush()
    else:
        db.execute(
            delete(QuestionStructureItem).where(QuestionStructureItem.review_id == review.id)
        )
        review.edit_version += 1
        review.score_policy = data.score_policy
        review.content_hash = content_hash
        review.updated_at = now_utc()
        db.flush()
    for item in payload:
        db.add(
            QuestionStructureItem(
                review_id=review.id,
                question_id=uuid.UUID(str(item["question_id"])),
                display_number=str(item["display_number"]),
                parent_number=str(item["parent_number"]) if item["parent_number"] else None,
                sub_number=str(item["sub_number"]) if item["sub_number"] else None,
                display_order=int(str(item["display_order"])),
                action=str(item["action"]),
                max_score=(
                    Decimal(str(item["max_score"])) if item["max_score"] is not None else None
                ),
                source_kind=str(item["source_kind"]),
                confidence=(
                    Decimal(str(item["confidence"])) if item["confidence"] is not None else None
                ),
            )
        )
    audit(
        db,
        actor.id,
        "autosave",
        "question_structure_review",
        review.id,
        {"version": review.version, "edit_version": review.edit_version},
    )
    db.commit()
    return _response(db, review, payload, score_policy=review.score_policy)


def _operation_state(
    db: Session,
    assignment: Assignment,
    expected_content_hash: str,
) -> tuple[QuestionStructureReview | None, list[dict[str, object]]]:
    review = _latest_review(db, assignment, lock=True)
    if review is None:
        payload = _initial_items(db, assignment)
        current_hash = _content_hash("unconfirmed", payload)
    else:
        if review.status != "draft":
            raise ApiProblem(
                409,
                "QUESTION_STRUCTURE_CONFIRMED",
                "已确认的题目清单不能直接合并或拆分",
            )
        payload = _serialized_items(_review_items(db, review.id))
        current_hash = review.content_hash
    if expected_content_hash != current_hash:
        raise ApiProblem(409, "QUESTION_STRUCTURE_STALE", "题目清单已在其他页面更新")
    return review, payload


def _ensure_sources_unbound(db: Session, question_ids: set[uuid.UUID]) -> None:
    for model in (ReferenceAnswerVersion, StructuredRubricVersion, StudentAnswer):
        if db.scalar(select(model.id).where(model.question_id.in_(question_ids)).limit(1)):
            raise ApiProblem(
                409,
                "QUESTION_STRUCTURE_CONTENT_BOUND",
                "题目已有正式答案、评分标准或学生作答，不能再合并或拆分",
            )


def _supersede_source_candidates(db: Session, question_ids: set[uuid.UUID]) -> None:
    answer_rows = db.scalars(
        select(AssignmentAnswerDraftCandidate).where(
            AssignmentAnswerDraftCandidate.question_id.in_(question_ids),
            AssignmentAnswerDraftCandidate.status.not_in(("superseded", "rejected")),
        )
    ).all()
    for row in answer_rows:
        row.status = "superseded"
        row.review_note = "题目结构已由教师合并或拆分，请重新生成"
    rubric_rows = db.scalars(
        select(AssignmentRubricDraftCandidate).where(
            AssignmentRubricDraftCandidate.question_id.in_(question_ids),
            AssignmentRubricDraftCandidate.status.not_in(("superseded", "rejected")),
        )
    ).all()
    for rubric_row in rubric_rows:
        rubric_row.status = "superseded"
        rubric_row.review_note = "题目结构已由教师合并或拆分，请重新生成"


def _persist_operation_payload(
    db: Session,
    assignment: Assignment,
    actor_id: uuid.UUID,
    review: QuestionStructureReview | None,
    payload: list[dict[str, object]],
) -> QuestionStructureReview:
    normalized = [{**item, "display_order": order} for order, item in enumerate(payload, start=1)]
    content_hash = _content_hash("unconfirmed", normalized)
    if review is None:
        review = QuestionStructureReview(
            owner_id=actor_id,
            assignment_id=assignment.id,
            paper_version_id=assignment.active_paper_version_id,
            version=1,
            edit_version=1,
            status="draft",
            score_policy="unconfirmed",
            content_hash=content_hash,
        )
        db.add(review)
        db.flush()
    else:
        db.execute(
            delete(QuestionStructureItem).where(QuestionStructureItem.review_id == review.id)
        )
        review.edit_version += 1
        review.score_policy = "unconfirmed"
        review.content_hash = content_hash
        review.updated_at = now_utc()
        db.flush()
    for item in normalized:
        display_number, parent_number, sub_number = _parts(str(item["display_number"]))
        db.add(
            QuestionStructureItem(
                review_id=review.id,
                question_id=uuid.UUID(str(item["question_id"])),
                display_number=display_number,
                parent_number=parent_number,
                sub_number=sub_number,
                display_order=int(str(item["display_order"])),
                action=str(item["action"]),
                max_score=(
                    Decimal(str(item["max_score"])) if item.get("max_score") is not None else None
                ),
                source_kind=str(item["source_kind"]),
                confidence=(
                    Decimal(str(item["confidence"])) if item.get("confidence") is not None else None
                ),
            )
        )
    db.flush()
    return review


def _new_question(
    db: Session,
    assignment: Assignment,
    *,
    display_number: str,
    display_order: int,
    question_type: str,
) -> Question:
    normalized, _parent, _sub = _parts(display_number)
    question = Question(
        paper_version_id=assignment.active_paper_version_id,
        question_number=normalized,
        display_order=display_order,
        question_type=question_type,
        content_text=None,
        content_latex=None,
        max_score=None,
        status=QuestionStatus.active,
        source="manual",
    )
    db.add(question)
    db.flush()
    return question


@router.post("/assignments/{assignment_id}/question-structure/split")
def split_question_structure(
    assignment_id: uuid.UUID,
    data: StructureSplitInput,
    db: Db,
    actor: Actor,
) -> dict[str, object]:
    assignment = _owned_assignment(db, assignment_id, actor.id, lock=True)
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_NOT_DRAFT", "只有草稿作业可以拆分题目")
    review, payload = _operation_state(db, assignment, data.expected_content_hash)
    source_item = next(
        (
            item
            for item in payload
            if item["question_id"] == str(data.source_question_id) and item["action"] == "keep"
        ),
        None,
    )
    if source_item is None:
        raise ApiProblem(409, "QUESTION_STRUCTURE_SET_CHANGED", "待拆分题目已不在当前清单")
    _ensure_sources_unbound(db, {data.source_question_id})
    source = db.scalar(
        select(Question)
        .where(
            Question.id == data.source_question_id,
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
        .with_for_update()
    )
    if source is None:
        raise ApiProblem(409, "QUESTION_STRUCTURE_SET_CHANGED", "待拆分题目已经变化")
    regions = list(
        db.scalars(
            select(QuestionRegion)
            .where(QuestionRegion.question_id == source.id)
            .order_by(QuestionRegion.id)
            .with_for_update()
        ).all()
    )
    available_region_ids = {region.id for region in regions}
    provided_region_ids = [region_id for part in data.parts for region_id in part.region_ids]
    if (
        not available_region_ids
        or len(provided_region_ids) != len(set(provided_region_ids))
        or set(provided_region_ids) != available_region_ids
    ):
        raise ApiProblem(
            409,
            "QUESTION_REGION_PARTITION_INVALID",
            "拆分必须且只能覆盖该题当前的全部区域",
        )
    part_numbers = [_parts(part.display_number)[0] for part in data.parts]
    other_numbers = {
        str(item["display_number"])
        for item in payload
        if item["action"] == "keep" and item["question_id"] != str(source.id)
    }
    if len(part_numbers) != len(set(part_numbers)) or other_numbers.intersection(part_numbers):
        raise ApiProblem(422, "QUESTION_NUMBER_DUPLICATE", "拆分后的题号不能重复")
    max_order = max(
        (question.display_order for question in _question_map(db, assignment).values()),
        default=0,
    )
    replacements: list[dict[str, object]] = []
    region_by_id = {region.id: region for region in regions}
    for offset, part in enumerate(data.parts, start=1):
        question = _new_question(
            db,
            assignment,
            display_number=part.display_number,
            display_order=max_order + offset,
            question_type=source.question_type,
        )
        for region_id in part.region_ids:
            region_by_id[region_id].question_id = question.id
        replacements.append(
            _item_payload(
                question_id=question.id,
                display_number=part.display_number,
                display_order=0,
                action="keep",
                max_score=None,
                source_kind="manual",
                confidence=None,
            )
        )
    source.status = QuestionStatus.removed
    _supersede_source_candidates(db, {source.id})
    result_payload: list[dict[str, object]] = []
    for item in payload:
        if item["question_id"] == str(source.id):
            result_payload.extend(replacements)
        else:
            result_payload.append(item)
    review = _persist_operation_payload(db, assignment, actor.id, review, result_payload)
    audit(
        db,
        actor.id,
        "split",
        "question_structure_review",
        review.id,
        {
            "source_question_id": str(source.id),
            "new_question_ids": [item["question_id"] for item in replacements],
            "region_count": len(regions),
        },
    )
    db.commit()
    return _response(
        db,
        review,
        _serialized_items(_review_items(db, review.id)),
        score_policy=review.score_policy,
    )


@router.post("/assignments/{assignment_id}/question-structure/merge")
def merge_question_structure(
    assignment_id: uuid.UUID,
    data: StructureMergeInput,
    db: Db,
    actor: Actor,
) -> dict[str, object]:
    assignment = _owned_assignment(db, assignment_id, actor.id, lock=True)
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_NOT_DRAFT", "只有草稿作业可以合并题目")
    review, payload = _operation_state(db, assignment, data.expected_content_hash)
    source_ids = set(data.question_ids)
    if len(source_ids) != len(data.question_ids):
        raise ApiProblem(422, "QUESTION_MERGE_SET_INVALID", "合并题目不能重复")
    source_items = [
        item
        for item in payload
        if uuid.UUID(str(item["question_id"])) in source_ids and item["action"] == "keep"
    ]
    if len(source_items) != len(source_ids):
        raise ApiProblem(409, "QUESTION_STRUCTURE_SET_CHANGED", "待合并题目已经变化")
    merged_number = _parts(data.display_number)[0]
    other_numbers = {
        str(item["display_number"])
        for item in payload
        if item["action"] == "keep" and uuid.UUID(str(item["question_id"])) not in source_ids
    }
    if merged_number in other_numbers:
        raise ApiProblem(422, "QUESTION_NUMBER_DUPLICATE", "合并后的题号已存在")
    _ensure_sources_unbound(db, source_ids)
    sources = list(
        db.scalars(
            select(Question)
            .where(
                Question.id.in_(source_ids),
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == QuestionStatus.active,
            )
            .order_by(Question.display_order, Question.id)
            .with_for_update()
        ).all()
    )
    if len(sources) != len(source_ids):
        raise ApiProblem(409, "QUESTION_STRUCTURE_SET_CHANGED", "待合并题目已经变化")
    question_type = sources[0].question_type
    if any(source.question_type != question_type for source in sources[1:]):
        question_type = "other"
    max_order = max(
        (question.display_order for question in _question_map(db, assignment).values()),
        default=0,
    )
    merged = _new_question(
        db,
        assignment,
        display_number=merged_number,
        display_order=max_order + 1,
        question_type=question_type,
    )
    regions = list(
        db.scalars(
            select(QuestionRegion)
            .where(QuestionRegion.question_id.in_(source_ids))
            .order_by(QuestionRegion.id)
            .with_for_update()
        ).all()
    )
    for region in regions:
        region.question_id = merged.id
    for source in sources:
        source.status = QuestionStatus.removed
    _supersede_source_candidates(db, source_ids)
    merged_payload = _item_payload(
        question_id=merged.id,
        display_number=merged_number,
        display_order=0,
        action="keep",
        max_score=None,
        source_kind="manual",
        confidence=None,
    )
    first_source_position = min(
        index
        for index, item in enumerate(payload)
        if uuid.UUID(str(item["question_id"])) in source_ids
    )
    result_payload = [
        item for item in payload if uuid.UUID(str(item["question_id"])) not in source_ids
    ]
    result_payload.insert(first_source_position, merged_payload)
    review = _persist_operation_payload(db, assignment, actor.id, review, result_payload)
    audit(
        db,
        actor.id,
        "merge",
        "question_structure_review",
        review.id,
        {
            "source_question_ids": [str(question_id) for question_id in data.question_ids],
            "new_question_id": str(merged.id),
            "region_count": len(regions),
        },
    )
    db.commit()
    return _response(
        db,
        review,
        _serialized_items(_review_items(db, review.id)),
        score_policy=review.score_policy,
    )


@router.post("/assignments/{assignment_id}/question-structure/confirm")
def confirm_question_structure(
    assignment_id: uuid.UUID,
    data: StructureConfirmInput,
    db: Db,
    actor: Actor,
) -> dict[str, object]:
    assignment = _owned_assignment(db, assignment_id, actor.id, lock=True)
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_NOT_DRAFT", "只有草稿作业可以确认题目清单")
    review = _latest_review(db, assignment, lock=True)
    if review is None or review.status != "draft":
        raise ApiProblem(409, "QUESTION_STRUCTURE_DRAFT_REQUIRED", "请先保存题目清单")
    if data.expected_content_hash != review.content_hash:
        raise ApiProblem(409, "QUESTION_STRUCTURE_STALE", "题目清单已在其他页面更新")
    rows = _review_items(db, review.id)
    kept = [row for row in rows if row.action == "keep"]
    if not kept:
        raise ApiProblem(422, "QUESTION_STRUCTURE_EMPTY", "至少保留一个作答单元")
    if review.score_policy == "unconfirmed":
        raise ApiProblem(422, "QUESTION_SCORE_POLICY_REQUIRED", "请明确选择分值处理方式")
    scores: dict[uuid.UUID, Decimal] = {}
    if review.score_policy == "equal_weight":
        if assignment.total_score is None or Decimal(assignment.total_score) <= 0:
            raise ApiProblem(422, "ASSIGNMENT_TOTAL_SCORE_REQUIRED", "等权分配前请先填写作业总分")
        total = Decimal(assignment.total_score)
        each = (total / len(kept)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for row in kept[:-1]:
            scores[row.question_id] = each
        scores[kept[-1].question_id] = total - each * (len(kept) - 1)
    else:
        missing = [row.display_number for row in kept if row.max_score is None]
        if missing:
            raise ApiProblem(
                422,
                "QUESTION_SCORE_REQUIRED",
                f"以下题目尚未确认分值：{', '.join(missing)}",
            )
        scores = {row.question_id: Decimal(str(row.max_score)) for row in kept}
    questions = _question_map(db, assignment)
    kept_by_number = {row.display_number: row for row in kept}
    active_order = {
        row.question_id: order
        for order, row in enumerate(
            sorted(kept, key=lambda item: (item.display_order, item.id)),
            start=1,
        )
    }
    for row in rows:
        question = questions.get(row.question_id)
        if question is None:
            raise ApiProblem(409, "QUESTION_STRUCTURE_SET_CHANGED", "题目清单包含过期题目")
        if row.action == "remove":
            question.status = QuestionStatus.removed
            continue
        question.status = QuestionStatus.active
        question.question_number = row.display_number
        question.display_order = active_order[row.question_id]
        question.max_score = scores[row.question_id]
        parent = kept_by_number.get(row.parent_number) if row.parent_number else None
        question.parent_question_id = parent.question_id if parent is not None else None
    review.status = "confirmed"
    review.confirmed_by = actor.id
    review.confirmed_at = now_utc()
    audit(
        db,
        actor.id,
        "confirm",
        "question_structure_review",
        review.id,
        {
            "version": review.version,
            "answer_unit_count": len(kept),
            "score_policy": review.score_policy,
        },
    )
    db.commit()
    return _response(db, review, _serialized_items(rows), score_policy=review.score_policy)
