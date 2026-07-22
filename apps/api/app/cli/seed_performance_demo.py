"""Create an idempotent 2-class/50-student/20-question synthetic performance fixture."""

import json
import uuid
from decimal import Decimal

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

MARKER = "performance50.synthetic.invalid"
EMAIL = "synthetic-performance50@example.com"
PASSWORD = "Synthetic-Performance-50!"


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}")


def main() -> None:
    with SessionLocal.begin() as db:
        teacher = db.get(User, uid("teacher"))
        if teacher is None:
            teacher = User(
                id=uid("teacher"),
                email=EMAIL,
                password_hash=hash_password(PASSWORD),
                display_name="Synthetic Performance Teacher",
            )
            db.add(teacher)
            db.flush()
        elif teacher.email != EMAIL:
            raise RuntimeError("fixed performance teacher ID is occupied by unexpected data")
        else:
            teacher.password_hash = hash_password(PASSWORD)
        for class_index in (1, 2):
            school_class = db.get(SchoolClass, uid(f"class-{class_index}"))
            if school_class is None:
                school_class = SchoolClass(
                    id=uid(f"class-{class_index}"),
                    owner_id=teacher.id,
                    name=f"Synthetic Performance Class {class_index}",
                    grade="S8",
                    subject="Synthetic",
                )
                db.add(school_class)
                db.flush()
            for student_index in range(1, 51):
                student = db.get(Student, uid(f"student-{class_index}-{student_index}"))
                if student is None:
                    student = Student(
                        id=uid(f"student-{class_index}-{student_index}"),
                        owner_id=teacher.id,
                        student_number=f"{class_index}{student_index:04d}",
                        name=f"Synthetic Student {class_index}-{student_index:02d}",
                    )
                    db.add(student)
                    db.flush()
                if db.get(ClassStudent, uid(f"membership-{class_index}-{student_index}")) is None:
                    db.add(
                        ClassStudent(
                            id=uid(f"membership-{class_index}-{student_index}"),
                            class_id=school_class.id,
                            student_id=student.id,
                        )
                    )
            assignment = db.get(Assignment, uid(f"assignment-{class_index}"))
            if assignment is None:
                assignment = Assignment(
                    id=uid(f"assignment-{class_index}"),
                    owner_id=teacher.id,
                    title=f"Synthetic Performance Assignment {class_index}",
                    subject="Synthetic",
                    grade="S8",
                    total_score=Decimal("100"),
                )
                db.add(assignment)
                db.flush()
                db.add(
                    AssignmentClass(
                        id=uid(f"assignment-class-{class_index}"),
                        assignment_id=assignment.id,
                        class_id=school_class.id,
                    )
                )
                paper = PaperVersion(
                    id=uid(f"paper-{class_index}"),
                    assignment_id=assignment.id,
                    version=1,
                    created_by=teacher.id,
                )
                db.add(paper)
                db.flush()
                assignment.active_paper_version_id = paper.id
                for question_index in range(1, 21):
                    db.add(
                        Question(
                            id=uid(f"question-{class_index}-{question_index}"),
                            paper_version_id=paper.id,
                            question_number=str(question_index),
                            display_order=question_index,
                            question_type="single_choice" if question_index <= 10 else "essay",
                            content_text=f"Synthetic question {question_index}",
                            max_score=Decimal("5"),
                        )
                    )
    print(
        json.dumps(
            {
                "marker": MARKER,
                "teacher": str(uid("teacher")),
                "classes": [str(uid("class-1")), str(uid("class-2"))],
                "students_per_class": 50,
                "assignments": [str(uid("assignment-1")), str(uid("assignment-2"))],
                "questions_per_assignment": 20,
            }
        )
    )


if __name__ == "__main__":
    main()
