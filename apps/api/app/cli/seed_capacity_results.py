"""Seed complete synthetic score/release fixtures for capacity reports and analytics."""

import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.cli.seed_capacity_demo import MARKER, uid
from app.db.session import SessionLocal
from app.models import (
    AnalyticsSnapshot,
    Assignment,
    AssignmentStatus,
    GradeRelease,
    GradeReleaseItem,
    GradingBatch,
    KnowledgePoint,
    PaperVersion,
    Question,
    RubricVersion,
    Student,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
)

SCALES = (
    ("s1", 1, 50, 20),
    ("s2-t1-c1", 2, 100, 50),
    ("s3-t1", 4, 200, 100),
)


def build_scale(
    scale: str,
    teacher_index: int,
    student_count: int,
    question_count: int,
) -> dict[str, object]:
    generated_at = datetime.now(UTC)
    with SessionLocal.begin() as db:
        teacher = db.get(User, uid(f"teacher-{teacher_index}"))
        assignment = db.get(Assignment, uid(f"assignment-{scale}"))
        paper = db.get(PaperVersion, uid(f"paper-{scale}"))
        school_class_id = uid(f"class-{scale}")
        if teacher is None or assignment is None or paper is None:
            raise RuntimeError(f"capacity base fixture missing for {scale}")
        questions = list(
            db.scalars(
                select(Question)
                .where(Question.paper_version_id == paper.id)
                .order_by(Question.display_order)
            )
        )
        if len(questions) != question_count:
            raise RuntimeError(f"{scale} expected {question_count} questions, got {len(questions)}")
        rubric = db.get(RubricVersion, uid(f"rubric-{scale}"))
        if rubric is None:
            rubric = RubricVersion(
                id=uid(f"rubric-{scale}"),
                assignment_id=assignment.id,
                version=1,
                status="confirmed",
                created_by=teacher.id,
                confirmed_at=generated_at,
            )
            db.add(rubric)
            db.flush()
        assignment.active_rubric_version_id = rubric.id
        assignment.status = AssignmentStatus.completed
        knowledge_points = []
        for index in range(1, 6):
            point = db.get(KnowledgePoint, uid(f"kp-{teacher_index}-{index}"))
            if point is None:
                point = KnowledgePoint(
                    id=uid(f"kp-{teacher_index}-{index}"),
                    owner_id=teacher.id,
                    subject="Synthetic",
                    grade="S8",
                    name=f"Synthetic Capacity KP {teacher_index}-{index}",
                )
                db.add(point)
            knowledge_points.append(point)
        batch = db.get(GradingBatch, uid(f"batch-{scale}"))
        if batch is None:
            batch = GradingBatch(
                id=uid(f"batch-{scale}"),
                owner_id=teacher.id,
                assignment_id=assignment.id,
                class_id=school_class_id,
                name=f"Synthetic Capacity Batch {scale}",
                status="completed",
                submission_count=student_count,
                recognized_count=student_count,
                graded_count=student_count,
                reviewed_count=student_count,
                completed_at=generated_at,
            )
            db.add(batch)
        release = db.get(GradeRelease, uid(f"release-{scale}"))
        if release is None:
            release = GradeRelease(
                id=uid(f"release-{scale}"),
                owner_id=teacher.id,
                assignment_id=assignment.id,
                class_id=school_class_id,
                version=1,
                status="released",
                released_at=generated_at,
                created_by=teacher.id,
                idempotency_key=f"{MARKER}:release:{scale}",
            )
            db.add(release)
        db.flush()
        created_students = 0
        for student_index in range(1, student_count + 1):
            student = db.get(Student, uid(f"student-{scale}-{student_index}"))
            if student is None:
                raise RuntimeError(f"capacity student missing: {scale}/{student_index}")
            submission_id = uid(f"submission-{scale}-{student_index}")
            submission = db.get(Submission, submission_id)
            if submission is None:
                submission = Submission(
                    id=submission_id,
                    owner_id=teacher.id,
                    grading_batch_id=batch.id,
                    assignment_id=assignment.id,
                    class_id=school_class_id,
                    student_id=student.id,
                    status="finalized",
                    finalized_at=generated_at,
                )
                db.add(submission)
                db.flush()
            snapshot_id = uid(f"snapshot-{scale}-{student_index}")
            snapshot = db.get(SubmissionScoreSnapshot, snapshot_id)
            if snapshot is None:
                details: list[dict[str, object]] = []
                total = Decimal(0)
                for question_index, question in enumerate(questions, 1):
                    question.question_type = (
                        "essay" if question_index % 10 == 0 else "single_choice"
                    )
                    score = (
                        Decimal("0.5")
                        if (student_index + question_index) % 4 == 0
                        else Decimal("1")
                    )
                    total += score
                    answer_id = uid(f"answer-{scale}-{student_index}-{question_index}")
                    review_id = uid(f"review-{scale}-{student_index}-{question_index}")
                    db.add(
                        StudentAnswer(
                            id=answer_id,
                            submission_id=submission.id,
                            question_id=question.id,
                            question_version_reference="capacity-v1",
                            status="reviewed",
                            recognized_text=f"Synthetic answer {student_index}-{question_index}",
                            requires_review=False,
                        )
                    )
                    error_type = "concept" if score < 1 else None
                    db.add(
                        TeacherReview(
                            id=review_id,
                            student_answer_id=answer_id,
                            reviewer_id=teacher.id,
                            decision="manual_score",
                            final_score=score,
                            final_feedback=f"Synthetic feedback {student_index}-{question_index}",
                            final_error_type=error_type,
                            confirmed_at=generated_at,
                        )
                    )
                    details.append(
                        {
                            "question_id": str(question.id),
                            "question_number": question.question_number,
                            "question_type": question.question_type,
                            "score": str(score),
                            "max_score": "1",
                            "teacher_review_id": str(review_id),
                            "final_error_type": error_type,
                            "final_feedback": (
                                f"Synthetic feedback {student_index}-{question_index}"
                            ),
                            "knowledge_point_ids": [
                                str(
                                    knowledge_points[
                                        (question_index - 1) % len(knowledge_points)
                                    ].id
                                )
                            ],
                            "grading_method": "manual",
                            "finalized_at": generated_at.isoformat(),
                        }
                    )
                snapshot = SubmissionScoreSnapshot(
                    id=snapshot_id,
                    submission_id=submission.id,
                    assignment_id=assignment.id,
                    student_id=student.id,
                    paper_version_id=paper.id,
                    rubric_version_id=rubric.id,
                    total_score=total,
                    max_score=Decimal(question_count),
                    status="complete",
                    generated_by=teacher.id,
                    generated_at=generated_at,
                    version=1,
                    details=details,
                )
                db.add(snapshot)
                created_students += 1
                db.flush()
            release_item = db.scalar(
                select(GradeReleaseItem).where(
                    GradeReleaseItem.grade_release_id == release.id,
                    GradeReleaseItem.student_id == student.id,
                )
            )
            if release_item is None:
                db.add(
                    GradeReleaseItem(
                        id=uid(f"release-item-{scale}-{student_index}"),
                        grade_release_id=release.id,
                        student_id=student.id,
                        submission_id=submission.id,
                        score_snapshot_id=snapshot.id,
                    )
                )
        return {
            "scale": scale,
            "teacher_index": teacher_index,
            "students": student_count,
            "questions": question_count,
            "release_id": str(release.id),
            "created_students": created_students,
            "existing_analytics": len(
                list(
                    db.scalars(
                        select(AnalyticsSnapshot).where(
                            AnalyticsSnapshot.grade_release_id == release.id
                        )
                    )
                )
            ),
        }


def main() -> None:
    print(
        json.dumps(
            {
                "marker": MARKER,
                "scales": [
                    build_scale(scale, teacher, students, questions)
                    for scale, teacher, students, questions in SCALES
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
