"""Seed deterministic, idempotent S1/S2/S3 synthetic capacity fixtures."""

import json
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import (
    Assignment,
    AssignmentClass,
    ClassStudent,
    PaperVersion,
    Question,
    SchoolClass,
    Student,
    User,
)

MARKER = "performance-capacity.synthetic.invalid"
PASSWORD = "Synthetic-Capacity-Only!"

SCALES = (
    ("s1", 1, 50, 20),
    ("s2-t1-c1", 2, 100, 50),
    ("s2-t1-c2", 2, 100, 50),
    ("s2-t2-c1", 3, 100, 50),
    ("s2-t2-c2", 3, 100, 50),
    ("s3-t1", 4, 200, 100),
    ("s3-t2", 5, 200, 100),
)


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}")


def ensure_teacher(db: Session, index: int) -> User:
    teacher = db.get(User, uid(f"teacher-{index}"))
    email = f"capacity-teacher-{index}@{MARKER}"
    if teacher is None:
        teacher = User(
            id=uid(f"teacher-{index}"),
            email=email,
            password_hash=hash_password(PASSWORD),
            display_name=f"Synthetic Capacity Teacher {index}",
        )
        db.add(teacher)
        db.flush()
    elif teacher.email != email:
        raise RuntimeError(f"fixed teacher ID {teacher.id} is occupied by unexpected data")
    else:
        teacher.password_hash = hash_password(PASSWORD)
    return teacher


def main() -> None:
    summary: list[dict[str, object]] = []
    with SessionLocal.begin() as db:
        teachers = {index: ensure_teacher(db, index) for index in range(1, 6)}
        for scale, teacher_index, student_count, question_count in SCALES:
            teacher = teachers[teacher_index]
            school_class = db.get(SchoolClass, uid(f"class-{scale}"))
            if school_class is None:
                school_class = SchoolClass(
                    id=uid(f"class-{scale}"),
                    owner_id=teacher.id,
                    name=f"Synthetic Capacity {scale.upper()} {student_count}",
                    grade="S8",
                    subject="Synthetic",
                )
                db.add(school_class)
                db.flush()
            for student_index in range(1, student_count + 1):
                student = db.get(Student, uid(f"student-{scale}-{student_index}"))
                if student is None:
                    student = Student(
                        id=uid(f"student-{scale}-{student_index}"),
                        owner_id=teacher.id,
                        student_number=(
                            f"{teacher_index}{uid(f'class-{scale}').hex[:6]}{student_index:04d}"
                        ),
                        name=f"Synthetic {scale.upper()} Student {student_index:03d}",
                    )
                    db.add(student)
                    db.flush()
                membership_id = uid(f"membership-{scale}-{student_index}")
                if db.get(ClassStudent, membership_id) is None:
                    db.add(
                        ClassStudent(
                            id=membership_id,
                            class_id=school_class.id,
                            student_id=student.id,
                        )
                    )
            assignment = db.get(Assignment, uid(f"assignment-{scale}"))
            if assignment is None:
                assignment = Assignment(
                    id=uid(f"assignment-{scale}"),
                    owner_id=teacher.id,
                    title=f"Synthetic Capacity {scale.upper()} {question_count} Questions",
                    subject="Synthetic",
                    grade="S8",
                    total_score=Decimal(question_count),
                )
                db.add(assignment)
                db.flush()
                db.add(
                    AssignmentClass(
                        id=uid(f"assignment-class-{scale}"),
                        assignment_id=assignment.id,
                        class_id=school_class.id,
                    )
                )
                paper = PaperVersion(
                    id=uid(f"paper-{scale}"),
                    assignment_id=assignment.id,
                    version=1,
                    created_by=teacher.id,
                )
                db.add(paper)
                db.flush()
                assignment.active_paper_version_id = paper.id
            else:
                existing_paper = db.get(PaperVersion, uid(f"paper-{scale}"))
                if existing_paper is None:
                    raise RuntimeError(f"assignment {assignment.id} has no deterministic paper")
                paper = existing_paper
            existing = set(
                db.scalars(select(Question.id).where(Question.paper_version_id == paper.id)).all()
            )
            for question_index in range(1, question_count + 1):
                question_id = uid(f"question-{scale}-{question_index}")
                if question_id not in existing:
                    db.add(
                        Question(
                            id=question_id,
                            paper_version_id=paper.id,
                            question_number=str(question_index),
                            display_order=question_index,
                            question_type="single_choice",
                            content_text=f"Synthetic capacity question {question_index}",
                            max_score=Decimal("1"),
                        )
                    )
            summary.append(
                {
                    "scale": scale,
                    "teacher_index": teacher_index,
                    "class_id": str(school_class.id),
                    "assignment_id": str(assignment.id),
                    "students": student_count,
                    "questions": question_count,
                }
            )
    print(
        json.dumps(
            {
                "marker": MARKER,
                "password": PASSWORD,
                "teachers": [
                    {
                        "index": index,
                        "email": f"capacity-teacher-{index}@{MARKER}",
                        "id": str(uid(f"teacher-{index}")),
                    }
                    for index in range(1, 6)
                ],
                "fixtures": summary,
            }
        )
    )


if __name__ == "__main__":
    main()
