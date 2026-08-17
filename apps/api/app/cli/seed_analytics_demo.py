"""Create the idempotent, non-personal Analytics 7.2 HTTP verification fixture."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypeVar

from app.api.assignment_central_review import (
    _answer_content_payload,
    _criterion_payload,
    _rubric_content_payload,
)
from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import (
    AnalyticsSnapshot,
    Assignment,
    AssignmentClass,
    ClassStudent,
    GradeRelease,
    GradeReleaseItem,
    GradingBatch,
    KnowledgePoint,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    ReportJob,
    ReportJobStudentScope,
    RubricCriterion,
    SchoolClass,
    ScoreRevision,
    StoredFile,
    StructuredRubricSet,
    StructuredRubricSetItem,
    StructuredRubricVersion,
    Student,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
    TeachingInsight,
    User,
)
from app.question_versions import question_version_token
from app.results.services import compute_metrics, release_scores
from app.semantic_content import semantic_hash

MARKER = "analytics72.synthetic.invalid"
EMAIL_A = "synthetic-analytics72-a@example.com"
EMAIL_B = "synthetic-analytics72-b@example.com"
PASSWORD_A = "Synthetic-A-7.2!"
PASSWORD_B = "Synthetic-B-7.2!"


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}")


T = TypeVar("T")


def synthetic_hash(*parts: object) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode()).hexdigest()


def add(db: object, value: T) -> T:
    db.add(value)  # type: ignore[attr-defined]
    return value


def main() -> None:
    with SessionLocal() as db:
        existing = db.get(User, uid("teacher-a"))
        if existing is not None:
            existing.email, existing.password_hash = EMAIL_A, hash_password(PASSWORD_A)
            teacher_b = db.get(User, uid("teacher-b"))
            assert teacher_b is not None
            teacher_b.email, teacher_b.password_hash = EMAIL_B, hash_password(PASSWORD_B)
            for index in range(1, 4):
                for student_index in range(3):
                    snapshot = db.get(
                        SubmissionScoreSnapshot, uid(f"score-{index}-{student_index}")
                    )
                    if snapshot is None:
                        continue
                    details = [dict(item) for item in snapshot.details]
                    for item in details:
                        item["score"] = str(Decimal(str(item["score"])).quantize(Decimal("0.01")))
                        item["max_score"] = str(
                            Decimal(str(item["max_score"])).quantize(Decimal("0.01"))
                        )
                    snapshot.details = details
                    snapshot.total_score = sum(
                        (Decimal(item["score"]) for item in details), Decimal(0)
                    )
            db.flush()
            for index in range(1, 4):
                analytics = db.get(AnalyticsSnapshot, uid(f"analytics-{index}"))
                if analytics is not None:
                    rows = release_scores(db, uid(f"release-{index}"))
                    analytics.metrics = compute_metrics(rows)
            db.commit()
            print(json.dumps(summary(db), ensure_ascii=False))
            return
        teacher_a = add(
            db,
            User(
                id=uid("teacher-a"),
                email=EMAIL_A,
                password_hash=hash_password(PASSWORD_A),
                display_name="Synthetic Teacher A",
            ),
        )
        teacher_b = add(
            db,
            User(
                id=uid("teacher-b"),
                email=EMAIL_B,
                password_hash=hash_password(PASSWORD_B),
                display_name="Synthetic Teacher B",
            ),
        )
        db.flush()
        class_a = add(
            db,
            SchoolClass(
                id=uid("class-a"),
                owner_id=teacher_a.id,
                name="Synthetic Analytics 7.2 Class",
                grade="S7",
                subject="Synthetic",
            ),
        )
        class_b = add(
            db,
            SchoolClass(
                id=uid("class-b"),
                owner_id=teacher_b.id,
                name="Synthetic Isolation Class",
                grade="S7",
                subject="Synthetic",
            ),
        )
        db.flush()
        students = []
        for index in range(1, 4):
            student = add(
                db,
                Student(
                    id=uid(f"student-{index}"),
                    owner_id=teacher_a.id,
                    student_number=f"00{index}",
                    name=f"Synthetic Student {index}",
                ),
            )
            db.flush()
            add(
                db,
                ClassStudent(
                    id=uid(f"membership-{index}"), class_id=class_a.id, student_id=student.id
                ),
            )
            students.append(student)
        student_b = add(
            db,
            Student(
                id=uid("student-b"),
                owner_id=teacher_b.id,
                student_number="009",
                name="Synthetic Isolation Student",
            ),
        )
        db.flush()
        add(db, ClassStudent(id=uid("membership-b"), class_id=class_b.id, student_id=student_b.id))
        kp1 = add(
            db,
            KnowledgePoint(
                id=uid("kp-1"),
                owner_id=teacher_a.id,
                subject="Synthetic",
                grade="S7",
                name="Synthetic Algebra",
            ),
        )
        kp2 = add(
            db,
            KnowledgePoint(
                id=uid("kp-2"),
                owner_id=teacher_a.id,
                subject="Synthetic",
                grade="S7",
                name="Synthetic Geometry",
            ),
        )
        db.flush()
        release_times = [
            datetime(2026, 5, 1, tzinfo=UTC),
            datetime(2026, 6, 1, tzinfo=UTC),
            datetime(2026, 7, 1, tzinfo=UTC),
        ]
        maxima = [30, 60, 45]
        score_sets = [
            [(8, 7, 5), (10, 10, 10), (5, 5, 5)],
            [(18, 15, 12), (15, 15, 15)],
            [(12, 12, 10), (15, 15, 15), (8, 7, 6)],
        ]
        snapshots: list[AnalyticsSnapshot] = []
        first_review: TeacherReview | None = None
        for assignment_index, (maximum, score_rows) in enumerate(
            zip(maxima, score_sets, strict=True), 1
        ):
            assignment = add(
                db,
                Assignment(
                    id=uid(f"assignment-{assignment_index}"),
                    owner_id=teacher_a.id,
                    title=f"Synthetic Assignment {assignment_index}",
                    subject="Synthetic",
                    grade="S7",
                    status="completed",
                    total_score=Decimal(maximum),
                ),
            )
            db.flush()
            add(
                db,
                AssignmentClass(
                    id=uid(f"assignment-class-{assignment_index}"),
                    assignment_id=assignment.id,
                    class_id=class_a.id,
                ),
            )
            paper = add(
                db,
                PaperVersion(
                    id=uid(f"paper-{assignment_index}"),
                    assignment_id=assignment.id,
                    version=1,
                    status="confirmed",
                    source_type="manual",
                    created_by=teacher_a.id,
                    confirmed_at=release_times[assignment_index - 1],
                ),
            )
            db.flush()
            assignment.active_paper_version_id = paper.id
            question_max = (Decimal(maximum) / 3).quantize(Decimal("0.01"))
            total_points = Decimal(maximum).quantize(Decimal("0.01"))
            questions = [
                add(
                    db,
                    Question(
                        id=uid(f"q-{assignment_index}-{q}"),
                        paper_version_id=paper.id,
                        question_number=str(q),
                        display_order=q,
                        question_type="single_choice" if q < 3 else "essay",
                        content_text=f"Synthetic Q{q}",
                        max_score=question_max,
                        source="manual",
                    ),
                )
                for q in range(1, 4)
            ]
            db.flush()
            rubric_set = add(
                db,
                StructuredRubricSet(
                    id=uid(f"structured-set-{assignment_index}"),
                    owner_id=teacher_a.id,
                    assignment_id=assignment.id,
                    paper_version_id=paper.id,
                    version=1,
                    status="active",
                    content_hash=synthetic_hash(MARKER, assignment_index, "set-pending"),
                    source_snapshot_hash=synthetic_hash(
                        MARKER, assignment_index, "source-snapshot"
                    ),
                    total_points=total_points,
                    created_by=teacher_a.id,
                    confirmed_by=teacher_a.id,
                    confirmed_at=release_times[assignment_index - 1],
                    activated_at=release_times[assignment_index - 1],
                ),
            )
            db.flush()
            set_items: list[StructuredRubricSetItem] = []
            for q_index, question in enumerate(questions, 1):
                reference = add(
                    db,
                    ReferenceAnswerVersion(
                        id=uid(f"reference-{assignment_index}-{q_index}"),
                        question_id=question.id,
                        source_type="teacher_official",
                        source_region={},
                        raw_content=f"Synthetic answer {q_index}",
                        normalized_content=f"Synthetic answer {q_index}",
                        structured_content={"synthetic": True},
                        content_hash="0" * 64,
                        version=1,
                        provenance={"fixture": MARKER},
                        created_by=teacher_a.id,
                        status="confirmed",
                        teacher_confirmed_at=release_times[assignment_index - 1],
                    ),
                )
                reference.content_hash = semantic_hash(_answer_content_payload(reference))
                db.flush()
                rubric = add(
                    db,
                    StructuredRubricVersion(
                        id=uid(f"structured-rubric-{assignment_index}-{q_index}"),
                        question_id=question.id,
                        question_version=question_version_token(question),
                        reference_answer_version_id=reference.id,
                        rubric_version=1,
                        title=f"Synthetic rubric {q_index}",
                        total_points=question_max,
                        status="confirmed",
                        content_hash="0" * 64,
                        created_by=teacher_a.id,
                        confirmed_by=teacher_a.id,
                        confirmed_at=release_times[assignment_index - 1],
                    ),
                )
                db.flush()
                criterion = add(
                    db,
                    RubricCriterion(
                        id=uid(f"criterion-{assignment_index}-{q_index}"),
                        rubric_version_id=rubric.id,
                        stable_key="manual-score",
                        title="Synthetic manually confirmed score",
                        description="Synthetic analytics fixture criterion",
                        max_points=question_max,
                        display_order=1,
                        criterion_type="manual",
                        required=True,
                        dependencies=[],
                        expected_evidence={"fixture": MARKER},
                        validation_mode="manual",
                        validation_rule={},
                        manual_review_policy={"manual_only": True},
                        partial_credit_policy={},
                        metadata_={"synthetic": True},
                    ),
                )
                db.flush()
                rubric.content_hash = semantic_hash(_rubric_content_payload(db, rubric))
                set_item = add(
                    db,
                    StructuredRubricSetItem(
                        id=uid(f"structured-set-item-{assignment_index}-{q_index}"),
                        rubric_set_id=rubric_set.id,
                        question_id=question.id,
                        question_version=rubric.question_version,
                        reference_answer_version_id=reference.id,
                        structured_rubric_version_id=rubric.id,
                        answer_content_hash=reference.content_hash,
                        rubric_content_hash=rubric.content_hash,
                        criteria_hash=semantic_hash([_criterion_payload(criterion)]),
                        display_order=q_index,
                        max_points=question_max,
                    ),
                )
                set_items.append(set_item)
            rubric_set.content_hash = semantic_hash(
                {
                    "assignment_id": str(assignment.id),
                    "paper_version_id": str(paper.id),
                    "source_snapshot_hash": rubric_set.source_snapshot_hash,
                    "total_points": str(total_points),
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
                        for item in set_items
                    ],
                }
            )
            assignment.active_structured_rubric_set_id = rubric_set.id
            batch = add(
                db,
                GradingBatch(
                    id=uid(f"batch-{assignment_index}"),
                    owner_id=teacher_a.id,
                    assignment_id=assignment.id,
                    class_id=class_a.id,
                    name=f"Synthetic Batch {assignment_index}",
                    status="completed",
                    submission_count=len(score_rows),
                ),
            )
            release = add(
                db,
                GradeRelease(
                    id=uid(f"release-{assignment_index}"),
                    owner_id=teacher_a.id,
                    assignment_id=assignment.id,
                    class_id=class_a.id,
                    version=1,
                    status="released",
                    released_at=release_times[assignment_index - 1],
                    created_by=teacher_a.id,
                    idempotency_key=f"{MARKER}:release:{assignment_index}",
                ),
            )
            db.flush()
            for student_index, scores in enumerate(score_rows):
                student = students[student_index]
                submission = add(
                    db,
                    Submission(
                        id=uid(f"submission-{assignment_index}-{student_index}"),
                        owner_id=teacher_a.id,
                        grading_batch_id=batch.id,
                        assignment_id=assignment.id,
                        class_id=class_a.id,
                        student_id=student.id,
                        status="finalized",
                        finalized_at=release_times[assignment_index - 1],
                    ),
                )
                db.flush()
                details = []
                total = Decimal(0)
                for q_index, (question, raw_score) in enumerate(
                    zip(questions, scores, strict=True), 1
                ):
                    score = (Decimal(raw_score) * question_max / Decimal(max(scores))).quantize(
                        Decimal("0.01")
                    )
                    total += score
                    answer = add(
                        db,
                        StudentAnswer(
                            id=uid(f"answer-{assignment_index}-{student_index}-{q_index}"),
                            submission_id=submission.id,
                            question_id=question.id,
                            question_version_reference="synthetic-v1",
                            status="reviewed",
                            recognized_text="synthetic",
                            requires_review=False,
                        ),
                    )
                    db.flush()
                    error = (
                        "concept"
                        if score < question_max and q_index == 1
                        else "calculation"
                        if score < question_max
                        else None
                    )
                    review = add(
                        db,
                        TeacherReview(
                            id=uid(f"review-{assignment_index}-{student_index}-{q_index}"),
                            student_answer_id=answer.id,
                            reviewer_id=teacher_a.id,
                            decision="manual_score",
                            final_score=score,
                            final_feedback=f"Synthetic confirmed feedback {q_index}",
                            final_error_type=error,
                            confirmed_at=release_times[assignment_index - 1],
                        ),
                    )
                    db.flush()
                    if first_review is None:
                        first_review = review
                    kp_ids = [kp1.id] if q_index < 3 else ([kp2.id] if assignment_index > 1 else [])
                    details.append(
                        {
                            "question_id": str(question.id),
                            "question_number": str(q_index),
                            "question_type": question.question_type,
                            "score": str(score),
                            "max_score": str(question_max),
                            "teacher_review_id": str(review.id),
                            "final_error_type": error,
                            "final_feedback": review.final_feedback,
                            "knowledge_point_ids": [str(x) for x in kp_ids],
                            "grading_method": "manual",
                            "finalized_at": release_times[assignment_index - 1].isoformat(),
                        }
                    )
                snapshot = add(
                    db,
                    SubmissionScoreSnapshot(
                        id=uid(f"score-{assignment_index}-{student_index}"),
                        submission_id=submission.id,
                        assignment_id=assignment.id,
                        student_id=student.id,
                        paper_version_id=paper.id,
                        structured_rubric_set_id=rubric_set.id,
                        total_score=total,
                        max_score=Decimal(maximum),
                        status="complete",
                        generated_by=teacher_a.id,
                        generated_at=release_times[assignment_index - 1],
                        version=1,
                        details=details,
                    ),
                )
                db.flush()
                add(
                    db,
                    GradeReleaseItem(
                        id=uid(f"release-item-{assignment_index}-{student_index}"),
                        grade_release_id=release.id,
                        student_id=student.id,
                        submission_id=submission.id,
                        score_snapshot_id=snapshot.id,
                    ),
                )
            db.flush()
            rows = release_scores(db, release.id)
            analytics = add(
                db,
                AnalyticsSnapshot(
                    id=uid(f"analytics-{assignment_index}"),
                    owner_id=teacher_a.id,
                    assignment_id=assignment.id,
                    class_id=class_a.id,
                    grade_release_id=release.id,
                    schema_version="1.0",
                    status="complete",
                    source_snapshot_count=len(rows),
                    metrics=compute_metrics(rows),
                    generated_at=release_times[assignment_index - 1],
                ),
            )
            snapshots.append(analytics)
        assert first_review is not None
        add(
            db,
            ScoreRevision(
                id=uid("revision"),
                teacher_review_id=first_review.id,
                student_answer_id=first_review.student_answer_id,
                actor_id=teacher_a.id,
                previous_score=Decimal("0"),
                new_score=first_review.final_score,
                reason="Synthetic verification revision",
                created_at=release_times[0],
            ),
        )
        stored = add(
            db,
            StoredFile(
                id=uid("stored-report"),
                owner_id=teacher_a.id,
                storage_key=f"synthetic/{MARKER}/report.pdf",
                original_name="synthetic-report.pdf",
                content_type="application/pdf",
                size=1,
                checksum="0" * 64,
                status="ready",
            ),
        )
        db.flush()
        completed = add(
            db,
            ReportJob(
                id=uid("report-completed"),
                owner_id=teacher_a.id,
                assignment_id=snapshots[-1].assignment_id,
                class_id=class_a.id,
                grade_release_id=snapshots[-1].grade_release_id,
                report_type="student_report_pdf",
                status="completed",
                progress=100,
                stored_file_id=stored.id,
                idempotency_key=f"{MARKER}:completed",
                completed_at=release_times[-1],
                expires_at=datetime.now(UTC) + timedelta(days=30),
            ),
        )
        failed = add(
            db,
            ReportJob(
                id=uid("report-failed"),
                owner_id=teacher_a.id,
                assignment_id=snapshots[-1].assignment_id,
                class_id=class_a.id,
                grade_release_id=snapshots[-1].grade_release_id,
                report_type="student_report_pdf",
                status="failed",
                progress=20,
                error_code="SYNTHETIC_FAILURE",
                error_message="Synthetic worker failure",
                idempotency_key=f"{MARKER}:failed",
                expires_at=datetime.now(UTC) + timedelta(days=30),
            ),
        )
        db.flush()
        add(db, ReportJobStudentScope(report_job_id=completed.id, student_id=students[0].id))
        add(db, ReportJobStudentScope(report_job_id=failed.id, student_id=students[0].id))
        metric = snapshots[-1].metrics["questions"][0]
        add(
            db,
            TeachingInsight(
                id=uid("insight"),
                owner_id=teacher_a.id,
                analytics_snapshot_id=snapshots[-1].id,
                status="generated",
                content={
                    "title": "Synthetic rule insight",
                    "recommendations": ["Review synthetic question 1"],
                    "rules_version": "rules-v1",
                },
                evidence=[
                    {
                        "metric": "question_score_rate",
                        "question_id": metric["question_id"],
                        "value": metric["score_rate"],
                        "participants": metric["participants"],
                    }
                ],
            ),
        )
        db.commit()
        print(json.dumps(summary(db), ensure_ascii=False))


def summary(db: object) -> dict[str, object]:
    return {
        "marker": MARKER,
        "teacher_a_email": EMAIL_A,
        "teacher_b_email": EMAIL_B,
        "teacher_a_id": str(uid("teacher-a")),
        "teacher_b_id": str(uid("teacher-b")),
        "class_id": str(uid("class-a")),
        "student_ids": [str(uid(f"student-{x}")) for x in range(1, 4)],
        "assignment_ids": [str(uid(f"assignment-{x}")) for x in range(1, 4)],
        "release_ids": [str(uid(f"release-{x}")) for x in range(1, 4)],
        "analytics_ids": [str(uid(f"analytics-{x}")) for x in range(1, 4)],
        "question_id": str(uid("q-3-1")),
        "knowledge_point_ids": [str(uid("kp-1")), str(uid("kp-2"))],
        "report_ids": [str(uid("report-completed")), str(uid("report-failed"))],
        "insight_id": str(uid("insight")),
    }


if __name__ == "__main__":
    main()
