import io
import uuid
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.grading.providers import GradeSuggestion
from app.main import app
from app.models import (
    Assignment,
    AssignmentStatus,
    AuditLog,
    ClassStudent,
    GradingCriterionResult,
    GradingEvidence,
    GradingJob,
    GradingResult,
    MembershipStatus,
    PaperVersion,
    Question,
    QuestionRecognitionEvidence,
    RubricCriterion,
    StructuredRubricSetItem,
    StructuredRubricVersion,
    Student,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionRecognitionBlock,
    SubmissionScoreSnapshot,
    TeacherReview,
    VersionStatus,
    now_utc,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structured_rubric_support import activate_structured_rubric_set
from test_assignments import FakeStorage, active_class, actor_and_db, create

client = TestClient(app)


def png(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (240, 160), color).save(output, "PNG")
    return output.getvalue()


def four_page_pdf() -> bytes:
    output = io.BytesIO()
    pages = [Image.new("RGB", (240, 160), color) for color in ("white", "ivory", "snow", "beige")]
    pages[0].save(output, "PDF", save_all=True, append_images=pages[1:])
    return output.getvalue()


def workflow(
    *, criterion_validation_mode: str = "manual_only"
) -> tuple[Session, FakeStorage, str, uuid.UUID, str]:
    actor, db = actor_and_db()
    school_class = active_class(db, actor.id)
    student = Student(owner_id=actor.id, student_number="0001", name="合成学生")
    db.add(student)
    db.flush()
    db.add(
        ClassStudent(
            class_id=school_class.id,
            student_id=student.id,
            status=MembershipStatus.active,
            joined_at=now_utc(),
        )
    )
    db.commit()
    storage = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    assignment_id = create(client, school_class.id)["id"]
    client.post(
        f"/api/assignments/{assignment_id}/files",
        files={"file": ("paper.png", png("white"), "image/png")},
    )
    question = client.post(
        f"/api/assignments/{assignment_id}/questions",
        json={
            "question_number": "1",
            "question_type": "single_choice",
            "max_score": 10,
            "content_text": "合成选择题",
        },
    ).json()
    # Downstream workflow fixture: construct the historical published
    # precondition directly. Production HTTP publication now requires a
    # teacher-created central-review readiness snapshot.
    assignment = db.get(Assignment, uuid.UUID(assignment_id))
    assert assignment is not None
    paper = db.get(PaperVersion, assignment.active_paper_version_id)
    question_row = db.get(Question, uuid.UUID(question["id"]))
    assert paper is not None and question_row is not None
    paper.status = VersionStatus.confirmed
    paper.confirmed_at = now_utc()
    activate_structured_rubric_set(
        db,
        assignment,
        [question_row],
        actor_id=assignment.owner_id,
        answers={question_row.id: "1. 测试题"},
        criterion_validation_mode=criterion_validation_mode,
    )
    assignment.status = AssignmentStatus.published
    assignment.published_at = now_utc()
    db.commit()
    batch = client.post(
        f"/api/assignments/{assignment_id}/grading-batches",
        json={"class_id": str(school_class.id)},
    ).json()
    upload = client.post(
        f"/api/grading-batches/{batch['id']}/files",
        files=[
            ("files", ("0001-a.png", png("white"), "image/png")),
            ("files", ("0001-b.png", png("ivory"), "image/png")),
            ("files", ("0001-c.png", png("snow"), "image/png")),
        ],
    )
    assert upload.status_code == 201, upload.text
    submission_id = uuid.UUID(upload.json()["items"][0]["submission_id"])
    return db, storage, batch["id"], submission_id, question["id"]


def confirm_answer_regions(db: Session, submission_id: uuid.UUID) -> None:
    submission = db.get(Submission, submission_id)
    assert submission is not None
    page = db.scalar(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission_id)
        .order_by(SubmissionPage.page_number)
    )
    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
    ).all()
    assert page is not None and answers
    for answer in answers:
        regions = db.scalars(
            select(StudentAnswerRegion).where(StudentAnswerRegion.student_answer_id == answer.id)
        ).all()
        if not regions:
            region = StudentAnswerRegion(
                student_answer_id=answer.id,
                submission_page_id=page.id,
                x=Decimal("0"),
                y=Decimal("0"),
                width=Decimal("1"),
                height=Decimal("1"),
                source="manual",
            )
            db.add(region)
            regions = [region]
        for region in regions:
            region.status = "confirmed"
            region.confirmed_by = submission.owner_id
            region.confirmed_at = now_utc()
    db.commit()


