from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from app.db.base import Base
from app.models import (
    Assignment,
    GradeRelease,
    GradingBatch,
    PaperVersion,
    ProcessingRun,
    ProcessingRunCommand,
    ProcessingStep,
    Question,
    SchoolClass,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
)
from app.processing import orchestrator
from app.processing.contracts import ProcessingInputError, ProcessingInputSnapshot
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


@pytest.fixture
def isolated_session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def _id(value: int) -> uuid.UUID:
    return uuid.UUID(f"aaaaaaaa-aaaa-4aaa-8aaa-{value:012x}")


def _seed_mixed_batch(
    db: Session,
) -> tuple[uuid.UUID, GradingBatch, dict[uuid.UUID, str]]:
    owner = User(
        id=_id(1),
        email=f"manifest-{uuid.uuid4()}@example.test",
        password_hash="test",
        display_name="Manifest",
    )
    school_class = SchoolClass(id=_id(2), owner_id=owner.id, name="Manifest")
    assignment = Assignment(id=_id(3), owner_id=owner.id, title="Manifest")
    db.add_all([owner, school_class, assignment])
    db.flush()
    paper = PaperVersion(
        id=_id(4),
        assignment_id=assignment.id,
        version=1,
        created_by=owner.id,
    )
    batch = GradingBatch(
        id=_id(5),
        owner_id=owner.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        name="Manifest",
    )
    db.add_all([paper, batch])
    db.flush()
    questions = [
        Question(
            id=_id(20 + offset),
            paper_version_id=paper.id,
            question_number=str(offset + 1),
            display_order=offset + 1,
            question_type="short_answer",
            content_text="Q",
        )
        for offset in range(2)
    ]
    db.add_all(questions)
    db.flush()

    no_answer = Submission(
        id=_id(10),
        owner_id=owner.id,
        grading_batch_id=batch.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        status="uploaded",
    )
    formal_blocked = Submission(
        id=_id(11),
        owner_id=owner.id,
        grading_batch_id=batch.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        status="recognized",
    )
    ready = Submission(
        id=_id(12),
        owner_id=owner.id,
        grading_batch_id=batch.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        status="recognized",
    )
    terminal = Submission(
        id=_id(13),
        owner_id=owner.id,
        grading_batch_id=batch.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        status="finalized",
    )
    db.add_all([no_answer, formal_blocked, ready, terminal])
    db.flush()
    blocked_answer = StudentAnswer(
        id=_id(30),
        submission_id=formal_blocked.id,
        question_id=questions[0].id,
        question_version_reference=str(paper.id),
    )
    ready_answer = StudentAnswer(
        id=_id(31),
        submission_id=ready.id,
        question_id=questions[1].id,
        question_version_reference=str(paper.id),
    )
    terminal_answer = StudentAnswer(
        id=_id(32),
        submission_id=terminal.id,
        question_id=questions[0].id,
        question_version_reference=str(paper.id),
    )
    db.add_all([blocked_answer, ready_answer, terminal_answer])
    db.commit()
    return (
        owner.id,
        batch,
        {
            blocked_answer.id: "blocked",
            ready_answer.id: "ready",
            terminal_answer.id: "terminal",
        },
    )


def test_mixed_manifest_is_stable_complete_and_never_writes_formal_grades(
    isolated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = isolated_session
    owner_id, batch, answer_modes = _seed_mixed_batch(db)
    blocker = {"code": "STRUCTURED_SET_REQUIRED"}

    def snapshot(
        _db: Session,
        *,
        owner_id: uuid.UUID,
        grading_batch_id: uuid.UUID,
        submission_id: uuid.UUID,
        answer_id: uuid.UUID,
    ) -> ProcessingInputSnapshot:
        assert owner_id
        assert grading_batch_id == batch.id
        assert submission_id
        mode = answer_modes[answer_id]
        if mode == "blocked":
            raise ProcessingInputError(blocker["code"], "synthetic blocker")
        return ProcessingInputSnapshot(
            payload={"submission": {"id": submission_id}, "answer": {"id": answer_id}},
            input_version=f"{answer_id.int:064x}",
        )

    monkeypatch.setattr(orchestrator, "build_processing_input_snapshot", snapshot)
    first = orchestrator._manifest(db, owner_id, batch)
    second = orchestrator._manifest(db, owner_id, batch)
    assert first == second
    assert [item["submission_id"] for item in first["included"]] == [
        str(_id(10)),
        str(_id(11)),
        str(_id(12)),
    ]
    assert first["included"][0]["blockers"] == ["STUDENT_ANSWERS_REQUIRED"]
    assert first["included"][1]["blockers"] == ["STRUCTURED_SET_REQUIRED"]
    assert first["included"][2]["blockers"] == []
    assert first["excluded"] == [
        {
            "submission_id": str(_id(13)),
            "reason": "SUBMISSION_TERMINAL",
            "status": "finalized",
        }
    ]

    blocker["code"] = "STRUCTURED_SET_STALE"
    changed = orchestrator._manifest(db, owner_id, batch)
    assert changed["input_version"] != first["input_version"]
    blocker["code"] = "STRUCTURED_SET_REQUIRED"

    monkeypatch.setattr(orchestrator, "materialize_work_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        orchestrator,
        "_dispatch_submission_processing_jobs",
        lambda *_args, **_kwargs: None,
    )
    run = orchestrator.continue_processing(
        db,
        owner_id=owner_id,
        batch_id=batch.id,
        idempotency_key=f"manifest-{uuid.uuid4()}",
    )
    steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(ProcessingStep.processing_run_id == run.id)
            .order_by(ProcessingStep.submission_id)
        )
    )
    assert [(step.submission_id, step.kind, step.status) for step in steps] == [
        (_id(10), "recognition", "dispatched"),
        (_id(11), "review_readiness", "blocked_review"),
        (_id(12), "codex_suggestion", "pending"),
    ]
    assert run.submission_count == run.step_count == 3
    assert (
        db.scalar(select(func.count()).select_from(TeacherReview)),
        db.scalar(select(func.count()).select_from(SubmissionScoreSnapshot)),
        db.scalar(select(func.count()).select_from(GradeRelease)),
    ) == (0, 0, 0)


@pytest.mark.parametrize("code", ["PROCESSING_INPUT_STALE", "SUBMISSION_SCOPE_MISMATCH"])
def test_manifest_fails_closed_on_non_readiness_input_errors_without_rows(
    isolated_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    db = isolated_session
    owner_id, batch, _answer_modes = _seed_mixed_batch(db)

    def hard_failure(*_args: object, **_kwargs: object) -> ProcessingInputSnapshot:
        raise ProcessingInputError(code, "synthetic invariant failure")

    monkeypatch.setattr(orchestrator, "build_processing_input_snapshot", hard_failure)
    with pytest.raises(orchestrator.OrchestratorProblem) as raised:
        orchestrator.continue_processing(
            db,
            owner_id=owner_id,
            batch_id=batch.id,
            idempotency_key=f"hard-failure-{code}",
        )
    db.rollback()
    assert raised.value.status == 409
    assert raised.value.code == code
    assert (
        db.scalar(select(func.count()).select_from(ProcessingRun)),
        db.scalar(select(func.count()).select_from(ProcessingRunCommand)),
        db.scalar(select(func.count()).select_from(ProcessingStep)),
    ) == (0, 0, 0)
