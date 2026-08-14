import uuid
from decimal import Decimal

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    AssignmentStatus,
    FileStatus,
    PaperPage,
    PaperVersion,
    Question,
    QuestionRegion,
    ReferenceAnswerVersion,
    StoredFile,
    User,
    VersionStatus,
    now_utc,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

client = TestClient(app)


def seeded_assignment(numbers: list[str], *, total_score: str | None = None) -> uuid.UUID:
    client.get("/api/assignments")
    db = SessionLocal()
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor is not None
    assignment = Assignment(
        owner_id=actor.id,
        title=f"脱敏层级题号 {uuid.uuid4()}",
        subject="数学分析",
        grade="大学",
        status=AssignmentStatus.draft,
        total_score=Decimal(total_score) if total_score is not None else None,
    )
    db.add(assignment)
    db.flush()
    paper = PaperVersion(
        assignment_id=assignment.id,
        version=1,
        status=VersionStatus.draft,
        source_type="synthetic",
        created_by=actor.id,
    )
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    for order, number in enumerate(numbers, start=1):
        db.add(
            Question(
                paper_version_id=paper.id,
                question_number=number,
                display_order=order,
                question_type="calculation",
                content_text=f"脱敏合成题 {number}",
                max_score=None,
                source="synthetic",
            )
        )
    db.commit()
    result = assignment.id
    db.close()
    return result


