import uuid
from decimal import Decimal

from app.api.actor import CurrentActor, get_current_actor
from app.main import app
from app.models import (
    AuditLog,
    GradingCollaborator,
    GradingQuestionAssignment,
    Question,
    Role,
    ScoreRevision,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    TeacherReview,
    User,
    UserRole,
)
from app.storage.dependencies import get_storage
from sqlalchemy import select
from test_submission_workflow import client, workflow


def test_question_scoped_collaboration_conflict_and_release_boundary() -> None:
    db, _storage, batch_id, submission_id, question_id = workflow()
    owner = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert owner is not None
    collaborator = User(
        email="collaborator@example.com",
        password_hash="!test!",
        display_name="协作老师",
    )
    db.add(collaborator)
    db.flush()
    teacher_role = db.scalar(select(Role).where(Role.name == "teacher"))
    if teacher_role is None:
        teacher_role = Role(name="teacher", description="教师端")
        db.add(teacher_role)
        db.flush()
    db.add(UserRole(user_id=collaborator.id, role_id=teacher_role.id))
    student_role = Role(name="student", description="学生端")
    non_teacher_role = Role(name="auditor", description="只读审计")
    student_account = User(
        email="student-collaborator@example.com",
        password_hash="!test!",
        display_name="学生账号",
    )
    no_role_account = User(
        email="no-role-collaborator@example.com",
        password_hash="!test!",
        display_name="无角色账号",
    )
    non_teacher_account = User(
        email="auditor-collaborator@example.com",
        password_hash="!test!",
        display_name="审计账号",
    )
    db.add_all(
        [student_role, non_teacher_role, student_account, no_role_account, non_teacher_account]
    )
    db.flush()
    db.add(UserRole(user_id=student_account.id, role_id=student_role.id))
    db.add(UserRole(user_id=non_teacher_account.id, role_id=non_teacher_role.id))
    answer = db.scalar(
        select(StudentAnswer).where(
            StudentAnswer.submission_id == submission_id,
            StudentAnswer.question_id == uuid.UUID(question_id),
        )
    )
    if answer is None:
        answer = StudentAnswer(
            submission_id=submission_id,
            question_id=uuid.UUID(question_id),
            question_version_reference="collaboration-test-v1",
            status="manually_entered",
            recognized_text="synthetic answer",
            requires_review=False,
        )
        db.add(answer)
        db.flush()
    review = TeacherReview(
        student_answer_id=answer.id,
        reviewer_id=owner.id,
        decision="manual_scored",
        final_score=Decimal("5"),
        review_version=1,
    )
    db.add(review)
    first_question = db.get(Question, uuid.UUID(question_id))
    assert first_question is not None
    unassigned_question = Question(
        paper_version_id=first_question.paper_version_id,
        question_number="2",
        display_order=2,
        question_type="short_answer",
        content_text="unassigned synthetic question",
        max_score=Decimal("10"),
        source="manual",
    )
    db.add(unassigned_question)
    db.flush()
    unassigned_answer = StudentAnswer(
        submission_id=submission_id,
        question_id=unassigned_question.id,
        question_version_reference="collaboration-test-v1",
        status="manually_entered",
        recognized_text="unassigned synthetic answer",
        requires_review=True,
    )
    db.add(unassigned_answer)
    db.flush()
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission_id)
        .order_by(SubmissionPage.page_number)
    ).all()
    assert len(pages) == 3
    pages[0].processed_storage_key = "scoped/assigned-page.png"
    pages[1].processed_storage_key = "scoped/unassigned-page.png"
    pages[2].processed_storage_key = "scoped/unmapped-page.png"
    db.add_all(
        [
            StudentAnswerRegion(
                student_answer_id=answer.id,
                submission_page_id=pages[0].id,
                x=Decimal("0"),
                y=Decimal("0"),
                width=Decimal("1"),
                height=Decimal("1"),
                source="manual",
                status="confirmed",
            ),
            StudentAnswerRegion(
                student_answer_id=unassigned_answer.id,
                submission_page_id=pages[1].id,
                x=Decimal("0"),
                y=Decimal("0"),
                width=Decimal("1"),
                height=Decimal("1"),
                source="manual",
                status="confirmed",
            ),
        ]
    )
    original_submission = db.get(Submission, submission_id)
    assert original_submission is not None
    unrelated_submission = Submission(
        owner_id=original_submission.owner_id,
        grading_batch_id=original_submission.grading_batch_id,
        assignment_id=original_submission.assignment_id,
        class_id=original_submission.class_id,
        student_id=None,
        attempt_number=1,
        status="uploaded",
    )
    db.add(unrelated_submission)
    db.flush()
    db.add(
        SubmissionPage(
            submission_id=unrelated_submission.id,
            stored_file_id=pages[2].stored_file_id,
            page_number=1,
            source_page_number=1,
            status="ready",
            processed_storage_key="scoped/unrelated-page.png",
        )
    )
    db.commit()

    try:
        rejected_student = client.post(
            f"/api/grading-batches/{batch_id}/collaborators",
            json={"email": student_account.email},
        )
        assert rejected_student.status_code == 422, rejected_student.text
        assert rejected_student.json()["code"] == "COLLABORATOR_TEACHER_REQUIRED"
        for rejected_account in (no_role_account, non_teacher_account):
            rejected = client.post(
                f"/api/grading-batches/{batch_id}/collaborators",
                json={"email": rejected_account.email},
            )
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["code"] == "COLLABORATOR_TEACHER_REQUIRED"

        added = client.post(
            f"/api/grading-batches/{batch_id}/collaborators",
            json={"email": collaborator.email},
        )
        assert added.status_code == 201, added.text
        assigned = client.put(
            f"/api/grading-batches/{batch_id}/question-assignments/{question_id}",
            json={"assignee_id": str(collaborator.id)},
        )
        assert assigned.status_code == 200, assigned.text

        app.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            collaborator.id, collaborator.email
        )
        workspace = client.get(f"/api/grading-batches/{batch_id}/review-workspace")
        assert workspace.status_code == 200, workspace.text
        payload = workspace.json()
        assert payload["collaboration"]["is_owner"] is False
        assert payload["collaboration"]["can_confirm_results"] is False
        assert payload["batch"]["matching"]["items"] == []
        assert payload["batch"]["matching"]["student_options"] == []
        assert payload["batch"]["actions"] == ["grade"]
        assert {
            item["question"]["id"]
            for submission in payload["items"]
            for item in submission["answers"]
        } == {question_id}
        assert len(payload["items"]) == 1
        assert [page["id"] for page in payload["items"][0]["pages"]] == [str(pages[0].id)]
        assert payload["items"][0]["pages"][0]["original_url"] is None
        assert payload["items"][0]["pages"][0]["processed_url"].endswith(
            "/scoped/assigned-page.png"
        )
        forbidden_question = client.get(
            f"/api/grading-batches/{batch_id}/review-workspace",
            params={"question_id": str(unassigned_question.id)},
        )
        assert forbidden_question.status_code == 200, forbidden_question.text
        assert forbidden_question.json()["items"] == []
        unrelated = client.get(
            f"/api/grading-batches/{batch_id}/review-workspace",
            params={"submission_id": str(unrelated_submission.id)},
        )
        assert unrelated.status_code == 200, unrelated.text
        assert unrelated.json()["items"] == []

        forbidden_release = client.get(f"/api/grading-batches/{batch_id}/confirm-results/readiness")
        assert forbidden_release.status_code == 404
        forbidden_review = client.put(
            f"/api/student-answers/{unassigned_answer.id}/review",
            json={
                "decision": "manual_scored",
                "final_score": "8",
                "reason": "越权测试",
            },
        )
        assert forbidden_review.status_code == 403
        assert forbidden_review.json()["code"] == "GRADING_SCOPE_FORBIDDEN"

        saved = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={
                "decision": "manual_scored",
                "final_score": "6",
                "reason": "协作老师复核",
                "expected_review_version": 1,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["review_version"] == 2

        stale = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={
                "decision": "manual_scored",
                "final_score": "7",
                "reason": "陈旧页面覆盖",
                "expected_review_version": 1,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "REVIEW_CONFLICT"
        db.expire_all()
        assert db.get(TeacherReview, review.id).final_score == Decimal("6")
        assert (
            db.scalar(
                select(ScoreRevision).where(ScoreRevision.teacher_review_id == review.id)
            ).actor_id
            == collaborator.id
        )

        app.dependency_overrides.pop(get_current_actor, None)
        removed = client.delete(f"/api/grading-batches/{batch_id}/collaborators/{collaborator.id}")
        assert removed.status_code == 204, removed.text
        db.expire_all()
        assert (
            db.scalar(
                select(GradingCollaborator).where(GradingCollaborator.user_id == collaborator.id)
            ).status
            == "inactive"
        )
        assert (
            db.scalar(
                select(GradingQuestionAssignment).where(
                    GradingQuestionAssignment.assignee_id == collaborator.id
                )
            )
            is None
        )
        assert (
            db.scalar(select(AuditLog).where(AuditLog.action == "grading.question.assign"))
            is not None
        )
    finally:
        app.dependency_overrides.pop(get_current_actor, None)
        app.dependency_overrides.pop(get_storage, None)
        db.close()
