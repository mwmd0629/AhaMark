import io
import uuid

from app.core.config import get_settings
from app.main import app
from app.models import (
    Assignment,
    AssignmentStatus,
    ClassStudent,
    GradingCriterionResult,
    GradingEvidence,
    GradingResult,
    MembershipStatus,
    PaperVersion,
    RubricVersion,
    Student,
    StudentAnswer,
    SubmissionPage,
    SubmissionRecognitionBlock,
    TeacherReview,
    VersionStatus,
    now_utc,
)
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
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


def workflow() -> tuple[object, FakeStorage, str, str, str]:
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
    client.put(
        f"/api/assignments/{assignment_id}/rubrics/{question['id']}",
        json={"standard_answer": "1. 测试题", "items": [{"title": "答案正确", "points": 10}]},
    )
    # Downstream workflow fixture: construct the historical published
    # precondition directly. Production HTTP publication now requires a
    # teacher-created central-review readiness snapshot.
    assignment = db.get(Assignment, uuid.UUID(assignment_id))
    assert assignment is not None
    paper = db.get(PaperVersion, assignment.active_paper_version_id)
    rubric = db.get(RubricVersion, assignment.active_rubric_version_id)
    assert paper is not None and rubric is not None
    paper.status = VersionStatus.confirmed
    paper.confirmed_at = now_utc()
    rubric.status = VersionStatus.confirmed
    rubric.confirmed_at = now_utc()
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


def test_submission_ocr_worker_is_idempotent_and_writes_answers() -> None:
    db, storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        job = client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "submission-ocr-1"},
        )
        assert job.status_code == 201, job.text
        assert job.json()["status"] == "completed"
        first_blocks = db.scalar(select(func.count()).select_from(SubmissionRecognitionBlock))
        assert first_blocks == 3
        answer = db.scalar(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission_id)
        )
        assert answer is not None and answer.recognized_text == "1. 测试题"
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


def test_criteria_evidence_bulk_eligibility_and_consistency() -> None:
    db, _storage, batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "grade-ocr"},
        )
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
        app.dependency_overrides.pop(get_storage, None)
        db.close()


def test_local_codex_suggestion_is_review_only_and_supersedes_previous_result() -> None:
    db, _storage, _batch_id, submission_id, _question_id = workflow()
    settings = get_settings()
    previous = settings.recognition_provider
    settings.recognition_provider = "fake"
    try:
        client.post(
            f"/api/submissions/{submission_id}/recognition-jobs?run_now=true",
            json={"idempotency_key": "codex-ocr"},
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
        result = db.scalar(
            select(GradingResult).where(GradingResult.student_answer_id == answer.id)
        )
        assert result is not None
        criterion_id = db.scalar(
            select(GradingCriterionResult.rubric_item_id).where(
                GradingCriterionResult.grading_result_id == result.id
            )
        )
        assert criterion_id is not None
        suggestion = client.put(
            f"/api/student-answers/{answer.id}/codex-suggestion",
            json={
                "score": 8,
                "reasoning": "本地建议：答案证据完整，但结论表述不够严谨。",
                "criterion_scores": {str(criterion_id): 8},
            },
        )
        assert suggestion.status_code == 200, suggestion.text
        assert suggestion.json()["provider"] == "codex-assisted"
        assert suggestion.json()["status"] == "suggested"
        assert db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
        ) is None
        latest = db.scalar(
            select(GradingResult)
            .where(GradingResult.student_answer_id == answer.id)
            .order_by(GradingResult.created_at.desc())
        )
        assert latest is not None and latest.score == 8 and latest.status == "suggested"
    finally:
        settings.recognition_provider = previous
        app.dependency_overrides.pop(get_storage, None)
        db.close()
