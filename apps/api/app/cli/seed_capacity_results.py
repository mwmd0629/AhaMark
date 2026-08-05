"""Seed complete synthetic score/release fixtures for capacity reports and analytics."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.api.assignment_central_review import (
    _answer_content_payload,
    _criterion_payload,
    _rubric_content_payload,
)
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
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricSet,
    StructuredRubricSetItem,
    StructuredRubricVersion,
    Student,
    StudentAnswer,
    Submission,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
    VersionStatus,
)
from app.question_versions import question_version_token
from app.semantic_content import semantic_hash

SCALES = (
    ("s1", 1, 50, 20),
    ("s2-t1-c1", 2, 100, 50),
    ("s3-t1", 4, 200, 100),
)


def synthetic_hash(*parts: object) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode()).hexdigest()


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
        rubric_set = db.get(StructuredRubricSet, uid(f"structured-set-{scale}"))
        if rubric_set is None:
            total_points = Decimal(question_count).quantize(Decimal("0.01"))
            rubric_set = StructuredRubricSet(
                id=uid(f"structured-set-{scale}"),
                owner_id=teacher.id,
                assignment_id=assignment.id,
                paper_version_id=paper.id,
                version=1,
                status="active",
                content_hash=synthetic_hash(MARKER, scale, "structured-set-pending"),
                source_snapshot_hash=synthetic_hash(MARKER, scale, "source-snapshot"),
                total_points=total_points,
                created_by=teacher.id,
                confirmed_by=teacher.id,
                confirmed_at=generated_at,
                activated_at=generated_at,
            )
            db.add(rubric_set)
            db.flush()
            set_items: list[StructuredRubricSetItem] = []
            for question_index, question in enumerate(questions, 1):
                reference = ReferenceAnswerVersion(
                    id=uid(f"reference-{scale}-{question_index}"),
                    question_id=question.id,
                    source_type="teacher_official",
                    source_region={},
                    raw_content=f"Synthetic capacity answer {question_index}",
                    normalized_content=f"Synthetic capacity answer {question_index}",
                    structured_content={"synthetic": True},
                    content_hash="0" * 64,
                    version=1,
                    provenance={"fixture": MARKER},
                    created_by=teacher.id,
                    status="confirmed",
                    teacher_confirmed_at=generated_at,
                )
                reference.content_hash = semantic_hash(_answer_content_payload(reference))
                db.add(reference)
                db.flush()
                rubric = StructuredRubricVersion(
                    id=uid(f"structured-rubric-{scale}-{question_index}"),
                    question_id=question.id,
                    question_version=question_version_token(question),
                    reference_answer_version_id=reference.id,
                    rubric_version=1,
                    title=f"Synthetic capacity rubric {question_index}",
                    total_points=Decimal("1.00"),
                    status="confirmed",
                    content_hash="0" * 64,
                    created_by=teacher.id,
                    confirmed_by=teacher.id,
                    confirmed_at=generated_at,
                )
                db.add(rubric)
                db.flush()
                criterion = RubricCriterion(
                    id=uid(f"criterion-{scale}-{question_index}"),
                    rubric_version_id=rubric.id,
                    stable_key="manual-score",
                    title="Synthetic manually confirmed score",
                    description="Synthetic capacity fixture criterion",
                    max_points=Decimal("1.00"),
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
                )
                db.add(criterion)
                db.flush()
                rubric.content_hash = semantic_hash(_rubric_content_payload(db, rubric))
                set_item = StructuredRubricSetItem(
                    id=uid(f"structured-set-item-{scale}-{question_index}"),
                    rubric_set_id=rubric_set.id,
                    question_id=question.id,
                    question_version=rubric.question_version,
                    reference_answer_version_id=reference.id,
                    structured_rubric_version_id=rubric.id,
                    answer_content_hash=reference.content_hash,
                    rubric_content_hash=rubric.content_hash,
                    criteria_hash=semantic_hash([_criterion_payload(criterion)]),
                    display_order=question_index,
                    max_points=Decimal("1.00"),
                )
                db.add(set_item)
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
        elif (
            rubric_set.assignment_id != assignment.id
            or rubric_set.paper_version_id != paper.id
            or rubric_set.status != "active"
        ):
            raise RuntimeError(f"capacity Structured Rubric Set is inconsistent for {scale}")
        paper.status = VersionStatus.confirmed
        paper.confirmed_at = paper.confirmed_at or generated_at
        assignment.active_structured_rubric_set_id = rubric_set.id
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
                    structured_rubric_set_id=rubric_set.id,
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