def test_batch_submissions_include_optional_student_identity() -> None:
    db, _storage, batch_id, submission_id, _question_id = workflow()
    try:
        matched = db.get(Submission, submission_id)
        assert matched is not None
        unmatched = Submission(
            owner_id=matched.owner_id,
            grading_batch_id=matched.grading_batch_id,
            assignment_id=matched.assignment_id,
            class_id=matched.class_id,
            student_id=None,
            attempt_number=1,
            status="uploaded",
        )
        db.add(unmatched)
        db.commit()

        response = client.get(f"/api/grading-batches/{batch_id}/submissions")

        assert response.status_code == 200, response.text
        rows = {row["id"]: row for row in response.json()}
        assert rows[str(submission_id)]["student_name"] == "合成学生"
        assert rows[str(submission_id)]["student_number"] == "0001"
        assert rows[str(unmatched.id)]["student_id"] is None
        assert rows[str(unmatched.id)]["student_name"] is None
        assert rows[str(unmatched.id)]["student_number"] is None
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_submission_ocr_worker_is_idempotent_and_writes_answers() -> None:
    db, storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "submission-processing-1"},
        )
        assert processing.status_code == 201, processing.text
        confirm_answer_regions(db, submission_id)
        job = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "submission-ocr-1"},
        )
        assert job.status_code == 201, job.text
        assert job.json()["status"] == "completed"
        first_blocks = db.scalar(select(func.count()).select_from(SubmissionRecognitionBlock))
        assert first_blocks >= 1
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None and answer.recognized_text
        again = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "submission-ocr-1"},
        )
        assert again.json()["id"] == job.json()["id"]
        assert (
            db.scalar(select(func.count()).select_from(SubmissionRecognitionBlock)) == first_blocks
        )
        assert len(storage.objects) >= 12
    finally:
        settings.recognition_provider = previous
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("regions", "CONFIRMED_REGION_MISSING"),
        ("missing_evidence", "CURRENT_RECOGNITION_EVIDENCE_MISSING"),
        ("stale_evidence", "RECOGNITION_EVIDENCE_STALE"),
        ("empty_sources", "CURRENT_RECOGNITION_EVIDENCE_MISSING"),
        ("empty_answer", "EFFECTIVE_ANSWER_MISSING"),
    ],
)
def test_grade_requires_current_answer_evidence_before_creating_job(
    mutation: str, reason: str
) -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        assert (
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
                json={"idempotency_key": f"guard-processing-{mutation}"},
            ).status_code
            == 201
        )
        confirm_answer_regions(db, submission_id)
        assert (
            client.post(
                f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
                json={"idempotency_key": f"guard-recognition-{mutation}"},
            ).status_code
            == 201
        )
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        evidence = db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.student_answer_id == answer.id
            )
        )
        assert evidence is not None
        if mutation == "regions":
            for region in db.scalars(
                select(StudentAnswerRegion).where(
                    StudentAnswerRegion.student_answer_id == answer.id
                )
            ).all():
                db.delete(region)
        elif mutation == "missing_evidence":
            db.delete(evidence)
        elif mutation == "stale_evidence":
            evidence.status = "stale"
            evidence.stale_at = now_utc()
        elif mutation == "empty_sources":
            evidence.block_sources = []
        else:
            answer.recognized_text = None
            answer.recognized_latex = None
            answer.corrected_text = None
            answer.corrected_latex = None
            answer.is_blank = False
        db.commit()
        before = db.scalar(select(func.count()).select_from(GradingJob))
        response = client.post(f"/api/student-answers/{answer.id}/grade")
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "ANSWER_EVIDENCE_REQUIRED"
        assert response.json()["details"]["reason"] == reason
        assert db.scalar(select(func.count()).select_from(GradingJob)) == before
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_blank_answer_with_current_evidence_can_receive_objective_zero() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        assert (
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
                json={"idempotency_key": "blank-processing"},
            ).status_code
            == 201
        )
        confirm_answer_regions(db, submission_id)
        assert (
            client.post(
                f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
                json={"idempotency_key": "blank-recognition"},
            ).status_code
            == 201
        )
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        answer.is_blank = True
        answer.corrected_text = ""
        db.commit()
        response = client.post(f"/api/student-answers/{answer.id}/grade")
        assert response.status_code == 200, response.text
        assert Decimal(response.json()["score"]) == 0
        assert response.json()["evidence_count"] >= 1
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_grading_uses_active_set_version_not_newest_confirmed_rubric() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None
        assignment = db.get(Assignment, submission.assignment_id)
        assert assignment is not None and assignment.active_structured_rubric_set_id is not None
        active_item = db.scalar(
            select(StructuredRubricSetItem).where(
                StructuredRubricSetItem.rubric_set_id == assignment.active_structured_rubric_set_id,
                StructuredRubricSetItem.question_id == uuid.UUID(question_id),
            )
        )
        assert active_item is not None
        active_rubric = db.get(StructuredRubricVersion, active_item.structured_rubric_version_id)
        assert active_rubric is not None
        newer_confirmed = StructuredRubricVersion(
            question_id=active_rubric.question_id,
            question_version=active_rubric.question_version,
            reference_answer_version_id=active_rubric.reference_answer_version_id,
            rubric_version=active_rubric.rubric_version + 1,
            title="newer confirmed rubric outside active set",
            total_points=active_rubric.total_points,
            status="confirmed",
            content_hash="newer-confirmed-not-active".ljust(64, "0"),
            created_by=assignment.owner_id,
            confirmed_by=assignment.owner_id,
            confirmed_at=now_utc(),
        )
        db.add(newer_confirmed)
        db.flush()
        db.add(
            RubricCriterion(
                rubric_version_id=newer_confirmed.id,
                stable_key="newer-answer",
                title="newer criterion",
                max_points=active_rubric.total_points,
                display_order=1,
                criterion_type="answer",
                required=True,
                dependencies=[],
                expected_evidence={"source": "student_answer"},
                validation_mode="manual_only",
                manual_review_policy={},
                partial_credit_policy={},
                validation_rule={},
                metadata_={"fixture": "not-active"},
            )
        )
        db.commit()

        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "active-set-fixed-version-processing"},
        )
        assert processing.status_code == 201, processing.text
        confirm_answer_regions(db, submission_id)
        recognition = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "active-set-fixed-version-recognition"},
        )
        assert recognition.status_code == 201, recognition.text
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        graded = client.post(f"/api/student-answers/{answer.id}/grade")
        assert graded.status_code == 200, graded.text
        result = db.scalar(
            select(GradingResult)
            .where(GradingResult.student_answer_id == answer.id)
            .order_by(GradingResult.created_at.desc())
        )
        assert result is not None
        assert result.structured_rubric_set_id == assignment.active_structured_rubric_set_id
        assert result.structured_rubric_version_id == active_rubric.id
        assert result.structured_rubric_version_id != newer_confirmed.id
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_accept_requires_current_grading_evidence_but_manual_score_does_not() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        assert (
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
                json={"idempotency_key": "review-processing"},
            ).status_code
            == 201
        )
        confirm_answer_regions(db, submission_id)
        assert (
            client.post(
                f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
                json={"idempotency_key": "review-recognition"},
            ).status_code
            == 201
        )
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        graded = client.post(f"/api/student-answers/{answer.id}/grade")
        assert graded.status_code == 200, graded.text
        result = db.scalar(
            select(GradingResult)
            .where(GradingResult.student_answer_id == answer.id)
            .order_by(GradingResult.created_at.desc())
        )
        assert result is not None
        for evidence in db.scalars(
            select(GradingEvidence).where(GradingEvidence.grading_result_id == result.id)
        ).all():
            db.delete(evidence)
        db.commit()
        accepted = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={"decision": "accepted"},
        )
        assert accepted.status_code == 409
        assert accepted.json()["code"] == "GRADING_EVIDENCE_REQUIRED"
        assert (
            db.scalar(select(TeacherReview).where(TeacherReview.student_answer_id == answer.id))
            is None
        )
        modified = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={"decision": "modified", "final_score": 7},
        )
        assert modified.status_code == 409
        assert modified.json()["code"] == "GRADING_EVIDENCE_REQUIRED"
        manual = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={"decision": "manual_scored", "final_score": 7},
        )
        assert manual.status_code == 200, manual.text
        review = db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
        )
        assert review is not None and review.final_score == 7
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_student_number_filename_separators_and_manual_pdf_confirmation_keep_all_pages() -> None:
    db, _storage, batch_id, submission_id, _question_id = workflow()
    try:
        for index, filename in enumerate(
            ("0001_answer.png", "0001-answer.png", "0001 answer.png", "（0001）答卷.png")
        ):
            response = client.post(
                f"/api/grading-batches/{batch_id}/files",
                files=[
                    (
                        "files",
                        (filename, png(("red", "green", "blue", "black")[index]), "image/png"),
                    )
                ],
            )
            assert response.status_code == 201, response.text
            assert response.json()["items"][0]["status"] == "confirmed"

        upload = client.post(
            f"/api/grading-batches/{batch_id}/files",
            files=[("files", ("unknown.pdf", four_page_pdf(), "application/pdf"))],
        )
        assert upload.status_code == 201, upload.text
        item = upload.json()["items"][0]
        assert item["status"] == "pending"
        student_id = db.scalar(select(Student.id).where(Student.student_number == "0001"))
        confirmation = client.post(
            f"/api/grading-batches/{batch_id}/matches/{item['match_id']}/confirm",
            json={"student_id": str(student_id)},
        )
        assert confirmation.status_code == 200, confirmation.text
        stored_file_id = uuid.UUID(item["file_id"])
        pages = db.scalars(
            select(SubmissionPage)
            .where(
                SubmissionPage.submission_id == submission_id,
                SubmissionPage.stored_file_id == stored_file_id,
            )
            .order_by(SubmissionPage.page_number)
        ).all()
        assert [page.source_page_number for page in pages] == [1, 2, 3, 4]
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_finalized_workflow_prioritizes_complete_snapshot_over_pending_pages() -> None:
    db, _storage, batch_id, submission_id, _question_id = workflow()
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None and submission.student_id is not None
        assignment = db.get(Assignment, submission.assignment_id)
        assert assignment is not None
        assert assignment.active_paper_version_id is not None
        assert assignment.active_structured_rubric_set_id is not None
        assert db.scalar(
            select(func.count())
            .select_from(SubmissionPage)
            .where(
                SubmissionPage.submission_id == submission.id,
                SubmissionPage.status == "ready",
            )
        )
        db.add(
            SubmissionScoreSnapshot(
                submission_id=submission.id,
                assignment_id=assignment.id,
                student_id=submission.student_id,
                paper_version_id=assignment.active_paper_version_id,
                structured_rubric_set_id=assignment.active_structured_rubric_set_id,
                total_score=Decimal("10"),
                max_score=Decimal("10"),
                status="complete",
                generated_by=submission.owner_id,
                version=1,
                details=[],
            )
        )
        submission.status = "finalized"
        submission.finalized_at = now_utc()
        db.commit()

        batch = client.get(f"/api/grading-batches/{batch_id}")
        assert batch.status_code == 200, batch.text
        assert batch.json()["workflow"] == {
            "stage_counts": {"completed": 1},
            "blocked": [],
            "completed_count": 1,
            "blocked_count": 0,
        }
        submissions = client.get(f"/api/grading-batches/{batch_id}/submissions")
        assert submissions.status_code == 200, submissions.text
        assert submissions.json()[0]["workflow"] == {
            "stage": "completed",
            "stage_label": "批改完成",
            "reason_code": None,
            "reason": "可以检查结果。",
            "action": "检查结果",
        }
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_page_reorder_split_merge_and_finalize_guard() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    try:
        pages = db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        ).all()
        order = [str(page.id) for page in reversed(pages)]
        assert (
            client.put(
                f"/api/submissions/{submission_id}/pages/order", json={"page_ids": order}
            ).status_code
            == 200
        )
        split = client.post(
            f"/api/submissions/{submission_id}/split", json={"page_ids": [order[-1]]}
        )
        assert split.status_code == 201, split.text
        new_id = split.json()["new_submission_id"]
        source = db.get(Submission, submission_id)
        split_attempt = db.get(Submission, uuid.UUID(new_id))
        assert source is not None and split_attempt is not None
        assert split_attempt.student_id == source.student_id
        assert split_attempt.attempt_number == source.attempt_number + 1
        merged = client.post(
            f"/api/submissions/{submission_id}/merge",
            json={"source_submission_id": new_id},
        )
        assert merged.status_code == 200 and merged.json()["page_count"] == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubmissionPage)
                .where(SubmissionPage.submission_id == submission_id)
            )
            == 3
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_recognized_page_reorder_is_noop_only_when_order_is_unchanged() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        first_question = db.get(Question, uuid.UUID(question_id))
        assert first_question is not None
        db.add(
            Question(
                id=uuid.UUID("f0000000-0000-4000-8000-000000000002"),
                paper_version_id=first_question.paper_version_id,
                question_number="2",
                display_order=first_question.display_order + 1,
                question_type="short_answer",
                content_text="题目 2",
                max_score=10,
            )
        )
        db.commit()
        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "reorder-noop-processing"},
        )
        assert processing.status_code == 201, processing.text
        confirm_answer_regions(db, submission_id)
        recognition = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "reorder-noop-recognition"},
        )
        assert recognition.status_code == 201, recognition.text

        pages = db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        ).all()
        answers = db.scalars(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        ).all()
        evidence = db.scalars(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.submission_id == submission_id
            )
        ).all()
        assert pages and len(answers) == 2 and len(evidence) == 2
        assert all(row.stale_at is None for row in evidence)
        page_state = [(page.id, page.page_number, page.page_version) for page in pages]
        answer_state = [(answer.id, answer.status, answer.requires_review) for answer in answers]
        evidence_state = [
            (row.id, row.status, row.requires_review, row.stale_at) for row in evidence
        ]
        reorder_audit_count = db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.resource_type == "submission",
                AuditLog.resource_id == str(submission_id),
                AuditLog.action == "submission.pages.reorder",
            )
        )
        order = [str(page.id) for page in pages]

        noop = client.put(f"/api/submissions/{submission_id}/pages/order", json={"page_ids": order})
        assert noop.status_code == 200, noop.text
        assert noop.json() == {"submission_id": str(submission_id), "page_ids": order}
        db.expire_all()
        assert [
            (page.id, page.page_number, page.page_version)
            for page in db.scalars(
                select(SubmissionPage)
                .where(SubmissionPage.submission_id == submission_id)
                .order_by(SubmissionPage.page_number)
            )
        ] == page_state
        assert [
            (answer.id, answer.status, answer.requires_review)
            for answer in db.scalars(
                select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
            )
        ] == answer_state
        assert [
            (row.id, row.status, row.requires_review, row.stale_at)
            for row in db.scalars(
                select(QuestionRecognitionEvidence)
                .where(QuestionRecognitionEvidence.submission_id == submission_id)
                .order_by(QuestionRecognitionEvidence.id)
            )
        ] == sorted(evidence_state)
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.resource_type == "submission",
                    AuditLog.resource_id == str(submission_id),
                    AuditLog.action == "submission.pages.reorder",
                )
            )
            == reorder_audit_count
        )

        reordered = client.put(
            f"/api/submissions/{submission_id}/pages/order",
            json={"page_ids": list(reversed(order))},
        )
        assert reordered.status_code == 200, reordered.text
        db.expire_all()
        reordered_pages = db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        ).all()
        original_versions = {page_id: version for page_id, _number, version in page_state}
        assert [str(page.id) for page in reordered_pages] == list(reversed(order))
        assert all(page.page_version == original_versions[page.id] + 1 for page in reordered_pages)
        assert all(
            answer.status == "stale" and answer.requires_review
            for answer in db.scalars(
                select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
            )
        )
        assert all(
            row.status == "stale" and row.requires_review and row.stale_at is not None
            for row in db.scalars(
                select(QuestionRecognitionEvidence).where(
                    QuestionRecognitionEvidence.submission_id == submission_id
                )
            )
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.resource_type == "submission",
                    AuditLog.resource_id == str(submission_id),
                    AuditLog.action == "submission.pages.reorder",
                )
            )
            == reorder_audit_count + 1
        )
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_criteria_evidence_bulk_eligibility_and_consistency() -> None:
    db, _storage, batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "grade-processing"},
        )
        assert processing.status_code == 201, processing.text
        confirm_answer_regions(db, submission_id)
        recognition = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "grade-ocr"},
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
        assert grade.json()["criterion_count"] == 1 and grade.json()["evidence_count"] == 1
        assert db.scalar(select(func.count()).select_from(GradingCriterionResult)) == 1
        assert db.scalar(select(func.count()).select_from(GradingEvidence)) == 1
        eligibility = client.get(f"/api/grading-batches/{batch_id}/bulk-accept-eligibility").json()
        assert eligibility["eligible_count"] == 1
        accepted = client.post(
            f"/api/grading-batches/{batch_id}/bulk-accept", json={"answer_ids": [str(answer.id)]}
        )
        assert accepted.status_code == 200 and accepted.json()["accepted_answer_ids"] == [
            str(answer.id)
        ]
        consistency = client.get(
            f"/api/grading-batches/{batch_id}/questions/{answer.question_id}/consistency"
        )
        assert consistency.status_code == 200 and consistency.json()["total"] == 1
    finally:
        settings.recognition_provider = previous
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_subjective_boundary_recheck_disagreement_enters_review_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _storage, batch_id, submission_id, question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"

    class SequenceProvider:
        name = "quality-test"
        version = "v1"
        is_demo = True

        def __init__(self) -> None:
            self.calls = 0

        def grade(
            self, answer: str, max_score: Decimal, context: dict | None = None
        ) -> GradeSuggestion:
            del answer
            self.calls += 1
            assert context is not None
            criterion_id = context["rubric_criteria"][0]["id"]
            evidence_id = context["evidence_regions"][0]["id"]
            score = max_score if self.calls == 1 else max_score - Decimal("1")
            return GradeSuggestion(
                score=score,
                confidence=Decimal("0.99"),
                summary="边界答案复核",
                criterion_scores={criterion_id: score},
                criterion_reasons={criterion_id: "答案步骤支持该得分。"},
                criterion_evidence_refs={criterion_id: [evidence_id]},
            )

    provider = SequenceProvider()
    monkeypatch.setattr("app.api.grading.provider_from_settings", lambda _settings: provider)
    try:
        question = db.get(Question, uuid.UUID(question_id))
        assert question is not None
        question.question_type = "short_answer"
        db.commit()
        assert (
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
                json={"idempotency_key": "boundary-processing"},
            ).status_code
            == 201
        )
        confirm_answer_regions(db, submission_id)
        assert (
            client.post(
                f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
                json={"idempotency_key": "boundary-recognition"},
            ).status_code
            == 201
        )
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        answer.requires_review = False
        answer.status = "ready_for_grading"
        db.commit()

        graded = client.post(f"/api/student-answers/{answer.id}/grade")

        assert graded.status_code == 200, graded.text
        assert provider.calls == 2
        assert graded.json()["quality_flags"] == ["BOUNDARY_RECHECK_DISAGREEMENT"]
        assert graded.json()["requires_review"] is True
        workspace = client.get(f"/api/grading-batches/{batch_id}/review-workspace").json()
        projected = workspace["items"][0]["answers"][0]
        assert projected["result"]["quality_flags"] == ["BOUNDARY_RECHECK_DISAGREEMENT"]
        assert projected["criteria"][0]["title"] == "答案正确"
        assert projected["criteria"][0]["evidence_quotes"]
    finally:
        settings.recognition_provider = previous
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_current_result_status_projection_preserves_teacher_review_gate() -> None:
    db, _storage, batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "manual-review-projection-processing"},
        )
        assert processing.status_code == 201, processing.text
        confirm_answer_regions(db, submission_id)
        recognition = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "manual-review-projection-recognition"},
        )
        assert recognition.status_code == 201, recognition.text
        for page in db.scalars(
            select(SubmissionPage).where(SubmissionPage.submission_id == submission_id)
        ):
            page.status = "recognized"
        db.commit()

        submission = db.get(Submission, submission_id)
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert submission is not None and answer is not None
        assignment = db.get(Assignment, submission.assignment_id)
        question = db.get(Question, answer.question_id)
        assert (
            assignment is not None
            and assignment.active_structured_rubric_set_id is not None
            and question is not None
        )
        set_item = db.scalar(
            select(StructuredRubricSetItem).where(
                StructuredRubricSetItem.rubric_set_id == assignment.active_structured_rubric_set_id,
                StructuredRubricSetItem.question_id == question.id,
            )
        )
        assert set_item is not None
        criterion = db.scalar(
            select(RubricCriterion).where(
                RubricCriterion.rubric_version_id == set_item.structured_rubric_version_id
            )
        )
        assert criterion is not None
        job = GradingJob(
            owner_id=submission.owner_id,
            grading_batch_id=submission.grading_batch_id,
            submission_id=submission.id,
            question_id=question.id,
            structured_rubric_set_id=assignment.active_structured_rubric_set_id,
            structured_rubric_version_id=set_item.structured_rubric_version_id,
            status="completed",
            provider="codex_local",
            provider_version="local",
            prompt_version="codex-local-test",
            config_version="test",
            idempotency_key="manual-review-projection-result",
            started_at=now_utc(),
            completed_at=now_utc(),
        )
        db.add(job)
        db.flush()
        result = GradingResult(
            grading_job_id=job.id,
            student_answer_id=answer.id,
            question_id=question.id,
            structured_rubric_set_id=assignment.active_structured_rubric_set_id,
            structured_rubric_version_id=set_item.structured_rubric_version_id,
            grading_method="codex_assisted",
            provider="codex_local",
            provider_version="local",
            prompt_version="codex-local-test",
            score=None,
            max_score=question.max_score,
            confidence=None,
            recognized_answer_snapshot=answer.recognized_text,
            reasoning_summary="该题需要教师人工判断。",
            requires_review=True,
            status="suggested",
        )
        db.add(result)
        db.flush()
        db.add(
            GradingCriterionResult(
                grading_result_id=result.id,
                criterion_id=criterion.id,
                status="manual_review",
                awarded_points=None,
                max_points=criterion.max_points,
                reason="缺少足够信息，不能自动给分。",
                confidence=None,
            )
        )
        db.commit()

        detail = client.get(f"/api/grading-batches/{batch_id}")
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["graded_count"] == 0
        assert payload["reviewed_count"] == 0
        assert payload["workflow"]["stage_counts"] == {"teacher_review": 1}
        assert payload["workflow"]["blocked"][0]["reason_code"] == "TEACHER_REVIEW_REQUIRED"
        assert all(
            item["reason_code"] != "GRADING_RESULT_REQUIRED"
            for item in payload["workflow"]["blocked"]
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TeacherReview)
                .where(TeacherReview.student_answer_id == answer.id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubmissionScoreSnapshot)
                .where(
                    SubmissionScoreSnapshot.submission_id == submission.id,
                    SubmissionScoreSnapshot.status == "complete",
                )
            )
            == 0
        )

        reviewed = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={"decision": "manual_scored", "final_score": 7},
        )
        assert reviewed.status_code == 200, reviewed.text
        db.refresh(result)
        db.refresh(answer)
        assert result.status == "modified"
        assert answer.requires_review is False
        completed = client.get(f"/api/grading-batches/{batch_id}").json()
        assert completed["graded_count"] == 1
        assert completed["reviewed_count"] == 1
        assert completed["workflow"]["stage_counts"] == {"completed": 1}
        assert completed["workflow"]["blocked"] == []

        for invalid_status in ("stale", "superseded"):
            result.status = invalid_status
            db.commit()
            invalid = client.get(f"/api/grading-batches/{batch_id}").json()
            assert invalid["workflow"]["stage_counts"] == {"grading": 1}
            assert invalid["workflow"]["blocked"][0]["reason_code"] == "GRADING_RESULT_REQUIRED"
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_bulk_accept_requires_complete_current_answer_and_grading_evidence() -> None:
    db, _storage, batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        assert (
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
                json={"idempotency_key": "bulk-guard-processing"},
            ).status_code
            == 201
        )
        confirm_answer_regions(db, submission_id)
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        pages = db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission_id)
            .order_by(SubmissionPage.page_number)
        ).all()
        assert answer is not None and len(pages) >= 2
        db.add(
            StudentAnswerRegion(
                student_answer_id=answer.id,
                submission_page_id=pages[1].id,
                x=Decimal("0"),
                y=Decimal("0"),
                width=Decimal("1"),
                height=Decimal("1"),
                source="manual",
                status="confirmed",
                confirmed_by=db.get(Submission, submission_id).owner_id,
                confirmed_at=now_utc(),
            )
        )
        db.commit()
        assert (
            client.post(
                f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
                json={"idempotency_key": "bulk-guard-recognition"},
            ).status_code
            == 201
        )
        answer.requires_review = False
        db.commit()
        graded = client.post(f"/api/student-answers/{answer.id}/grade")
        assert graded.status_code == 200, graded.text
        result = db.scalar(
            select(GradingResult).where(GradingResult.student_answer_id == answer.id)
        )
        grading_evidence = db.scalars(
            select(GradingEvidence).where(GradingEvidence.grading_result_id == result.id)
        ).all()
        assert result is not None and len(grading_evidence) == 2

        grading_evidence[0].x = Decimal("0.1")
        db.commit()
        eligibility = client.get(f"/api/grading-batches/{batch_id}/bulk-accept-eligibility").json()
        item = next(item for item in eligibility["items"] if item["answer_id"] == str(answer.id))
        assert "GRADING_EVIDENCE_REQUIRED" in item["reasons"]
        rejected = client.post(
            f"/api/grading-batches/{batch_id}/bulk-accept",
            json={"answer_ids": [str(answer.id)]},
        )
        assert rejected.status_code == 200
        assert rejected.json()["accepted_answer_ids"] == []
        assert (
            db.scalar(select(TeacherReview).where(TeacherReview.student_answer_id == answer.id))
            is None
        )

        for row in grading_evidence:
            db.delete(row)
        db.commit()
        zero_evidence = client.post(
            f"/api/grading-batches/{batch_id}/bulk-accept",
            json={"answer_ids": [str(answer.id)]},
        )
        assert "GRADING_EVIDENCE_REQUIRED" in zero_evidence.json()["excluded"][0]["reasons"]

        recognition_evidence = db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.student_answer_id == answer.id
            )
        )
        assert recognition_evidence is not None
        source_block = db.get(
            SubmissionRecognitionBlock,
            uuid.UUID(recognition_evidence.block_sources[0]["block_id"]),
        )
        assert source_block is not None
        source_block.stale_at = now_utc()
        source_block.status = "stale"
        db.commit()
        stale = client.get(f"/api/grading-batches/{batch_id}/bulk-accept-eligibility").json()
        stale_item = next(item for item in stale["items"] if item["answer_id"] == str(answer.id))
        assert "RECOGNITION_EVIDENCE_STALE" in stale_item["reasons"]
        finalized = client.post(f"/api/submissions/{submission_id}/finalize")
        assert finalized.status_code == 200
        assert finalized.json()["status"] == "incomplete"
        assert any(problem["code"] == "REVIEW_REQUIRED" for problem in finalized.json()["problems"])
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubmissionScoreSnapshot)
                .where(
                    SubmissionScoreSnapshot.submission_id == submission_id,
                    SubmissionScoreSnapshot.status == "complete",
                )
            )
            == 0
        )
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_codex_suggestion_requires_current_recognition_evidence() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous_recognition = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        assert (
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
                json={"idempotency_key": "codex-guard-processing"},
            ).status_code
            == 201
        )
        confirm_answer_regions(db, submission_id)
        assert (
            client.post(
                f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
                json={"idempotency_key": "codex-guard-recognition"},
            ).status_code
            == 201
        )
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        graded = client.post(f"/api/student-answers/{answer.id}/grade")
        assert graded.status_code == 200, graded.text
        result = db.scalar(
            select(GradingResult).where(GradingResult.student_answer_id == answer.id)
        )
        criterion_id = db.scalar(
            select(GradingCriterionResult.criterion_id).where(
                GradingCriterionResult.grading_result_id == result.id
            )
        )
        recognition_evidence = db.scalar(
            select(QuestionRecognitionEvidence).where(
                QuestionRecognitionEvidence.student_answer_id == answer.id
            )
        )
        assert result is not None and criterion_id is not None and recognition_evidence is not None
        block = db.get(
            SubmissionRecognitionBlock,
            uuid.UUID(recognition_evidence.block_sources[0]["block_id"]),
        )
        assert block is not None
        block.stale_at = now_utc()
        block.status = "stale"
        db.commit()
        before = (
            db.scalar(select(func.count()).select_from(GradingJob)),
            db.scalar(select(func.count()).select_from(GradingResult)),
        )
        suggestion = client.put(
            f"/api/student-answers/{answer.id}/codex-suggestion",
            json={
                "score": 8,
                "reasoning": "不得基于旧识别证据写入建议",
                "criterion_scores": {str(criterion_id): 8},
            },
        )
        assert suggestion.status_code == 410
        assert suggestion.json()["code"] == "CODEX_LOCAL_INTERNAL_ONLY"
        after = (
            db.scalar(select(func.count()).select_from(GradingJob)),
            db.scalar(select(func.count()).select_from(GradingResult)),
        )
        assert after == before
    finally:
        settings.recognition_provider = previous_recognition
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_modified_without_result_requires_manual_scored_decision() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        assert (
            client.post(
                f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
                json={"idempotency_key": "modified-without-result-processing"},
            ).status_code
            == 201
        )
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        modified = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={"decision": "modified", "final_score": 7},
        )
        assert modified.status_code == 409
        assert modified.json()["code"] == "GRADING_RESULT_REQUIRED"
        assert (
            db.scalar(select(TeacherReview).where(TeacherReview.student_answer_id == answer.id))
            is None
        )
        manual = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={"decision": "manual_scored", "final_score": 7},
        )
        assert manual.status_code == 200, manual.text
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_finalized_submission_rejects_answer_and_review_mutations() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None
        answer = StudentAnswer(
            submission_id=submission.id,
            question_id=uuid.UUID(question_id),
            question_version_reference="finalized-mutation-guard-v1",
            status="manually_entered",
            recognized_text="original recognized answer",
            corrected_text="original corrected answer",
            requires_review=False,
        )
        db.add(answer)
        db.flush()
        review = TeacherReview(
            student_answer_id=answer.id,
            reviewer_id=submission.owner_id,
            decision="manual_scored",
            final_score=Decimal("7"),
            final_feedback="original feedback",
            review_notes="original notes",
            confirmed_at=now_utc(),
        )
        db.add(review)
        page = db.scalar(
            select(SubmissionPage).where(SubmissionPage.submission_id == submission.id)
        )
        assert page is not None
        region = StudentAnswerRegion(
            student_answer_id=answer.id,
            submission_page_id=page.id,
            x=Decimal("0"),
            y=Decimal("0"),
            width=Decimal("1"),
            height=Decimal("1"),
            source="manual",
            status="confirmed",
            confirmed_by=submission.owner_id,
            confirmed_at=now_utc(),
        )
        db.add(region)
        submission.status = "finalized"
        submission.finalized_at = now_utc()
        db.commit()

        patched = client.patch(
            f"/api/student-answers/{answer.id}",
            json={
                "corrected_text": "mutated corrected answer",
                "corrected_latex": "x=2",
            },
        )
        assert patched.status_code == 409
        assert patched.json()["code"] == "SUBMISSION_FINALIZED"

        reviewed = client.put(
            f"/api/student-answers/{answer.id}/review",
            json={
                "decision": "manual_scored",
                "final_score": 3,
                "final_feedback": "mutated feedback",
                "review_notes": "mutated notes",
            },
        )
        assert reviewed.status_code == 409
        assert reviewed.json()["code"] == "SUBMISSION_FINALIZED"

        created_region = client.post(
            f"/api/student-answers/{answer.id}/regions",
            json={
                "submission_page_id": str(page.id),
                "x": 0,
                "y": 0,
                "width": 1,
                "height": 1,
                "source": "manual",
                "confirmed": True,
            },
        )
        assert created_region.status_code == 409
        assert created_region.json()["code"] == "SUBMISSION_FINALIZED"

        deleted_region = client.delete(f"/api/student-answers/{answer.id}/regions/{region.id}")
        assert deleted_region.status_code == 409
        assert deleted_region.json()["code"] == "SUBMISSION_FINALIZED"

        finalized_again = client.post(f"/api/submissions/{submission.id}/finalize")
        assert finalized_again.status_code == 409
        assert finalized_again.json()["code"] == "SUBMISSION_FINALIZED"

        created_answer = client.post(
            f"/api/submissions/{submission.id}/answers",
            json={
                "question_id": question_id,
                "recognized_text": "must not be added after finalization",
            },
        )
        assert created_answer.status_code == 409
        assert created_answer.json()["code"] == "SUBMISSION_FINALIZED"

        db.expire_all()
        unchanged_answer = db.get(StudentAnswer, answer.id)
        unchanged_review = db.get(TeacherReview, review.id)
        assert unchanged_answer is not None
        assert unchanged_answer.corrected_text == "original corrected answer"
        assert unchanged_answer.corrected_latex is None
        assert unchanged_answer.status == "manually_entered"
        assert unchanged_answer.requires_review is False
        assert unchanged_review is not None
        assert unchanged_review.decision == "manual_scored"
        assert unchanged_review.final_score == Decimal("7")
        assert unchanged_review.final_feedback == "original feedback"
        assert unchanged_review.review_notes == "original notes"
        assert db.get(StudentAnswerRegion, region.id) is not None
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_voided_submission_rejects_new_answer() -> None:
    db, _storage, _batch_id, submission_id, question_id = workflow()
    try:
        submission = db.get(Submission, submission_id)
        assert submission is not None
        submission.status = "voided"
        db.commit()

        response = client.post(
            f"/api/submissions/{submission.id}/answers",
            json={"question_id": question_id, "recognized_text": "must not be added"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "SUBMISSION_VOIDED"
    finally:
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_reopen_submission_rejects_blank_reason_before_mutation() -> None:
    response = client.post(
        f"/api/submissions/{uuid.uuid4()}/reopen",
        json={"reason": "   "},
    )
    assert response.status_code == 422


def test_local_codex_suggestion_is_review_only_and_supersedes_previous_result() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    previous_answer = settings.answer_recognition_provider
    settings.recognition_provider = "fake"
    settings.answer_recognition_provider = "fake"
    try:
        processing = client.post(
            f"/api/submissions/{submission_id}/processing-jobs?run_now=true",
            json={"idempotency_key": "codex-processing"},
        )
        assert processing.status_code == 201, processing.text
        confirm_answer_regions(db, submission_id)
        recognition = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "codex-ocr"},
        )
        assert recognition.status_code == 201, recognition.text
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None
        answer.requires_review = False
        answer.status = "ready_for_grading"
        db.commit()
        graded = client.post(f"/api/student-answers/{answer.id}/grade")
        assert graded.status_code == 200, graded.text
        result = db.scalar(
            select(GradingResult).where(GradingResult.student_answer_id == answer.id)
        )
        assert result is not None
        criterion_id = db.scalar(
            select(GradingCriterionResult.criterion_id).where(
                GradingCriterionResult.grading_result_id == result.id
            )
        )
        assert criterion_id is not None
        before = (
            db.scalar(select(func.count()).select_from(GradingJob)),
            db.scalar(select(func.count()).select_from(GradingResult)),
        )
        suggestion = client.put(
            f"/api/student-answers/{answer.id}/codex-suggestion",
            json={
                "score": 8,
                "reasoning": "本地建议：答案证据完整，但结论表述不够严谨。",
                "criterion_scores": {str(criterion_id): 8},
            },
        )
        assert suggestion.status_code == 410, suggestion.text
        assert suggestion.json()["code"] == "CODEX_LOCAL_INTERNAL_ONLY"
        assert (
            db.scalar(select(TeacherReview).where(TeacherReview.student_answer_id == answer.id))
            is None
        )
        assert before == (
            db.scalar(select(func.count()).select_from(GradingJob)),
            db.scalar(select(func.count()).select_from(GradingResult)),
        )
    finally:
        settings.recognition_provider = previous
        settings.answer_recognition_provider = previous_answer
        app.dependency_overrides.pop(get_storage, None)
        db.close()
