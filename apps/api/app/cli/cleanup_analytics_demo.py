"""Delete only the fixed Analytics 7.2 synthetic fixture after an explicit confirmation flag."""

import argparse
import uuid

from sqlalchemy import text

from app.db.session import SessionLocal
from app.models import User

MARKER = "analytics72.synthetic.invalid"


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{MARKER}:{name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-marker", required=True)
    args = parser.parse_args()
    if args.confirm_marker != MARKER:
        raise SystemExit("marker mismatch; nothing deleted")
    owners = [uid("teacher-a"), uid("teacher-b")]
    with SessionLocal.begin() as db:
        users = db.query(User).filter(User.id.in_(owners)).all()
        if any(
            user.email
            not in {"synthetic-analytics72-a@example.com", "synthetic-analytics72-b@example.com"}
            for user in users
        ):
            raise RuntimeError("fixed synthetic user IDs no longer have expected emails")
        params = {"a": owners[0], "b": owners[1]}
        statements = [
            "DELETE FROM report_job_student_scopes WHERE report_job_id IN "
            "(SELECT id FROM report_jobs WHERE owner_id IN (:a,:b))",
            "DELETE FROM teaching_insights WHERE owner_id IN (:a,:b)",
            "DELETE FROM analytics_snapshots WHERE owner_id IN (:a,:b)",
            "DELETE FROM report_jobs WHERE owner_id IN (:a,:b)",
            "DELETE FROM grade_release_items WHERE grade_release_id IN "
            "(SELECT id FROM grade_releases WHERE owner_id IN (:a,:b))",
            "DELETE FROM grade_releases WHERE owner_id IN (:a,:b)",
            "DELETE FROM score_revisions WHERE actor_id IN (:a,:b)",
            "DELETE FROM teacher_reviews WHERE reviewer_id IN (:a,:b)",
            "DELETE FROM student_answers WHERE submission_id IN "
            "(SELECT id FROM submissions WHERE owner_id IN (:a,:b))",
            "DELETE FROM submission_score_snapshots WHERE generated_by IN (:a,:b)",
            "DELETE FROM submissions WHERE owner_id IN (:a,:b)",
            "DELETE FROM grading_batches WHERE owner_id IN (:a,:b)",
            "DELETE FROM assignment_classes WHERE assignment_id IN "
            "(SELECT id FROM assignments WHERE owner_id IN (:a,:b))",
            "DELETE FROM assignments WHERE owner_id IN (:a,:b)",
            "DELETE FROM class_students WHERE class_id IN "
            "(SELECT id FROM classes WHERE owner_id IN (:a,:b))",
            "DELETE FROM class_students WHERE student_id IN "
            "(SELECT id FROM students WHERE owner_id IN (:a,:b))",
            "DELETE FROM students WHERE owner_id IN (:a,:b)",
            "DELETE FROM knowledge_points WHERE owner_id IN (:a,:b)",
            "DELETE FROM classes WHERE owner_id IN (:a,:b)",
            "DELETE FROM stored_files WHERE owner_id IN (:a,:b)",
            "DELETE FROM audit_logs WHERE actor_id IN (:a,:b)",
            "DELETE FROM user_roles WHERE user_id IN (:a,:b)",
            "DELETE FROM user_sessions WHERE user_id IN (:a,:b)",
            "DELETE FROM users WHERE id IN (:a,:b)",
        ]
        for statement in statements:
            db.execute(text(statement), params)
    print("ANALYTICS_72_SYNTHETIC_DATA_REMOVED")


if __name__ == "__main__":
    main()
