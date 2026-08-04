from __future__ import annotations

import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from threading import Event

import pytest
from app.api import grading as grading_api
from app.api import results as results_api
from app.core.config import get_settings
from app.db.session import engine
from app.main import app
from app.models import (
    Assignment,
    ClassStudent,
    GradeRelease,
    GradeReleaseItem,
    GradingResult,
    KnowledgePoint,
    MembershipStatus,
    Question,
    QuestionKnowledgePoint,
    QuestionRubric,
    RubricItem,
    RubricVersion,
    Student,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
    VersionStatus,
)
from app.storage.dependencies import get_storage
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_submission_workflow import client, confirm_answer_regions, png, workflow


@dataclass(frozen=True)
class ConfirmableCase:
    db: Session
    batch_id: str
    submission_id: uuid.UUID
    answer_id: uuid.UUID
    result_id: uuid.UUID


def _formal_counts(db: Session) -> tuple[int, int, int]:
    db.expire_all()
    return (
        db.scalar(select(func.count()).select_from(TeacherReview)) or 0,
        db.scalar(select(func.count()).select_from(SubmissionScoreSnapshot)) or 0,
        db.scalar(select(func.count()).select_from(GradeRelease)) or 0,
    )


@contextmanager
def _confirmable_case() -> Generator[ConfirmableCase, None, None]:
    db, _storage, batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": f"confirm-results-processing-{uuid.uuid4()}"},
        )
        assert processing.status_code == 201, processing.text
        confirm_answer_regions(db, submission_id)
        recognition = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": f"confirm-results-recognition-{uuid.uuid4()}"},
        )
        assert recognition.status_code == 201, recognition.text

        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        answer.requires_review = False
        answer.status = "ready_for_grading"
        db.commit()

        grade = client.post(f"/api/student-answers/{answer.id}/grade")
        assert grade.status_code == 200, grade.text
        result = db.scalar(
            select(GradingResult)
            .where(GradingResult.student_answer_id == answer.id)
            .order_by(GradingResult.created_at.desc(), GradingResult.id.desc())
        )
        assert result is not None
        assert result.status == "suggested"
        assert _formal_counts(db) == (0, 0, 0)
        yield ConfirmableCase(db, batch_id, submission_id, answer.id, result.id)
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def _readiness(case: ConfirmableCase) -> dict[str, object]:
    response = client.get(f"/api/grading-batches/{case.batch_id}/confirm-results/readiness")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload["review_hash"], str)
    assert len(payload["review_hash"]) == 64
    return payload


def _add_confirmable_submission(case: ConfirmableCase) -> uuid.UUID:
    original = case.db.get(Submission, case.submission_id)
    assert original is not None
    student = Student(
        owner_id=original.owner_id,
        student_number=f"second-{uuid.uuid4()}",
        name="Synthetic second student",
    )
    case.db.add(student)
    case.db.flush()
    case.db.add(
        ClassStudent(
            class_id=original.class_id,
            student_id=student.id,
            status=MembershipStatus.active,
            joined_at=original.created_at,
        )
    )
    case.db.commit()
    upload = client.post(
        f"/api/grading-batches/{case.batch_id}/files",
        files=[
            (
                "files",
                (f"{student.student_number}.png", png("azure"), "image/png"),
            )
        ],
    )
    assert upload.status_code == 201, upload.text
    submission_id = uuid.UUID(upload.json()["items"][0]["submission_id"])
    submission = case.db.get(Submission, submission_id)
    assert submission is not None
    submission.student_id = student.id
    submission.status = "matched"
    case.db.commit()
    processing = client.post(
        f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
        json={"idempotency_key": f"second-processing-{uuid.uuid4()}"},
    )
    assert processing.status_code == 201, processing.text
    confirm_answer_regions(case.db, submission_id)
    recognition = client.post(
        f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
        json={"idempotency_key": f"second-recognition-{uuid.uuid4()}"},
    )
    assert recognition.status_code == 201, recognition.text
    answer = case.db.scalar(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
    )
    assert answer is not None
    answer.requires_review = False
    answer.status = "ready_for_grading"
    case.db.commit()
    grade = client.post(f"/api/student-answers/{answer.id}/grade")
    assert grade.status_code == 200, grade.text
    return submission_id


