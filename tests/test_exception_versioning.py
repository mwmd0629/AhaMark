import copy
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.main import app
from app.models import (
    AnalyticsSnapshot,
    ClassStudent,
    GradeRelease,
    GradeReleaseItem,
    GradingResult,
    MembershipStatus,
    ReportJob,
    RubricVersion,
    ScoreRevision,
    StoredFile,
    Student,
    StudentAnswer,
    Submission,
    SubmissionFileMatch,
    SubmissionPage,
    SubmissionScoreSnapshot,
    TeachingInsight,
    VersionStatus,
    now_utc,
)
from app.results.services import FinalScoreService, release_scores
from app.storage.dependencies import get_storage
from sqlalchemy import func, select
from test_submission_workflow import client, confirm_answer_regions, png, workflow


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"recognized_text": None, "is_blank": True}, "blank"),
        ({"recognized_text": "低置信答案", "recognition_confidence": "0.20"}, "low_confidence"),
        ({"recognized_text": "x + 1", "recognized_latex": "x+1"}, "formula_unavailable"),
    ],
)
def test_submission_answer_exception_states_are_distinct(
    payload: dict[str, object], expected_status: str
) -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    try:
        response = client.post(
            f"/api/submissions/{submission_id}/answers",
            json={"question_id": question_id, **payload},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == expected_status
        assert response.json()["requires_review"] is True
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_ambiguous_match_requires_manual_confirmation_and_is_idempotent() -> None:
    db, _storage, batch_id, _submission_id, _question_id = workflow()
    first = db.scalar(select(Student).where(Student.student_number == "0001"))
    assert first is not None
    second = Student(owner_id=first.owner_id, student_number="0002", name=first.name)
    db.add(second)
    db.flush()
    school_class_id = db.scalar(
        select(ClassStudent.class_id).where(ClassStudent.student_id == first.id)
    )
    assert school_class_id is not None
    db.add(
        ClassStudent(
            class_id=school_class_id,
            student_id=second.id,
            status=MembershipStatus.active,
            joined_at=now_utc(),
        )
    )
    db.commit()
    try:
        upload = client.post(
            f"/api/grading-batches/{batch_id}/files",
            files={"files": (f"{first.name}-歧义.png", png("azure"), "image/png")},
        )
        assert upload.status_code == 201, upload.text
        item = upload.json()["items"][0]
        assert item["method"] == "ambiguous"
        assert item["status"] == "pending"
        assert item["submission_id"] is None
        batch = client.get(f"/api/grading-batches/{batch_id}").json()
        pending = next(
            match for match in batch["matching"]["items"] if match["id"] == item["match_id"]
        )
        assert pending["reason"] == "文件名包含多个学生标识" or pending["method"] == "ambiguous"

        first_confirm = client.post(
            f"/api/grading-batches/{batch_id}/matches/{item['match_id']}/confirm",
            json={"student_id": str(second.id)},
        )
        assert first_confirm.status_code == 200
        second_confirm = client.post(
            f"/api/grading-batches/{batch_id}/matches/{item['match_id']}/confirm",
            json={"student_id": str(second.id)},
        )
        assert second_confirm.status_code == 200
        assert second_confirm.json()["submission_id"] == first_confirm.json()["submission_id"]
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubmissionPage)
                .join(
                    SubmissionFileMatch,
                    SubmissionFileMatch.stored_file_id == SubmissionPage.stored_file_id,
                )
                .where(SubmissionFileMatch.id == uuid.UUID(item["match_id"]))
            )
            == 1
        )
        conflict = client.post(
            f"/api/grading-batches/{batch_id}/matches/{item['match_id']}/confirm",
            json={"student_id": str(first.id)},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "MATCH_ALREADY_CONFIRMED"
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_score_release_report_analytics_and_insight_versions_remain_fixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    settings = get_settings()
    previous_provider = settings.recognition_provider
    previous_answer_provider = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "exceptions-versioning-processing"},
        )
        assert processing.status_code == 201, processing.text
        assert processing.json()["status"] == "completed"
        confirm_answer_regions(db, submission_id)
        recognition = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "exceptions-versioning-ocr"},
        )
        assert recognition.status_code == 201
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        corrected = client.patch(
            f"/api/student-answers/{answer.id}",
            json={"corrected_text": "1. 测试题"},
        )
        assert corrected.status_code == 200, corrected.text
        answer.status, answer.requires_review = "ready_for_grading", False
        db.commit()

        grade_v1 = client.post(f"/api/student-answers/{answer.id}/grade")
        assert grade_v1.status_code == 200
        review_v1 = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={"decision": "accepted"},
        )
        assert review_v1.status_code == 200
        snapshot_v1 = client.post(f"/api/submissions/{submission_id}/finalize")
        assert snapshot_v1.status_code == 200
        assert snapshot_v1.json()["status"] == "complete"
        assert snapshot_v1.json()["version"] == 1
        assert snapshot_v1.json()["total_score"] == "10.00"
        snapshot_v1_details = copy.deepcopy(snapshot_v1.json()["details"])

        submission = db.get(Submission, submission_id)
        assert submission is not None
        release_v1 = client.post(
            "/api/grade-releases",
            json={
                "assignment_id": str(submission.assignment_id),
                "class_id": str(submission.class_id),
                "idempotency_key": "exceptions-release-v1",
            },
        )
        assert release_v1.status_code == 201, release_v1.text
        analytics_v1 = client.post(f"/api/grade-releases/{release_v1.json()['id']}/analytics")
        assert analytics_v1.status_code == 201
        metrics_v1 = copy.deepcopy(analytics_v1.json()["metrics"])
        assert metrics_v1["participant_count"] == 1
        assert metrics_v1["average_score"] == 10
        analytics_v1_repeat = client.post(
            f"/api/grade-releases/{release_v1.json()['id']}/analytics"
        )
        assert analytics_v1_repeat.status_code == 201
        assert analytics_v1_repeat.json()["id"] == analytics_v1.json()["id"]
        assert (
            db.scalar(
                select(func.count())
                .select_from(AnalyticsSnapshot)
                .where(AnalyticsSnapshot.grade_release_id == uuid.UUID(release_v1.json()["id"]))
            )
            == 1
        )

        insight_v1 = client.post(f"/api/analytics/{analytics_v1.json()['id']}/insights")
        assert insight_v1.status_code == 201
        edited = client.patch(
            f"/api/teaching-insights/{insight_v1.json()['id']}",
            json={"recommendations": ["合成规则建议：复核第 1 题"]},
        )
        assert edited.status_code == 200 and edited.json()["status"] == "draft"
        confirmed = client.post(f"/api/teaching-insights/{insight_v1.json()['id']}/confirm")
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
        confirmed_content = copy.deepcopy(confirmed.json()["content"])

        review_v2 = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={
                "decision": "modified",
                "final_score": 8,
                "final_feedback": "合成改分",
                "reason": "第三部分版本一致性测试",
            },
        )
        assert review_v2.status_code == 200
        snapshot_v2 = client.post(f"/api/submissions/{submission_id}/finalize")
        assert snapshot_v2.status_code == 200
        assert snapshot_v2.json()["status"] == "complete"
        assert snapshot_v2.json()["version"] == 2
        assert snapshot_v2.json()["total_score"] == "8.00"
        assert db.scalar(select(func.count()).select_from(ScoreRevision)) == 1

        release_v2 = client.post(
            "/api/grade-releases",
            json={
                "assignment_id": str(submission.assignment_id),
                "class_id": str(submission.class_id),
                "idempotency_key": "exceptions-release-v2",
            },
        )
        assert release_v2.status_code == 201
        analytics_v2 = client.post(f"/api/grade-releases/{release_v2.json()['id']}/analytics")
        assert analytics_v2.status_code == 201
        assert analytics_v2.json()["metrics"]["average_score"] == 8

        release_1_model = db.get(GradeRelease, uuid.UUID(release_v1.json()["id"]))
        release_2_model = db.get(GradeRelease, uuid.UUID(release_v2.json()["id"]))
        assert release_1_model is not None and release_2_model is not None
        item_v1 = db.scalar(
            select(GradeReleaseItem).where(GradeReleaseItem.grade_release_id == release_1_model.id)
        )
        item_v2 = db.scalar(
            select(GradeReleaseItem).where(GradeReleaseItem.grade_release_id == release_2_model.id)
        )
        assert item_v1 is not None and item_v2 is not None
        assert str(item_v1.score_snapshot_id) == snapshot_v1.json()["id"]
        assert str(item_v2.score_snapshot_id) == snapshot_v2.json()["id"]
        assert release_scores(db, release_1_model.id)[0].payload.total_score == Decimal("10")
        assert release_scores(db, release_2_model.id)[0].payload.total_score == Decimal("8")
        stored_v1 = db.get(SubmissionScoreSnapshot, uuid.UUID(snapshot_v1.json()["id"]))
        assert stored_v1 is not None and stored_v1.details == snapshot_v1_details
        old_analytics = db.get(AnalyticsSnapshot, uuid.UUID(analytics_v1.json()["id"]))
        assert old_analytics is not None and old_analytics.metrics == metrics_v1

        regenerated = client.post(f"/api/teaching-insights/{insight_v1.json()['id']}/regenerate")
        assert regenerated.status_code == 201
        old_insight = db.get(TeachingInsight, uuid.UUID(insight_v1.json()["id"]))
        assert old_insight is not None
        assert old_insight.status == "superseded"
        assert old_insight.content == confirmed_content
        invalidated = client.post(f"/api/teaching-insights/{regenerated.json()['id']}/invalidate")
        assert invalidated.status_code == 200
        assert invalidated.json()["status"] == "invalid"

        from workers.celery_app import celery_app

        monkeypatch.setattr(celery_app, "send_task", lambda *_args, **_kwargs: None)
        failed_report = ReportJob(
            owner_id=submission.owner_id,
            assignment_id=submission.assignment_id,
            class_id=submission.class_id,
            grade_release_id=release_1_model.id,
            report_type="gradebook_xlsx",
            status="failed",
            idempotency_key="exceptions-report-failed",
            error_code="SYNTHETIC_FAILURE",
            expires_at=now_utc() + timedelta(days=1),
        )
        db.add(failed_report)
        db.commit()
        retry = client.post(f"/api/report-jobs/{failed_report.id}/retry")
        assert retry.status_code == 201
        assert retry.json()["id"] != str(failed_report.id)
        db.refresh(failed_report)
        assert failed_report.status == "failed"

        stored_file = db.scalar(select(StoredFile))
        assert stored_file is not None
        expired_report = ReportJob(
            owner_id=submission.owner_id,
            assignment_id=submission.assignment_id,
            class_id=submission.class_id,
            grade_release_id=release_1_model.id,
            report_type="gradebook_xlsx",
            status="completed",
            progress=100,
            stored_file_id=stored_file.id,
            idempotency_key="exceptions-report-expired",
            expires_at=now_utc() - timedelta(seconds=1),
        )
        db.add(expired_report)
        db.commit()
        assert client.get(f"/api/report-jobs/{expired_report.id}").json()["status"] == "expired"
        expired_download = client.get(f"/api/report-jobs/{expired_report.id}/download")
        assert expired_download.status_code == 409
        assert expired_download.json()["code"] == "REPORT_JOB_EXPIRED"

        assignment = client.get(f"/api/assignments/{submission.assignment_id}").json()
        rubric_change = client.put(
            f"/api/assignments/{submission.assignment_id}/rubrics/{question_id}",
            json={
                "standard_answer": "修订后的合成答案",
                "items": [{"title": "修订评分点", "points": 10}],
            },
        )
        assert rubric_change.status_code == 200
        assert (
            rubric_change.json()["rubric_version"]["version"]
            == assignment["rubric_version"]["version"] + 1
        )
        db.refresh(answer)
        assert answer.status == "stale" and answer.requires_review is True
        old_result = db.get(GradingResult, uuid.UUID(grade_v1.json()["id"]))
        assert old_result is not None and old_result.status == "stale"
        stale_accept = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={"decision": "accepted"},
        )
        assert stale_accept.status_code == 409
        assert stale_accept.json()["code"] == "GRADING_RESULT_STALE"
        republish = client.post(f"/api/assignments/{submission.assignment_id}/publish")
        # A published assignment cannot bypass the new readiness contract.
        # Re-publication requires a fresh teacher review flow rather than
        # the historical body-less endpoint.
        assert republish.status_code == 422, republish.text
        assert (
            client.get(f"/api/assignments/{submission.assignment_id}").json()["status"]
            == "published"
        )
        # Continue this legacy downstream versioning fixture with an explicitly
        # confirmed rubric; production confirmation now occurs through review.
        changed_rubric = db.get(
            RubricVersion, uuid.UUID(rubric_change.json()["rubric_version"]["id"])
        )
        assert changed_rubric is not None
        changed_rubric.status = VersionStatus.confirmed
        changed_rubric.confirmed_at = now_utc()
        db.commit()
        blocked = client.post(f"/api/submissions/{submission_id}/finalize")
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "incomplete"
        assert all(
            snapshot.status != "complete"
            for snapshot in db.scalars(
                select(SubmissionScoreSnapshot).where(
                    SubmissionScoreSnapshot.version == blocked.json()["version"],
                    SubmissionScoreSnapshot.submission_id == submission_id,
                )
            )
        )

        finalized_regrade = client.post(f"/api/student-answers/{answer.id}/grade")
        assert finalized_regrade.status_code == 409
        assert finalized_regrade.json()["code"] == "SUBMISSION_FINALIZED"

        current_batch = client.post(
            f"/api/assignments/{submission.assignment_id}/grading-batches",
            json={"class_id": str(submission.class_id)},
        )
        assert current_batch.status_code == 201, current_batch.text
        next_upload = client.post(
            f"/api/grading-batches/{current_batch.json()['id']}/files",
            files=[("files", ("0001-regrade.png", png("azure"), "image/png"))],
        )
        assert next_upload.status_code == 201, next_upload.text
        current_submission_id = uuid.UUID(next_upload.json()["items"][0]["submission_id"])
        assert current_submission_id != submission_id
        current_processing = client.post(
            f"/api/submissions/{current_submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "exceptions-versioning-processing-current"},
        )
        assert current_processing.status_code == 201, current_processing.text
        assert current_processing.json()["status"] == "completed"
        confirm_answer_regions(db, current_submission_id)
        current_recognition = client.post(
            f"/api/submissions/{current_submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "exceptions-versioning-ocr-current"},
        )
        assert current_recognition.status_code == 201, current_recognition.text
        current_answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == current_submission_id)
        )
        assert current_answer is not None
        current_corrected = client.patch(
            f"/api/student-answers/{current_answer.id}",
            json={"corrected_text": "1. 测试题"},
        )
        assert current_corrected.status_code == 200, current_corrected.text
        current_answer.status, current_answer.requires_review = "ready_for_grading", False
        db.commit()
        regrade = client.post(f"/api/student-answers/{current_answer.id}/grade")
        assert regrade.status_code == 200, regrade.text
        db.refresh(current_answer)
        assert current_answer.status == "graded"
        rereview = client.put(
            f"/api/student-answers/{current_answer.id}/review",
            json={"decision": "accepted"},
        )
        assert rereview.status_code == 200
        snapshot_current = client.post(f"/api/submissions/{current_submission_id}/finalize")
        assert snapshot_current.status_code == 200
        assert snapshot_current.json()["status"] == "complete"
        extra_incomplete = Submission(
            owner_id=submission.owner_id,
            grading_batch_id=submission.grading_batch_id,
            assignment_id=submission.assignment_id,
            class_id=submission.class_id,
            student_id=submission.student_id,
            attempt_number=2,
            status="recognized",
            source="split_page",
        )
        db.add(extra_incomplete)
        db.commit()
        readiness = client.get(
            f"/api/assignments/{submission.assignment_id}/classes/"
            f"{submission.class_id}/grade-readiness"
        )
        assert readiness.status_code == 200
        assert readiness.json()["releasable_count"] == 1
        assert readiness.json()["unreleasable_count"] == 0
        assert readiness.json()["ready"][0]["submission_id"] == str(current_submission_id)
        assert readiness.json()["ready"][0]["score_snapshot_id"] == snapshot_current.json()["id"]
        latest = FinalScoreService(db, submission.owner_id).latest(
            submission.assignment_id, submission.class_id
        )
        assert len(latest) == 1
        assert str(latest[0].snapshot.id) == snapshot_current.json()["id"]
        assert stored_v1.details == snapshot_v1_details
        assert item_v1.score_snapshot_id == uuid.UUID(snapshot_v1.json()["id"])
        assert item_v2.score_snapshot_id == uuid.UUID(snapshot_v2.json()["id"])
        assert old_analytics.metrics == metrics_v1
    finally:
        settings.recognition_provider = previous_provider
        settings.answer_recognition_provider = previous_answer_provider
        app.dependency_overrides.pop(get_storage, None)
        db.close()
