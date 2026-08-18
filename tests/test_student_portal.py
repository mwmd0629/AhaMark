import io
import uuid
import zipfile
from datetime import timedelta
from decimal import Decimal

import pytest
from app.api.actor import CurrentActor
from app.api.domain import ApiProblem
from app.api.student_portal import (
    StudentAccountInput,
    StudentSubmissionInput,
    TeacherReviewAdditionalInformationInput,
    TeacherReviewDecisionInput,
    TeacherReviewRequestInput,
    TeachingResourceInput,
    WrongQuestionMessageInput,
    add_teacher_review_information,
    create_student_account_link,
    create_teaching_resource,
    create_wrong_question_message,
    create_wrong_question_thread,
    decide_teacher_review_request,
    delete_teaching_resource,
    list_student_resources,
    list_teacher_wrong_questions,
    publish_teaching_resource,
    revoke_student_account_link,
    student_results,
    student_wrong_questions,
    submit_student_assignment,
    submit_teacher_review_request,
)
from app.db.session import SessionLocal
from app.models import (
    Assignment,
    AssignmentClass,
    AssignmentStatus,
    ClassStudent,
    FileStatus,
    GradeRelease,
    GradeReleaseItem,
    MembershipStatus,
    PaperVersion,
    Question,
    SchoolClass,
    ScoreRevision,
    StoredFile,
    Student,
    StudentAccountLink,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeachingResource,
    User,
    VersionStatus,
    WrongQuestionAIJob,
    now_utc,
)
from app.security.files import UnsafeFile, inspect_upload
from app.storage.base import ObjectMetadata
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session


class MemoryStorage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        pass

    def put(self, key: str, data: io.BytesIO, size: int, content_type: str) -> ObjectMetadata:
        self.data[key] = data.read()
        return ObjectMetadata(key, size, content_type)

    def get(self, key: str) -> io.BytesIO:
        return io.BytesIO(self.data[key])

    def stat(self, key: str) -> ObjectMetadata:
        return ObjectMetadata(key, len(self.data[key]), "image/png")

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return f"signed://{key}?expires={expires_seconds}"


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="PNG")
    return output.getvalue()


def pptx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/'
            'presentationml/2006/main" />',
        )
    return output.getvalue()


def test_teaching_resource_pptx_requires_explicit_safe_office_mode() -> None:
    content = pptx_bytes()
    with pytest.raises(UnsafeFile):
        inspect_upload(
            "lecture.pptx",
            content,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            max_pdf_pages=10,
            max_image_pixels=10_000,
        )
    inspection = inspect_upload(
        "lecture.pptx",
        content,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        max_pdf_pages=10,
        max_image_pixels=10_000,
        allow_pptx=True,
    )
    assert inspection.kind == "pptx"


@pytest.mark.parametrize(
    ("part_name", "part_content", "expected_code"),
    [
        (
            "ppt/_rels/presentation.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/'
                'package/2006/relationships"><Relationship Id="rId1" '
                'Type="test" Target="https://example.invalid" '
                'TargetMode = "External" /></Relationships>'
            ),
            "OFFICE_EXTERNAL_LINK_FORBIDDEN",
        ),
        ("ppt/embeddings/object1.bin", "not-a-real-ole-object", "OFFICE_ACTIVE_CONTENT_FORBIDDEN"),
    ],
)
def test_teaching_resource_pptx_rejects_active_content(
    part_name: str,
    part_content: str,
    expected_code: str,
) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/'
            'presentationml/2006/main" />',
        )
        archive.writestr(part_name, part_content)
    with pytest.raises(UnsafeFile) as error:
        inspect_upload(
            "lecture.pptx",
            output.getvalue(),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            max_pdf_pages=10,
            max_image_pixels=10_000,
            allow_pptx=True,
        )
    assert error.value.code == expected_code


