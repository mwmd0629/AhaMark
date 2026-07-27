"""Seed one publishable, synthetic Stage 6 assignment in the isolated database."""

import json
import os
from datetime import timedelta

from app.db.session import SessionLocal
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentClass,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    SchoolClass,
    StructuredRubricVersion,
    User,
    now_utc,
)
from sqlalchemy import func, select


def main() -> None:
    email = os.environ["PREPROD_TEACHER_EMAIL"]
    marker = email.split("@", 1)[0] + os.environ.get("PREPROD_FIXTURE_SUFFIX", "")
    with SessionLocal() as db:
        teacher = db.scalar(select(User).where(User.email == email))
        if teacher is None:
            raise RuntimeError("synthetic teacher missing")
        assignment = db.scalar(select(Assignment).where(Assignment.title == f"Stage 6 {marker}"))
        if assignment is None:
            school_class = SchoolClass(
                owner_id=teacher.id,
                name=f"Stage 6 Class {marker}",
                grade="Synthetic",
                subject="Mathematics",
                academic_year="2026-2027",
                semester="Synthetic",
                status=ArchiveStatus.active,
            )
            assignment = Assignment(
                owner_id=teacher.id,
                title=f"Stage 6 {marker}",
                subject="Mathematics",
                grade="Synthetic",
                description="Fully synthetic isolated Stage 6 assignment.",
                total_score=10,
                due_at=now_utc() + timedelta(days=7),
            )
            db.add_all([school_class, assignment])
            db.flush()
            db.add(AssignmentClass(assignment_id=assignment.id, class_id=school_class.id))
            paper = PaperVersion(assignment_id=assignment.id, version=1, created_by=teacher.id)
            db.add(paper)
            db.flush()
            assignment.active_paper_version_id = paper.id
            question = Question(
                paper_version_id=paper.id,
                question_number="1",
                display_order=1,
                question_type="calculation",
                content_text="Synthetic: calculate 1 + 1.",
                max_score=10,
            )
            db.add(question)
            db.flush()
            generation = (db.scalar(select(func.max(AssignmentGenerationJob.generation))) or 0) + 1
            job = AssignmentGenerationJob(
                owner_id=teacher.id,
                assignment_id=assignment.id,
                generation=generation,
                status="review_required",
                idempotency_key=f"{marker}-provider-unavailable",
                request_fingerprint="1" * 64,
                source_snapshot_hash="2" * 64,
                provider_config_version="assignment-generation-provider-v1",
                prompt_version="assignment-generation-prompt-v1",
                schema_version="assignment-generation-schema-v1",
                error_code="PROVIDER_UNAVAILABLE",
                error_message="生成 Provider 当前不可用，已进入教师手动回退。",
            )
            db.add(job)
            db.flush()
            db.add(
                AssignmentDraftRevision(
                    owner_id=teacher.id,
                    assignment_id=assignment.id,
                    generation_job_id=job.id,
                    revision=1,
                    source_snapshot_hash=job.source_snapshot_hash,
                    created_by_type="teacher",
                    created_by=teacher.id,
                )
            )
            answer = ReferenceAnswerVersion(
                question_id=question.id,
                source_type="teacher_official",
                raw_content="2",
                normalized_content="2",
                structured_content={"answer_type": "exact_scalar", "value": 2},
                content_hash="3" * 64,
                version=1,
                provenance={"marker": marker, "synthetic": True},
                created_by=teacher.id,
                status="confirmed",
                teacher_confirmed_at=now_utc(),
            )
            db.add(answer)
            db.flush()
            structured = StructuredRubricVersion(
                question_id=question.id,
                question_version="1",
                reference_answer_version_id=answer.id,
                rubric_version=1,
                title="Synthetic exact answer",
                total_points=10,
                status="confirmed",
                content_hash="4" * 64,
                created_by=teacher.id,
                confirmed_by=teacher.id,
                confirmed_at=now_utc(),
            )
            db.add(structured)
            db.flush()
            db.add(
                RubricCriterion(
                    rubric_version_id=structured.id,
                    stable_key="answer",
                    title="Synthetic answer correct",
                    max_points=10,
                    display_order=1,
                    criterion_type="answer",
                    required=True,
                    validation_mode="manual",
                )
            )
            db.commit()
        print(
            json.dumps({"assignment_id": str(assignment.id), "marker": marker, "synthetic": True})
        )


if __name__ == "__main__":
    main()
