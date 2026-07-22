"""Delete only the fixed performance fixture after explicit marker confirmation."""

import argparse
import uuid

from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models import Assignment, ClassStudent, PaperVersion, Question, SchoolClass, Student, User

MARKER = "performance50.synthetic.invalid"
EMAIL = "synthetic-performance50@example.com"


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-marker", required=True)
    args = parser.parse_args()
    if args.confirm_marker != MARKER:
        raise SystemExit("marker mismatch; nothing deleted")
    with SessionLocal.begin() as db:
        teacher = db.get(User, uid("teacher"))
        if teacher is None:
            print("performance fixture absent; nothing deleted")
            return
        if teacher.email != EMAIL:
            raise RuntimeError("fixed performance teacher ID has unexpected email")
        counts = {
            "classes": len(
                db.scalars(select(SchoolClass.id).where(SchoolClass.owner_id == teacher.id)).all()
            ),
            "students": len(
                db.scalars(select(Student.id).where(Student.owner_id == teacher.id)).all()
            ),
            "assignments": len(
                db.scalars(select(Assignment.id).where(Assignment.owner_id == teacher.id)).all()
            ),
        }
        print(f"validated cleanup scope: {counts}")
        assignment_ids = select(Assignment.id).where(Assignment.owner_id == teacher.id)
        paper_ids = select(PaperVersion.id).where(PaperVersion.assignment_id.in_(assignment_ids))
        db.execute(delete(Question).where(Question.paper_version_id.in_(paper_ids)))
        db.execute(delete(PaperVersion).where(PaperVersion.assignment_id.in_(assignment_ids)))
        db.execute(delete(Assignment).where(Assignment.owner_id == teacher.id))
        student_ids = select(Student.id).where(Student.owner_id == teacher.id)
        db.execute(delete(ClassStudent).where(ClassStudent.student_id.in_(student_ids)))
        db.execute(delete(Student).where(Student.owner_id == teacher.id))
        db.execute(delete(SchoolClass).where(SchoolClass.owner_id == teacher.id))
        db.delete(teacher)
    print("PERFORMANCE50_SYNTHETIC_DATA_REMOVED")


if __name__ == "__main__":
    main()