def seed_portal(db: Session) -> tuple[User, Student, SchoolClass, Assignment]:
    teacher = User(
        email="portal-teacher@example.com",
        password_hash="!test!",
        display_name="Portal teacher",
        status="active",
    )
    db.add(teacher)
    db.flush()
    student = Student(
        owner_id=teacher.id,
        student_number="S-001",
        name="Portal student",
        email="portal-student@example.com",
    )
    school_class = SchoolClass(owner_id=teacher.id, name="Portal class")
    db.add_all([student, school_class])
    db.flush()
    db.add(
        ClassStudent(
            class_id=school_class.id,
            student_id=student.id,
            status=MembershipStatus.active,
        )
    )
    assignment = Assignment(
        owner_id=teacher.id,
        title="Published assignment",
        status=AssignmentStatus.published,
        due_at=now_utc() + timedelta(days=1),
        published_at=now_utc(),
        total_score=Decimal("10"),
    )
    db.add(assignment)
    db.flush()
    db.add(AssignmentClass(assignment_id=assignment.id, class_id=school_class.id))
    db.commit()
    return teacher, student, school_class, assignment


def test_draft_teaching_resource_is_archived_instead_of_orphaning_its_file() -> None:
    with SessionLocal() as db:
        teacher, _student, school_class, _assignment = seed_portal(db)
        stored = StoredFile(
            owner_id=teacher.id,
            storage_key=f"teaching-resources/{teacher.id}/lecture.pdf",
            original_name="lecture.pdf",
            content_type="application/pdf",
            size=128,
            checksum="f" * 64,
            status=FileStatus.ready,
        )
        db.add(stored)
        db.commit()
        resource = create_teaching_resource(
            TeachingResourceInput(
                class_id=school_class.id,
                resource_type="handout",
                title="Lecture notes",
                stored_file_id=stored.id,
            ),
            db,
            CurrentActor(teacher.id, teacher.email),
        )

        resource_id = uuid.UUID(resource["id"])
        delete_teaching_resource(resource_id, db, CurrentActor(teacher.id, teacher.email))

        archived = db.get(TeachingResource, resource_id)
        assert archived is not None
        assert archived.status == "archived"
        assert archived.archived_at is not None
        assert archived.stored_file_id == stored.id
        retained_file = db.get(StoredFile, stored.id)
        assert retained_file is not None
        assert retained_file.status == FileStatus.ready


def test_student_account_resource_and_submission_boundaries() -> None:
    storage = MemoryStorage()
    with SessionLocal() as db:
        teacher, student, school_class, assignment = seed_portal(db)
        link = create_student_account_link(
            student.id,
            StudentAccountInput(
                email=student.email or "",
                display_name=student.name,
                temporary_password="temporary-pass-123",
            ),
            db,
            CurrentActor(teacher.id, teacher.email),
        )
        student_user = db.get(User, uuid.UUID(link["user_id"]))
        assert student_user is not None
        assert db.scalar(
            select(StudentAccountLink).where(StudentAccountLink.user_id == student_user.id)
        )

        resource = create_teaching_resource(
            TeachingResourceInput(
                class_id=school_class.id,
                assignment_id=assignment.id,
                resource_type="web",
                title="Reference",
                external_url="https://example.invalid/reference",
            ),
            db,
            CurrentActor(teacher.id, teacher.email),
        )
        publish_teaching_resource(
            uuid.UUID(resource["id"]), db, CurrentActor(teacher.id, teacher.email)
        )
        visible = list_student_resources(db, CurrentActor(student_user.id, student_user.email))
        assert visible["total"] == 1

        content = png_bytes()
        file = StoredFile(
            owner_id=student_user.id,
            storage_key=f"student-submissions/{student_user.id}/answer.png",
            original_name="answer.png",
            content_type="image/png",
            size=len(content),
            checksum="a" * 64,
            status=FileStatus.ready,
        )
        db.add(file)
        db.commit()
        storage.data[file.storage_key] = content
        response = submit_student_assignment(
            assignment.id,
            StudentSubmissionInput(
                class_id=school_class.id,
                stored_file_ids=[file.id],
                idempotency_key="student-submit-001",
            ),
            db,
            CurrentActor(student_user.id, student_user.email),
            storage,
        )
        submission = db.get(Submission, uuid.UUID(response["id"]))
        assert submission is not None
        assert submission.owner_id == teacher.id
        assert submission.submitted_by_user_id == student_user.id
        assert submission.source == "student_portal"
        retry_file = StoredFile(
            owner_id=student_user.id,
            storage_key=f"student-submissions/{student_user.id}/retry.png",
            original_name="retry.png",
            content_type="image/png",
            size=len(content),
            checksum="c" * 64,
            status=FileStatus.ready,
        )
        db.add(retry_file)
        db.commit()
        replay = submit_student_assignment(
            assignment.id,
            StudentSubmissionInput(
                class_id=school_class.id,
                stored_file_ids=[retry_file.id],
                idempotency_key="student-submit-001",
            ),
            db,
            CurrentActor(student_user.id, student_user.email),
            storage,
        )
        assert replay["id"] == response["id"]
        assert replay["idempotent_replay"] is True
        assert replay["stored_file_ids"] == [str(file.id)]

        revoke_student_account_link(
            student.id,
            db,
            CurrentActor(teacher.id, teacher.email),
        )
        with pytest.raises(ApiProblem) as revoked_replay:
            submit_student_assignment(
                assignment.id,
                StudentSubmissionInput(
                    class_id=school_class.id,
                    stored_file_ids=[file.id],
                    idempotency_key="student-submit-001",
                ),
                db,
                CurrentActor(student_user.id, student_user.email),
                storage,
            )
        assert revoked_replay.value.code == "STUDENT_CLASS_ACCESS_DENIED"