def _confirm(case: ConfirmableCase, *, key: str, review_hash: str):
    return client.post(
        f"/api/grading-batches/{case.batch_id}/confirm-results",
        json={
            "idempotency_key": key,
            "expected_review_hash": review_hash,
        },
    )


def test_readiness_is_read_only_and_one_blocker_prevents_every_formal_write() -> None:
    with _confirmable_case() as case:
        submission = case.db.get(Submission, case.submission_id)
        assert submission is not None
        blocked_student = Student(
            owner_id=submission.owner_id,
            student_number=f"blocked-{uuid.uuid4()}",
            name="Synthetic blocked student",
        )
        case.db.add(blocked_student)
        case.db.flush()
        case.db.add(
            Submission(
                owner_id=submission.owner_id,
                grading_batch_id=submission.grading_batch_id,
                assignment_id=submission.assignment_id,
                class_id=submission.class_id,
                student_id=blocked_student.id,
                status="uploaded",
            )
        )
        case.db.commit()

        readiness = _readiness(case)
        assert readiness["ready"] is False
        assert readiness["blockers"]
        assert _formal_counts(case.db) == (0, 0, 0)

        response = _confirm(
            case,
            key=f"blocked-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CONFIRM_RESULTS_BLOCKED"
        assert response.json()["details"]["blockers"]
        assert _formal_counts(case.db) == (0, 0, 0)


def test_confirm_results_materializes_review_complete_snapshot_and_one_release() -> None:
    with _confirmable_case() as case:
        readiness = _readiness(case)
        assert readiness["ready"] is True
        assert readiness["blockers"] == []
        assert readiness["previous_grade_release_id"] is None
        assert _formal_counts(case.db) == (0, 0, 0)

        response = _confirm(
            case,
            key=f"success-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["status"] == "released"
        assert payload["review_hash"] == readiness["review_hash"]
        assert payload["submission_count"] == 1
        assert payload["auto_accepted_count"] == 1
        assert len(payload["teacher_review_ids"]) == 1
        assert len(payload["snapshot_ids"]) == 1
        assert payload["previous_grade_release_id"] is None
        assert payload["new_snapshot_ids"] == payload["snapshot_ids"]
        assert payload["reused_snapshot_ids"] == []
        assert _formal_counts(case.db) == (1, 1, 1)

        review = case.db.get(TeacherReview, uuid.UUID(payload["teacher_review_ids"][0]))
        snapshot = case.db.get(SubmissionScoreSnapshot, uuid.UUID(payload["snapshot_ids"][0]))
        release = case.db.get(GradeRelease, uuid.UUID(payload["grade_release_id"]))
        submission = case.db.get(Submission, case.submission_id)
        result = case.db.get(GradingResult, case.result_id)
        assert review is not None
        assert review.student_answer_id == case.answer_id
        assert review.grading_result_id == case.result_id
        assert review.decision == "accepted"
        assert review.confirmed_at is not None
        assert snapshot is not None and snapshot.status == "complete"
        assert release is not None and release.status == "released"
        assert release.released_at is not None
        assert submission is not None and submission.status == "finalized"
        assert result is not None and result.status == "accepted"
        assert (
            case.db.scalar(
                select(func.count())
                .select_from(GradeReleaseItem)
                .where(
                    GradeReleaseItem.grade_release_id == release.id,
                    GradeReleaseItem.submission_id == case.submission_id,
                    GradeReleaseItem.score_snapshot_id == snapshot.id,
                )
            )
            == 1
        )
        refreshed = _readiness(case)
        assert refreshed["ready"] is False
        existing = refreshed["confirmed_result"]
        assert existing["grade_release_id"] == payload["grade_release_id"]
        assert existing["status"] == "released"
        assert existing["review_hash"] == payload["review_hash"]
        assert existing["snapshot_ids"] == payload["snapshot_ids"]

        finalized_readiness = _readiness(case)
        assert finalized_readiness["ready"] is False
        assert "CONFIRM_RESULTS_ALREADY_CURRENT" in {
            blocker["code"] for blocker in finalized_readiness["blockers"]
        }
        second_command = _confirm(
            case,
            key=f"second-success-{uuid.uuid4()}",
            review_hash=str(finalized_readiness["review_hash"]),
        )
        assert second_command.status_code == 409, second_command.text
        assert second_command.json()["code"] == "CONFIRM_RESULTS_BLOCKED"
        assert _formal_counts(case.db) == (1, 1, 1)


def test_confirm_results_uses_only_highest_non_voided_attempt_per_student() -> None:
    with _confirmable_case() as case:
        current = case.db.get(Submission, case.submission_id)
        current_answer = case.db.get(StudentAnswer, case.answer_id)
        current_result = case.db.get(GradingResult, case.result_id)
        assert current is not None and current.student_id is not None
        assert current_answer is not None and current_result is not None

        current.attempt_number = 2
        case.db.commit()
        superseded = Submission(
            owner_id=current.owner_id,
            grading_batch_id=current.grading_batch_id,
            assignment_id=current.assignment_id,
            class_id=current.class_id,
            student_id=current.student_id,
            attempt_number=1,
            status="matched",
            source="split",
        )
        case.db.add(superseded)
        case.db.flush()
        superseded_answer = StudentAnswer(
            submission_id=superseded.id,
            question_id=current_answer.question_id,
            question_version_reference=current_answer.question_version_reference,
            status="ready_for_grading",
            recognized_text="superseded synthetic attempt",
            requires_review=False,
        )
        case.db.add(superseded_answer)
        case.db.flush()
        case.db.add(
            GradingResult(
                grading_job_id=current_result.grading_job_id,
                student_answer_id=superseded_answer.id,
                question_id=current_result.question_id,
                rubric_version_id=current_result.rubric_version_id,
                grading_method=current_result.grading_method,
                provider=current_result.provider,
                provider_version=current_result.provider_version,
                prompt_version=current_result.prompt_version,
                score=current_result.score,
                max_score=current_result.max_score,
                confidence=current_result.confidence,
                requires_review=False,
                status="suggested",
            )
        )
        case.db.commit()

        readiness = _readiness(case)
        assert readiness["ready"] is True
        assert [item["submission_id"] for item in readiness["plan"]] == [str(current.id)]
        confirmed = _confirm(
            case,
            key=f"effective-attempt-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert confirmed.status_code == 201, confirmed.text
        release_id = uuid.UUID(confirmed.json()["grade_release_id"])
        release_items = list(
            case.db.scalars(
                select(GradeReleaseItem).where(GradeReleaseItem.grade_release_id == release_id)
            )
        )
        assert len(release_items) == 1
        assert release_items[0].submission_id == current.id
        case.db.refresh(superseded)
        assert superseded.status == "matched"
        assert superseded.finalized_at is None

        analytics = client.post(f"/api/grade-releases/{release_id}/analytics")
        assert analytics.status_code == 201, analytics.text
        assert analytics.json()["metrics"]["participant_count"] == 1


def test_same_key_replays_same_ids_and_different_payload_conflicts() -> None:
    with _confirmable_case() as case:
        readiness = _readiness(case)
        key = f"replay-{uuid.uuid4()}"
        first = _confirm(case, key=key, review_hash=str(readiness["review_hash"]))
        assert first.status_code == 201, first.text
        replay = _confirm(case, key=key, review_hash=str(readiness["review_hash"]))
        assert replay.status_code == 201, replay.text
        assert replay.json()["grade_release_id"] == first.json()["grade_release_id"]
        assert replay.json()["teacher_review_ids"] == first.json()["teacher_review_ids"]
        assert replay.json()["snapshot_ids"] == first.json()["snapshot_ids"]
        assert _formal_counts(case.db) == (1, 1, 1)

        conflict = _confirm(case, key=key, review_hash="0" * 64)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert _formal_counts(case.db) == (1, 1, 1)


def test_reopen_one_submission_reuses_unchanged_snapshot_and_versions_release() -> None:
    with _confirmable_case() as case:
        second_submission_id = _add_confirmable_submission(case)
        readiness = _readiness(case)
        assert readiness["ready"] is True
        assert readiness["new_snapshot_count"] == 2
        assert readiness["reused_snapshot_count"] == 0
        first = _confirm(
            case,
            key=f"first-release-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert first.status_code == 201, first.text
        first_payload = first.json()
        first_release_id = uuid.UUID(first_payload["grade_release_id"])
        first_items = {
            item.submission_id: item.score_snapshot_id
            for item in case.db.scalars(
                select(GradeReleaseItem).where(
                    GradeReleaseItem.grade_release_id == first_release_id
                )
            )
        }
        assert set(first_items) == {case.submission_id, second_submission_id}

        reopened = client.post(
            f"/api/submissions/{case.submission_id}/reopen",
            json={"reason": "Correct one synthetic submission"},
        )
        assert reopened.status_code == 200, reopened.text
        changed_answer = case.db.get(StudentAnswer, case.answer_id)
        assert changed_answer is not None
        changed_review = case.db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == case.answer_id)
        )
        assert changed_review is not None
        changed_review.final_feedback = "Corrected synthetic feedback"
        case.db.commit()
        partial = _readiness(case)
        assert partial["ready"] is True
        assert partial["confirmed_result"] is None
        assert partial["previous_grade_release_id"] == str(first_release_id)
        assert partial["new_snapshot_count"] == 1
        assert partial["reused_snapshot_count"] == 1
        reused_plan = [item for item in partial["plan"] if item["action"] == "reuse_snapshot"]
        assert len(reused_plan) == 1
        assert reused_plan[0]["submission_id"] == str(second_submission_id)
        assert reused_plan[0]["snapshot_id"] == str(first_items[second_submission_id])
        assert reused_plan[0]["snapshot_version"] == 1
        assert reused_plan[0]["changed_questions"] == []
        changed_plan = [item for item in partial["plan"] if item["action"] == "create_snapshot"]
        assert len(changed_plan) == 1
        assert changed_plan[0]["submission_id"] == str(case.submission_id)
        assert changed_plan[0]["student_name"]
        assert changed_plan[0]["student_number"]
        assert changed_plan[0]["changed_questions"] == [
            {
                "question_id": str(changed_answer.question_id),
                "question_number": "1",
            }
        ]

        second_key = f"second-release-{uuid.uuid4()}"
        second = _confirm(
            case,
            key=second_key,
            review_hash=str(partial["review_hash"]),
        )
        assert second.status_code == 201, second.text
        second_payload = second.json()
        assert second_payload["grade_release_version"] == 2
        assert second_payload["new_snapshot_count"] == 1
        assert second_payload["reused_snapshot_count"] == 1
        assert second_payload["previous_grade_release_id"] == str(first_release_id)
        second_release_id = uuid.UUID(second_payload["grade_release_id"])
        second_items = {
            item.submission_id: item.score_snapshot_id
            for item in case.db.scalars(
                select(GradeReleaseItem).where(
                    GradeReleaseItem.grade_release_id == second_release_id
                )
            )
        }
        assert second_items[second_submission_id] == first_items[second_submission_id]
        assert second_items[case.submission_id] != first_items[case.submission_id]
        assert second_payload["new_snapshot_ids"] == [str(second_items[case.submission_id])]
        assert second_payload["reused_snapshot_ids"] == [str(second_items[second_submission_id])]
        assert case.db.get(SubmissionScoreSnapshot, first_items[case.submission_id]) is not None
        assert case.db.scalar(select(func.count()).select_from(SubmissionScoreSnapshot)) == 3
        replay = _confirm(
            case,
            key=second_key,
            review_hash=str(partial["review_hash"]),
        )
        assert replay.status_code == 201
        assert replay.json()["grade_release_id"] == second_payload["grade_release_id"]
        assert replay.json()["snapshot_ids"] == second_payload["snapshot_ids"]

        current = _readiness(case)
        assert current["ready"] is False
        assert current["previous_grade_release_id"] == str(second_release_id)
        assert current["confirmed_result"]["grade_release_id"] == str(second_release_id)


def test_publish_and_confirm_share_assignment_serialization_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _confirmable_case() as case:
        readiness = _readiness(case)
        first = _confirm(
            case,
            key=f"serialized-first-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert first.status_code == 201, first.text
        first_release_id = first.json()["grade_release_id"]

        reopened = client.post(
            f"/api/submissions/{case.submission_id}/reopen",
            json={"reason": "Prepare a synthetic concurrent release"},
        )
        assert reopened.status_code == 200, reopened.text
        changed_review = case.db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == case.answer_id)
        )
        assert changed_review is not None
        changed_review.final_feedback = "Synthetic concurrent correction"
        case.db.commit()
        second_readiness = _readiness(case)
        assert second_readiness["ready"] is True

        publish_at_visibility_write = Event()
        allow_publish_to_commit = Event()
        confirm_request_started = Event()
        confirm_lock_sql_started = Event()
        confirm_reached_state_read = Event()
        original_results_now = results_api.now_utc
        original_confirm_state = grading_api._confirm_results_state

        def pause_publish_after_superseded_check():
            publish_at_visibility_write.set()
            assert allow_publish_to_commit.wait(5)
            return original_results_now()

        def observe_confirm_state(*args, **kwargs):
            confirm_reached_state_read.set()
            return original_confirm_state(*args, **kwargs)

        def observe_assignment_lock_sql(
            _conn, _cursor, statement, _parameters, _context, _executemany
        ):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("update assignments set updated_at=assignments.updated_at"):
                confirm_lock_sql_started.set()

        def issue_confirm():
            confirm_request_started.set()
            return _confirm(
                case,
                key=f"serialized-second-{uuid.uuid4()}",
                review_hash=str(second_readiness["review_hash"]),
            )

        monkeypatch.setattr(results_api, "now_utc", pause_publish_after_superseded_check)
        monkeypatch.setattr(grading_api, "_confirm_results_state", observe_confirm_state)

        with ThreadPoolExecutor(max_workers=2) as executor:
            publish_future = executor.submit(
                client.post,
                f"/api/grade-releases/{first_release_id}/publish-to-students",
            )
            assert publish_at_visibility_write.wait(5)
            event.listen(engine, "before_cursor_execute", observe_assignment_lock_sql)
            try:
                confirm_future = executor.submit(issue_confirm)
                assert confirm_request_started.wait(5), "confirm HTTP request did not start"
                assert confirm_lock_sql_started.wait(5), (
                    "confirm did not reach the assignment serialization SQL"
                )
                assert not confirm_reached_state_read.wait(0.5), (
                    "confirm crossed the assignment lock while publish still held it"
                )
            finally:
                allow_publish_to_commit.set()
                event.remove(engine, "before_cursor_execute", observe_assignment_lock_sql)
            published = publish_future.result(timeout=5)
            confirmed = confirm_future.result(timeout=5)

        assert published.status_code == 200, published.text
        assert confirmed.status_code == 201, confirmed.text
        assert confirmed.json()["grade_release_version"] == 2
        assert confirm_reached_state_read.is_set()


def test_active_rubric_version_change_prevents_snapshot_reuse() -> None:
    with _confirmable_case() as case:
        readiness = _readiness(case)
        first = _confirm(
            case,
            key=f"version-first-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert first.status_code == 201, first.text
        counts = _formal_counts(case.db)

        submission = case.db.get(Submission, case.submission_id)
        assert submission is not None
        assignment = case.db.get(Assignment, submission.assignment_id)
        assert assignment is not None and assignment.active_rubric_version_id is not None
        old_version = case.db.get(RubricVersion, assignment.active_rubric_version_id)
        assert old_version is not None
        old_rubric = case.db.scalar(
            select(QuestionRubric).where(QuestionRubric.rubric_version_id == old_version.id)
        )
        assert old_rubric is not None
        new_version = RubricVersion(
            assignment_id=assignment.id,
            version=old_version.version + 1,
            status=VersionStatus.confirmed,
            created_by=old_version.created_by,
            confirmed_at=old_version.confirmed_at,
            notes="synthetic changed rubric version",
        )
        case.db.add(new_version)
        case.db.flush()
        new_rubric = QuestionRubric(
            rubric_version_id=new_version.id,
            question_id=old_rubric.question_id,
            standard_answer=old_rubric.standard_answer,
            alternative_answers=old_rubric.alternative_answers,
            scoring_notes=old_rubric.scoring_notes,
            allow_step_score=old_rubric.allow_step_score,
            unit_requirement=old_rubric.unit_requirement,
            format_requirement=old_rubric.format_requirement,
            precision_requirement=old_rubric.precision_requirement,
        )
        case.db.add(new_rubric)
        case.db.flush()
        for item in case.db.scalars(
            select(RubricItem).where(RubricItem.question_rubric_id == old_rubric.id)
        ):
            case.db.add(
                RubricItem(
                    question_rubric_id=new_rubric.id,
                    display_order=item.display_order,
                    title=item.title,
                    description=item.description,
                    points=item.points,
                    item_type=item.item_type,
                    required=item.required,
                    deduction_rule=item.deduction_rule,
                )
            )
        assignment.active_rubric_version_id = new_version.id
        case.db.commit()

        changed = _readiness(case)
        assert changed["ready"] is False
        assert changed["confirmed_result"] is None
        assert changed["reused_snapshot_count"] == 0
        assert {blocker["code"] for blocker in changed["blockers"]} & {
            "SNAPSHOT_REUSE_MISMATCH",
            "RUBRIC_VERSION_MISMATCH",
            "STALE_RUBRIC",
        }
        blocked = _confirm(
            case,
            key=f"version-blocked-{uuid.uuid4()}",
            review_hash=str(changed["review_hash"]),
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "CONFIRM_RESULTS_BLOCKED"
        assert _formal_counts(case.db) == counts


@pytest.mark.parametrize("mutation", ["question_number", "question_type", "knowledge_point"])
def test_formal_explanation_change_prevents_snapshot_reuse_without_writes(
    mutation: str,
) -> None:
    with _confirmable_case() as case:
        readiness = _readiness(case)
        first = _confirm(
            case,
            key=f"explanation-first-{mutation}-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert first.status_code == 201, first.text
        counts = _formal_counts(case.db)
        answer = case.db.get(StudentAnswer, case.answer_id)
        assert answer is not None
        question = case.db.get(Question, answer.question_id)
        submission = case.db.get(Submission, case.submission_id)
        assert question is not None and submission is not None
        if mutation == "question_number":
            question.question_number = f"{question.question_number}-changed"
        elif mutation == "question_type":
            question.question_type = "short_answer"
        else:
            point = KnowledgePoint(
                owner_id=submission.owner_id,
                subject="synthetic",
                grade="synthetic",
                name=f"changed-{uuid.uuid4()}",
            )
            case.db.add(point)
            case.db.flush()
            case.db.add(
                QuestionKnowledgePoint(
                    question_id=question.id,
                    knowledge_point_id=point.id,
                )
            )
        case.db.commit()

        changed = _readiness(case)
        assert changed["ready"] is False
        assert changed["confirmed_result"] is None
        assert "SNAPSHOT_REUSE_MISMATCH" in {blocker["code"] for blocker in changed["blockers"]}
        blocked = _confirm(
            case,
            key=f"explanation-blocked-{mutation}-{uuid.uuid4()}",
            review_hash=str(changed["review_hash"]),
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "CONFIRM_RESULTS_BLOCKED"
        assert _formal_counts(case.db) == counts


def test_review_hash_rejects_stale_answer_without_partial_writes() -> None:
    with _confirmable_case() as case:
        readiness = _readiness(case)
        answer = case.db.get(StudentAnswer, case.answer_id)
        assert answer is not None
        answer.corrected_text = f"teacher changed answer {uuid.uuid4()}"
        case.db.commit()

        response = _confirm(
            case,
            key=f"stale-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CONFIRM_RESULTS_STALE"
        assert response.json()["details"]["current_review_hash"] != readiness["review_hash"]
        assert _formal_counts(case.db) == (0, 0, 0)


def test_zero_active_questions_blocks_without_formal_writes() -> None:
    with _confirmable_case() as case:
        answer = case.db.get(StudentAnswer, case.answer_id)
        assert answer is not None
        question = case.db.get(Question, answer.question_id)
        assert question is not None
        question.status = "removed"
        case.db.commit()

        readiness = _readiness(case)
        assert readiness["ready"] is False
        assert "QUESTION_MISSING" in {blocker["code"] for blocker in readiness["blockers"]}
        assert _formal_counts(case.db) == (0, 0, 0)

        response = _confirm(
            case,
            key=f"zero-questions-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CONFIRM_RESULTS_BLOCKED"
        assert _formal_counts(case.db) == (0, 0, 0)


@pytest.mark.parametrize(
    ("mutation", "expected_blocker"),
    [
        ("result_max_score", "RESULT_MAX_SCORE_MISMATCH"),
        ("answer_paper_version", "PAPER_VERSION_MISMATCH"),
    ],
)
def test_version_binding_mismatch_blocks_without_formal_writes(
    mutation: str, expected_blocker: str
) -> None:
    with _confirmable_case() as case:
        if mutation == "result_max_score":
            result = case.db.get(GradingResult, case.result_id)
            assert result is not None
            result.max_score += 1
        else:
            answer = case.db.get(StudentAnswer, case.answer_id)
            assert answer is not None
            answer.question_version_reference = str(uuid.uuid4())
        case.db.commit()

        readiness = _readiness(case)
        assert readiness["ready"] is False
        assert expected_blocker in {blocker["code"] for blocker in readiness["blockers"]}
        response = _confirm(
            case,
            key=f"binding-mismatch-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CONFIRM_RESULTS_BLOCKED"
        assert _formal_counts(case.db) == (0, 0, 0)


def test_review_bound_to_older_result_blocks_when_a_newer_result_exists() -> None:
    with _confirmable_case() as case:
        reviewed = client.put(
            f"/api/student-answers/{case.answer_id}/review",
            json={"decision": "accepted"},
        )
        assert reviewed.status_code == 200, reviewed.text
        old_result = case.db.get(GradingResult, case.result_id)
        assert old_result is not None
        newer_result = GradingResult(
            grading_job_id=old_result.grading_job_id,
            student_answer_id=old_result.student_answer_id,
            question_id=old_result.question_id,
            rubric_version_id=old_result.rubric_version_id,
            grading_method=old_result.grading_method,
            provider=old_result.provider,
            provider_version=old_result.provider_version,
            prompt_version=old_result.prompt_version,
            score=old_result.score,
            max_score=old_result.max_score,
            confidence=old_result.confidence,
            recognized_answer_snapshot=old_result.recognized_answer_snapshot,
            reasoning_summary=old_result.reasoning_summary,
            error_type=old_result.error_type,
            student_feedback=old_result.student_feedback,
            requires_review=False,
            status="suggested",
            created_at=old_result.created_at + timedelta(seconds=1),
            updated_at=old_result.updated_at + timedelta(seconds=1),
        )
        case.db.add(newer_result)
        case.db.commit()

        readiness = _readiness(case)
        assert readiness["ready"] is False
        assert "REVIEW_RESULT_MISMATCH" in {blocker["code"] for blocker in readiness["blockers"]}
        response = _confirm(
            case,
            key=f"newer-result-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CONFIRM_RESULTS_BLOCKED"
        assert _formal_counts(case.db) == (1, 0, 0)


def test_review_feedback_change_makes_preflight_hash_stale_without_formal_chain() -> None:
    with _confirmable_case() as case:
        reviewed = client.put(
            f"/api/student-answers/{case.answer_id}/review",
            json={"decision": "accepted"},
        )
        assert reviewed.status_code == 200, reviewed.text
        readiness = _readiness(case)
        assert readiness["ready"] is True

        review = case.db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == case.answer_id)
        )
        assert review is not None
        review.final_feedback = f"changed after readiness {uuid.uuid4()}"
        case.db.commit()

        response = _confirm(
            case,
            key=f"stale-review-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CONFIRM_RESULTS_STALE"
        assert response.json()["details"]["current_review_hash"] != readiness["review_hash"]
        assert _formal_counts(case.db) == (1, 0, 0)


def test_concurrent_same_command_creates_exactly_one_formal_chain() -> None:
    with _confirmable_case() as case:
        readiness = _readiness(case)
        key = f"concurrent-{uuid.uuid4()}"

        def submit() -> tuple[int, dict[str, object]]:
            response = _confirm(case, key=key, review_hash=str(readiness["review_hash"]))
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _index: submit(), range(2)))

        assert [status for status, _payload in responses] == [201, 201]
        assert len({payload["grade_release_id"] for _status, payload in responses}) == 1
        assert len({tuple(payload["teacher_review_ids"]) for _status, payload in responses}) == 1
        assert len({tuple(payload["snapshot_ids"]) for _status, payload in responses}) == 1
        assert _formal_counts(case.db) == (1, 1, 1)