def save(
    assignment_id: uuid.UUID,
    current: dict[str, object],
    *,
    policy: str,
    scores: dict[str, str] | None = None,
) -> dict[str, object]:
    items = current["items"]
    assert isinstance(items, list)
    payload_items = []
    for item in items:
        assert isinstance(item, dict)
        payload = dict(item)
        payload["max_score"] = (scores or {}).get(str(item["display_number"]))
        payload_items.append(payload)
    response = client.put(
        f"/api/assignments/{assignment_id}/question-structure",
        json={
            "expected_content_hash": current["content_hash"],
            "score_policy": policy,
            "items": payload_items,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_hierarchical_16_unit_review_autosave_and_missing_score_gate() -> None:
    numbers = [
        "1(1)",
        "1(2)",
        "2(1)",
        "2(2)",
        "2(3)",
        "2(4)",
        "2(5)",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "11(2)",
        "12(1)",
        "12(2)",
    ]
    assignment_id = seeded_assignment(numbers)
    initial = client.get(f"/api/assignments/{assignment_id}/question-structure")
    assert initial.status_code == 200
    assert initial.json()["answer_unit_count"] == 16
    draft = save(assignment_id, initial.json(), policy="unconfirmed")
    blocked = client.post(
        f"/api/assignments/{assignment_id}/question-structure/confirm",
        json={
            "expected_content_hash": draft["content_hash"],
            "explicit_confirmation": True,
        },
    )
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "QUESTION_SCORE_POLICY_REQUIRED"

    partial_scores = {number: "5" for number in numbers if number != "12(2)"}
    draft = save(assignment_id, draft, policy="manual", scores=partial_scores)
    missing = client.post(
        f"/api/assignments/{assignment_id}/question-structure/confirm",
        json={
            "expected_content_hash": draft["content_hash"],
            "explicit_confirmation": True,
        },
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "QUESTION_SCORE_REQUIRED"

    draft = save(
        assignment_id,
        draft,
        policy="manual",
        scores={number: "5" for number in numbers},
    )
    confirmed = client.post(
        f"/api/assignments/{assignment_id}/question-structure/confirm",
        json={
            "expected_content_hash": draft["content_hash"],
            "explicit_confirmation": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "confirmed"
    assert body["answer_unit_count"] == 16
    by_number = {item["display_number"]: item for item in body["items"]}
    assert by_number["2(3)"]["parent_number"] == "2"
    assert by_number["2(5)"]["sub_number"] == "5"
    assert by_number["12(1)"]["parent_number"] == "12"
    assert by_number["12(2)"]["sub_number"] == "2"

    db = SessionLocal()
    assignment = db.get(Assignment, assignment_id)
    assert assignment is not None
    assert assignment.total_score is None
    rows = db.scalars(
        select(Question)
        .where(Question.paper_version_id == assignment.active_paper_version_id)
        .order_by(Question.display_order)
    ).all()
    assert [row.question_number for row in rows] == numbers
    assert all(Decimal(row.max_score) == Decimal("5") for row in rows)
    db.close()


def test_equal_weight_requires_teacher_total_and_absorbs_rounding_tail() -> None:
    assignment_id = seeded_assignment(["1", "2(3)", "12(2)"], total_score="10")
    initial = client.get(f"/api/assignments/{assignment_id}/question-structure").json()
    draft = save(assignment_id, initial, policy="equal_weight")
    confirmed = client.post(
        f"/api/assignments/{assignment_id}/question-structure/confirm",
        json={
            "expected_content_hash": draft["content_hash"],
            "explicit_confirmation": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    db = SessionLocal()
    assignment = db.get(Assignment, assignment_id)
    assert assignment is not None
    scores = [
        Decimal(value)
        for value in db.scalars(
            select(Question.max_score)
            .where(Question.paper_version_id == assignment.active_paper_version_id)
            .order_by(Question.display_order)
        ).all()
    ]
    assert scores == [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]
    db.close()


def test_invalid_hierarchy_and_stale_autosave_fail_closed() -> None:
    assignment_id = seeded_assignment(["1", "2"])
    initial = client.get(f"/api/assignments/{assignment_id}/question-structure").json()
    bad_items = [dict(item) for item in initial["items"]]
    bad_items[1]["display_number"] = "第二题"
    invalid = client.put(
        f"/api/assignments/{assignment_id}/question-structure",
        json={
            "expected_content_hash": initial["content_hash"],
            "score_policy": "unconfirmed",
            "items": bad_items,
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "QUESTION_NUMBER_INVALID"
    saved = save(assignment_id, initial, policy="manual")
    stale = client.put(
        f"/api/assignments/{assignment_id}/question-structure",
        json={
            "expected_content_hash": initial["content_hash"],
            "score_policy": "unconfirmed",
            "items": saved["items"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "QUESTION_STRUCTURE_STALE"


def seeded_cross_page_regions() -> tuple[uuid.UUID, list[uuid.UUID], list[uuid.UUID]]:
    client.get("/api/assignments")
    db = SessionLocal()
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor is not None
    assignment = Assignment(
        owner_id=actor.id,
        title=f"脱敏跨页区域 {uuid.uuid4()}",
        subject="数学分析",
        status=AssignmentStatus.draft,
    )
    db.add(assignment)
    db.flush()
    paper = PaperVersion(
        assignment_id=assignment.id,
        version=1,
        status=VersionStatus.draft,
        source_type="synthetic",
        created_by=actor.id,
    )
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    stored = StoredFile(
        owner_id=actor.id,
        storage_key=f"synthetic/{uuid.uuid4()}.pdf",
        original_name="脱敏合成.pdf",
        content_type="application/pdf",
        size=100,
        checksum=uuid.uuid4().hex * 2,
        status=FileStatus.ready,
    )
    db.add(stored)
    db.flush()
    pages = [
        PaperPage(
            paper_version_id=paper.id,
            stored_file_id=stored.id,
            page_number=page_number,
            source_page_number=page_number,
            status="ready",
        )
        for page_number in (1, 2)
    ]
    db.add_all(pages)
    db.flush()
    questions = [
        Question(
            paper_version_id=paper.id,
            question_number=number,
            display_order=order,
            question_type="calculation",
            content_text=f"脱敏题 {number}",
            max_score=None,
            source="synthetic",
        )
        for order, number in enumerate(("2", "12(1)", "12(2)"), start=1)
    ]
    db.add_all(questions)
    db.flush()
    regions = [
        QuestionRegion(
            question_id=questions[0].id,
            paper_page_id=pages[0].id,
            x=Decimal("0.1"),
            y=Decimal("0.2"),
            width=Decimal("0.8"),
            height=Decimal("0.2"),
            source="synthetic",
        ),
        QuestionRegion(
            question_id=questions[0].id,
            paper_page_id=pages[1].id,
            x=Decimal("0.1"),
            y=Decimal("0.1"),
            width=Decimal("0.8"),
            height=Decimal("0.2"),
            source="synthetic",
        ),
        QuestionRegion(
            question_id=questions[1].id,
            paper_page_id=pages[0].id,
            x=Decimal("0.1"),
            y=Decimal("0.5"),
            width=Decimal("0.8"),
            height=Decimal("0.2"),
            source="synthetic",
        ),
        QuestionRegion(
            question_id=questions[2].id,
            paper_page_id=pages[1].id,
            x=Decimal("0.1"),
            y=Decimal("0.5"),
            width=Decimal("0.8"),
            height=Decimal("0.2"),
            source="synthetic",
        ),
    ]
    db.add_all(regions)
    db.commit()
    result = (
        assignment.id,
        [question.id for question in questions],
        [region.id for region in regions],
    )
    db.close()
    return result


def test_split_by_regions_and_merge_preserve_cross_page_evidence() -> None:
    assignment_id, question_ids, region_ids = seeded_cross_page_regions()
    initial = client.get(f"/api/assignments/{assignment_id}/question-structure")
    assert initial.status_code == 200
    initial_item = next(
        item for item in initial.json()["items"] if item["question_id"] == str(question_ids[0])
    )
    assert initial_item["region_count"] == 2
    assert initial_item["spans_pages"] is True

    split = client.post(
        f"/api/assignments/{assignment_id}/question-structure/split",
        json={
            "expected_content_hash": initial.json()["content_hash"],
            "source_question_id": str(question_ids[0]),
            "parts": [
                {"display_number": "2(3)", "region_ids": [str(region_ids[0])]},
                {"display_number": "2(5)", "region_ids": [str(region_ids[1])]},
            ],
            "explicit_confirmation": True,
        },
    )
    assert split.status_code == 200, split.text
    split_body = split.json()
    split_by_number = {item["display_number"]: item for item in split_body["items"]}
    assert split_by_number["2(3)"]["region_count"] == 1
    assert split_by_number["2(3)"]["regions"][0]["page_number"] == 1
    assert split_by_number["2(5)"]["regions"][0]["page_number"] == 2
    assert split_body["score_policy"] == "unconfirmed"

    merge = client.post(
        f"/api/assignments/{assignment_id}/question-structure/merge",
        json={
            "expected_content_hash": split_body["content_hash"],
            "question_ids": [str(question_ids[1]), str(question_ids[2])],
            "display_number": "12",
            "explicit_confirmation": True,
        },
    )
    assert merge.status_code == 200, merge.text
    merged_item = next(item for item in merge.json()["items"] if item["display_number"] == "12")
    assert merged_item["region_count"] == 2
    assert merged_item["page_count"] == 2
    assert merged_item["spans_pages"] is True

    db = SessionLocal()
    source_rows = [db.get(Question, question_id) for question_id in question_ids]
    assert all(row is not None and row.status.value == "removed" for row in source_rows)
    merged = db.get(Question, uuid.UUID(merged_item["question_id"]))
    assert merged is not None
    bound_pages = db.scalars(
        select(PaperPage.page_number)
        .join(QuestionRegion, QuestionRegion.paper_page_id == PaperPage.id)
        .where(QuestionRegion.question_id == merged.id)
        .order_by(PaperPage.page_number)
    ).all()
    assert bound_pages == [1, 2]
    db.close()


def test_split_fails_closed_after_reference_answer_is_bound() -> None:
    assignment_id, question_ids, region_ids = seeded_cross_page_regions()
    initial = client.get(f"/api/assignments/{assignment_id}/question-structure").json()
    db = SessionLocal()
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor is not None
    db.add(
        ReferenceAnswerVersion(
            question_id=question_ids[0],
            source_type="teacher_authored",
            raw_content="脱敏答案",
            normalized_content="脱敏答案",
            structured_content={},
            content_hash="f" * 64,
            version=1,
            provenance={},
            created_by=actor.id,
            status="confirmed",
            teacher_confirmed_at=now_utc(),
        )
    )
    db.commit()
    db.close()
    response = client.post(
        f"/api/assignments/{assignment_id}/question-structure/split",
        json={
            "expected_content_hash": initial["content_hash"],
            "source_question_id": str(question_ids[0]),
            "parts": [
                {"display_number": "2(3)", "region_ids": [str(region_ids[0])]},
                {"display_number": "2(5)", "region_ids": [str(region_ids[1])]},
            ],
            "explicit_confirmation": True,
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "QUESTION_STRUCTURE_CONTENT_BOUND"