def test_released_wrong_question_creates_only_an_ai_suggestion_job() -> None:
    storage = MemoryStorage()
    with SessionLocal() as db:
        teacher, student, school_class, assignment = seed_portal(db)
        link = create_student_account_link(
            student.id,
            StudentAccountInput(
                email=student.email or "",
                temporary_password="temporary-pass-123",
            ),
            db,
            CurrentActor(teacher.id, teacher.email),
        )
        student_user = db.get(User, uuid.UUID(link["user_id"]))
        assert student_user is not None
        content = png_bytes()
        file = StoredFile(
            owner_id=student_user.id,
            storage_key=f"student-submissions/{student_user.id}/wrong.png",
            original_name="wrong.png",
            content_type="image/png",
            size=len(content),
            checksum="b" * 64,
            status=FileStatus.ready,
        )
        db.add(file)
        db.commit()
        storage.data[file.storage_key] = content
        submission_data = submit_student_assignment(
            assignment.id,
            StudentSubmissionInput(
                class_id=school_class.id,
                stored_file_ids=[file.id],
                idempotency_key="student-submit-002",
            ),
            db,
            CurrentActor(student_user.id, student_user.email),
            storage,
        )
        submission = db.get(Submission, uuid.UUID(submission_data["id"]))
        assert submission is not None
        submission.status = "finalized"
        submission.finalized_at = now_utc()
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status=VersionStatus.confirmed,
            source_type="manual",
            created_by=teacher.id,
        )
        db.add(paper)
        db.flush()
        question = Question(
            paper_version_id=paper.id,
            question_number="1",
            display_order=1,
            question_type="short_answer",
            content_text="Why?",
            max_score=Decimal("10"),
        )
        db.add(question)
        db.flush()
        answer = StudentAnswer(
            submission_id=submission.id,
            question_id=question.id,
            question_version_reference="v1",
            recognized_text="student answer",
        )
        db.add(answer)
        db.flush()
        snapshot = SubmissionScoreSnapshot(
            submission_id=submission.id,
            assignment_id=assignment.id,
            student_id=student.id,
            paper_version_id=paper.id,
            rubric_version_id=uuid.uuid4(),
            total_score=Decimal("2"),
            max_score=Decimal("10"),
            status="complete",
            generated_by=teacher.id,
            version=1,
            details=[
                {
                    "question_id": str(question.id),
                    "question_number": "1",
                    "question_type": "short_answer",
                    "question_text": "Explain the concept.",
                    "student_answer_id": str(answer.id),
                    "student_answer_text": "A partially correct answer.",
                    "score": "2",
                    "max_score": "10",
                    "final_feedback": "Review the concept.",
                    "final_error_type": "concept",
                }
            ],
        )
        release = GradeRelease(
            owner_id=teacher.id,
            assignment_id=assignment.id,
            class_id=school_class.id,
            version=1,
            status="released",
            release_mode="score_and_feedback",
            released_at=now_utc(),
            created_by=teacher.id,
        )
        db.add_all([snapshot, release])
        db.flush()
        db.add(
            GradeReleaseItem(
                grade_release_id=release.id,
                student_id=student.id,
                submission_id=submission.id,
                score_snapshot_id=snapshot.id,
            )
        )
        db.commit()

        teacher_actor = CurrentActor(teacher.id, teacher.email)
        teacher_wrong = list_teacher_wrong_questions(db, teacher_actor)
        assert teacher_wrong["total"] == 1
        assert teacher_wrong["summary"] == {
            "total_wrong_questions": 1,
            "affected_students": 1,
            "knowledge_point_count": 0,
            "pending_review_count": 0,
        }
        assert teacher_wrong["items"][0] == {
            **teacher_wrong["items"][0],
            "student_name": student.name,
            "student_number": student.student_number,
            "class_id": str(school_class.id),
            "class_name": school_class.name,
            "assignment_id": str(assignment.id),
            "assignment_title": assignment.title,
            "question_content": "Explain the concept.",
            "student_answer": "A partially correct answer.",
            "score": "2",
            "max_score": "10",
            "feedback": "Review the concept.",
            "error_type": "concept",
            "review_status": None,
        }
        assert teacher_wrong["facets"]["classes"] == [
            {"id": str(school_class.id), "name": school_class.name}
        ]
        assert (
            list_teacher_wrong_questions(
                db,
                CurrentActor(uuid.uuid4(), "other-teacher@example.com"),
            )["total"]
            == 0
        )
        assert (
            list_teacher_wrong_questions(
                db,
                teacher_actor,
                class_id=uuid.uuid4(),
            )["total"]
            == 0
        )

        actor = CurrentActor(student_user.id, student_user.email)
        release.release_mode = "feedback_only"
        db.commit()
        result = student_results(db, actor)["items"][0]
        assert result["total_score"] is None
        assert result["max_score"] is None
        assert "score" not in result["details"][0]
        assert "max_score" not in result["details"][0]
        wrong = student_wrong_questions(db, actor)
        assert wrong["total"] == 1
        assert "score" not in wrong["items"][0]
        assert "max_score" not in wrong["items"][0]
        assert wrong["items"][0]["question_content"] == "Explain the concept."
        assert wrong["items"][0]["student_answer"] == "A partially correct answer."
        thread = create_wrong_question_thread(answer.id, db, actor)
        reply = create_wrong_question_message(
            uuid.UUID(thread["id"]),
            WrongQuestionMessageInput(content="Could this be a grading mistake?"),
            db,
            actor,
        )
        job = db.get(WrongQuestionAIJob, uuid.UUID(reply["job"]["id"]))
        assert job is not None and job.status == "queued"
        assert db.scalar(select(ScoreRevision.id)) is None

        review_request = submit_teacher_review_request(
            uuid.UUID(thread["id"]),
            TeacherReviewRequestInput(question="Please review the published feedback."),
            db,
            actor,
        )
        waiting = decide_teacher_review_request(
            uuid.UUID(review_request["id"]),
            TeacherReviewDecisionInput(
                action="needs_information",
                teacher_response="Please explain the second step.",
            ),
            db,
            CurrentActor(teacher.id, teacher.email),
        )
        assert waiting["status"] == "waiting_student"
        updated = add_teacher_review_information(
            uuid.UUID(review_request["id"]),
            TeacherReviewAdditionalInformationInput(
                content="I used the definition from the lecture handout."
            ),
            db,
            actor,
        )
        assert updated["status"] == "pending"
        assert "学生补充" in updated["student_question"]
        pending_teacher_wrong = list_teacher_wrong_questions(
            db,
            teacher_actor,
            review_state="open",
        )
        assert pending_teacher_wrong["total"] == 1
        assert pending_teacher_wrong["summary"]["pending_review_count"] == 1
        assert pending_teacher_wrong["items"][0]["review_status"] == "pending"

        revoke_student_account_link(
            student.id,
            db,
            CurrentActor(teacher.id, teacher.email),
        )
        db.refresh(job)
        assert job.status == "cancelled"
        with pytest.raises(ApiProblem) as error:
            create_wrong_question_message(
                uuid.UUID(thread["id"]),
                WrongQuestionMessageInput(content="Can I still ask?"),
                db,
                actor,
            )
        assert error.value.code == "WRONG_QUESTION_THREAD_NOT_FOUND"
