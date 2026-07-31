from __future__ import annotations

import inspect
import os
import uuid
from decimal import Decimal

import pytest
from app.db.session import SessionLocal
from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentReviewSession,
    AssignmentRubricPublicationBinding,
    AssignmentStatus,
    GradingBatch,
    PaperVersion,
    Question,
    QuestionRecognitionEvidence,
    RecognitionRevision,
    ReferenceAnswerVersion,
    RegionEvidenceImage,
    RubricCriterion,
    SchoolClass,
    StoredFile,
    StructuredRubricVersion,
    Student,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionPage,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    User,
    VersionStatus,
    now_utc,
)
from app.processing.contracts import (
    PROCESSING_INPUT_SCHEMA,
    ProcessingInputError,
    ProcessingInputSnapshot,
    build_request_hash,
    build_run_input_version,
    canonical_hash,
    canonicalize,
)
from app.processing.input_snapshot import (
    _current_evidence,
    _current_question_version,
    _latest_confirmed_formals,
    build_processing_input_snapshot,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified


def test_processing_canonicalization_retains_identity_and_normalizes_content() -> None:
    identity = uuid.uuid4()
    left = {
        "id": identity,
        "text": "Cafe\u0301  \r\nanswer\t ",
        "points": Decimal("2.5000"),
    }
    right = {
        "points": Decimal("2.5"),
        "text": "Caf\u00e9\nanswer",
        "id": str(identity).upper(),
    }
    assert canonicalize(left)["id"] == str(identity)
    assert canonical_hash(left) == canonical_hash(right)


def test_run_and_request_hashes_are_order_stable_and_suggestion_only() -> None:
    first = ProcessingInputSnapshot(
        payload={
            "schema": PROCESSING_INPUT_SCHEMA,
            "submission": {"id": "00000000-0000-0000-0000-000000000002"},
            "answer": {"id": "00000000-0000-0000-0000-000000000004"},
        },
        input_version="b" * 64,
    )
    second = ProcessingInputSnapshot(
        payload={
            "schema": PROCESSING_INPUT_SCHEMA,
            "submission": {"id": "00000000-0000-0000-0000-000000000001"},
            "answer": {"id": "00000000-0000-0000-0000-000000000003"},
        },
        input_version="a" * 64,
    )
    run_hash = build_run_input_version([first, second])
    assert run_hash == build_run_input_version([second, first])
    request_hash = build_request_hash(
        run_input_version=run_hash,
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        config_version="config-v1",
    )
    assert request_hash == build_request_hash(
        run_input_version=run_hash,
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        config_version="config-v1",
    )
    assert request_hash != build_request_hash(
        run_input_version=run_hash,
        prompt_version="prompt-v2",
        schema_version="schema-v1",
        config_version="config-v1",
    )


def test_formal_selector_never_falls_back_to_newer_draft() -> None:
    with SessionLocal() as db:
        owner = User(
            email=f"processing-{uuid.uuid4()}@example.test",
            password_hash="test",
            display_name="Processing Test",
        )
        db.add(owner)
        db.flush()
        assignment = Assignment(owner_id=owner.id, title="snapshot")
        db.add(assignment)
        db.flush()
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status=VersionStatus.confirmed,
            created_by=owner.id,
        )
        db.add(paper)
        db.flush()
        question = Question(
            paper_version_id=paper.id,
            question_number="1",
            display_order=1,
            question_type="short_answer",
            content_text="Q",
            max_score=Decimal("2"),
        )
        db.add(question)
        db.flush()
        confirmed_answer = ReferenceAnswerVersion(
            question_id=question.id,
            source_type="teacher_official",
            source_region={},
            raw_content="A",
            normalized_content="A",
            structured_content={},
            content_hash="a" * 64,
            version=1,
            provenance={},
            created_by=owner.id,
            status="confirmed",
        )
        draft_answer = ReferenceAnswerVersion(
            question_id=question.id,
            source_type="teacher_official",
            source_region={},
            raw_content="draft",
            normalized_content="draft",
            structured_content={},
            content_hash="b" * 64,
            version=2,
            provenance={},
            created_by=owner.id,
            status="draft",
        )
        db.add_all([confirmed_answer, draft_answer])
        db.flush()
        confirmed_rubric = StructuredRubricVersion(
            question_id=question.id,
            question_version=_current_question_version(question),
            reference_answer_version_id=confirmed_answer.id,
            rubric_version=1,
            title="R",
            total_points=Decimal("2"),
            status="confirmed",
            content_hash="c" * 64,
            created_by=owner.id,
        )
        draft_rubric = StructuredRubricVersion(
            question_id=question.id,
            question_version=_current_question_version(question),
            reference_answer_version_id=draft_answer.id,
            rubric_version=2,
            title="draft",
            total_points=Decimal("2"),
            status="draft",
            content_hash="d" * 64,
            created_by=owner.id,
        )
        db.add_all([confirmed_rubric, draft_rubric])
        db.flush()
        unrelated_confirmed_answer = ReferenceAnswerVersion(
            question_id=question.id,
            source_type="teacher_official",
            source_region={},
            raw_content="unrelated",
            normalized_content="unrelated",
            structured_content={},
            content_hash="e" * 64,
            version=3,
            provenance={},
            created_by=owner.id,
            status="confirmed",
        )
        db.add(unrelated_confirmed_answer)
        db.flush()
        retired_rubric = StructuredRubricVersion(
            question_id=question.id,
            question_version=_current_question_version(question),
            reference_answer_version_id=unrelated_confirmed_answer.id,
            rubric_version=3,
            title="retired",
            total_points=Decimal("2"),
            status="retired",
            content_hash="f" * 64,
            created_by=owner.id,
        )
        db.add(retired_rubric)
        db.flush()
        db.add(
            RubricCriterion(
                rubric_version_id=confirmed_rubric.id,
                stable_key="result",
                title="Result",
                max_points=Decimal("2"),
                display_order=0,
                criterion_type="result",
                required=True,
                dependencies=[],
                expected_evidence={},
                validation_mode="manual",
                manual_review_policy={},
                partial_credit_policy={},
                validation_rule={},
                metadata_={},
            )
        )
        db.flush()

        answer, rubric, criteria = _latest_confirmed_formals(db, question)
        assert answer.id == confirmed_answer.id
        assert rubric.id == confirmed_rubric.id
        assert [item.stable_key for item in criteria] == ["result"]


def test_snapshot_builder_is_read_only_and_reuses_phase2_projection_validator() -> None:
    source = inspect.getsource(build_processing_input_snapshot)
    assert "validate_current_projection_under_locks(" in source
    assert "lock=False" in source
    assert not any(
        marker in source
        for marker in ("db.add(", "db.delete(", "db.commit(", "db.flush(", "db.execute(")
    )


def _evidence_fixture() -> tuple[object, dict[str, object]]:
    db = SessionLocal()
    owner = User(
        email=f"evidence-{uuid.uuid4()}@example.test",
        password_hash="test",
        display_name="Evidence Test",
    )
    db.add(owner)
    db.flush()
    school_class = SchoolClass(owner_id=owner.id, name=f"class-{uuid.uuid4()}")
    student = Student(
        owner_id=owner.id,
        student_number=str(uuid.uuid4()),
        name="Synthetic Student",
    )
    assignment = Assignment(
        owner_id=owner.id,
        title="published snapshot",
        status=AssignmentStatus.published,
    )
    db.add_all([school_class, student, assignment])
    db.flush()
    paper = PaperVersion(
        assignment_id=assignment.id,
        version=1,
        status=VersionStatus.confirmed,
        created_by=owner.id,
    )
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    question = Question(
        paper_version_id=paper.id,
        question_number="1",
        display_order=1,
        question_type="short_answer",
        content_text="Q",
        max_score=Decimal("2"),
    )
    db.add(question)
    db.flush()
    batch = GradingBatch(
        owner_id=owner.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
    )
    db.add(batch)
    db.flush()
    submission = Submission(
        owner_id=owner.id,
        grading_batch_id=batch.id,
        assignment_id=assignment.id,
        class_id=school_class.id,
        student_id=student.id,
    )
    stored = StoredFile(
        owner_id=owner.id,
        storage_key=f"synthetic/{uuid.uuid4()}",
        original_name="synthetic.png",
        content_type="image/png",
        size=1,
        checksum="f" * 64,
    )
    db.add_all([submission, stored])
    db.flush()
    page = SubmissionPage(
        submission_id=submission.id,
        stored_file_id=stored.id,
        page_number=1,
        page_version=1,
    )
    answer = StudentAnswer(
        submission_id=submission.id,
        question_id=question.id,
        question_version_reference=str(paper.id),
        status="recognition_confirmed",
        recognized_text="answer",
        requires_review=False,
    )
    db.add_all([page, answer])
    db.flush()
    region = StudentAnswerRegion(
        student_answer_id=answer.id,
        submission_page_id=page.id,
        x=Decimal("0"),
        y=Decimal("0"),
        width=Decimal("1"),
        height=Decimal("1"),
        status="confirmed",
        confirmed_by=owner.id,
        confirmed_at=now_utc(),
        region_version=1,
    )
    job = SubmissionRecognitionJob(
        owner_id=owner.id,
        submission_id=submission.id,
        status="completed",
        provider="fake",
        provider_version="v1",
        idempotency_key=str(uuid.uuid4()),
        provider_kind="printed_text",
        config_version="config-v1",
        input_hash="1" * 64,
        output_hash="2" * 64,
        generation=1,
    )
    db.add_all([region, job])
    db.flush()
    image = RegionEvidenceImage(
        owner_id=owner.id,
        submission_id=submission.id,
        submission_page_id=page.id,
        student_answer_region_id=region.id,
        source_kind="processed",
        object_key=f"synthetic/evidence/{uuid.uuid4()}",
        content_hash="3" * 64,
        input_hash="4" * 64,
        width=10,
        height=10,
        margin_pixels=0,
        source_page_number=1,
        region_order=0,
        page_version=1,
        region_version=1,
        processing_config_version="config-v1",
        status="ready",
    )
    db.add(image)
    db.flush()
    block = SubmissionRecognitionBlock(
        submission_recognition_job_id=job.id,
        submission_page_id=page.id,
        block_index=0,
        text="raw",
        normalized_text="answer",
        confidence=Decimal("1"),
        status="confirmed",
        x=Decimal("0"),
        y=Decimal("0"),
        width=Decimal("1"),
        height=Decimal("1"),
        provider="fake",
        provider_version="v1",
        student_answer_region_id=region.id,
        region_evidence_image_id=image.id,
        source_page_number=1,
        reading_order=0,
        recognition_version=1,
        input_hash=image.input_hash,
        output_hash="5" * 64,
        confirmed_by=owner.id,
        confirmed_at=now_utc(),
        requires_review=False,
    )
    db.add(block)
    db.flush()
    revision = RecognitionRevision(
        recognition_block_id=block.id,
        revision=1,
        source="teacher",
        raw_text="raw",
        normalized_text="answer",
        latex=None,
        warning_codes=[],
        base_recognition_version=1,
        confirmed=True,
    )
    db.add(revision)
    db.flush()
    evidence = QuestionRecognitionEvidence(
        owner_id=owner.id,
        submission_id=submission.id,
        student_answer_id=answer.id,
        recognition_job_id=job.id,
        status="confirmed",
        block_sources=[
            {
                "region_id": str(region.id),
                "region_version": 1,
                "block_id": str(block.id),
                "block_recognition_version": 1,
                "block_recognition_job_id": str(job.id),
            }
        ],
        normalized_text="answer",
        provider_versions={"fake": "v1"},
        input_hash="6" * 64,
        output_hash="7" * 64,
        recognition_version=1,
        confirmed_revision=1,
        requires_review=False,
        confirmed_by=owner.id,
        confirmed_at=now_utc(),
    )
    db.add(evidence)
    db.commit()
    return db, {
        "owner": owner,
        "submission": submission,
        "answer": answer,
        "region": region,
        "page": page,
        "job": job,
        "image": image,
        "block": block,
        "revision": revision,
        "evidence": evidence,
    }


def test_current_evidence_snapshot_is_deterministic_and_zero_write() -> None:
    db, rows = _evidence_fixture()
    try:
        block = rows["block"]
        assert isinstance(block, SubmissionRecognitionBlock)
        before_updated_at = block.updated_at
        before_counts = {
            model: db.scalar(select(func.count()).select_from(model))
            for model in (
                SubmissionRecognitionJob,
                SubmissionRecognitionBlock,
                RecognitionRevision,
                QuestionRecognitionEvidence,
            )
        }
        first = _current_evidence(
            db,
            owner_id=rows["owner"].id,
            submission=rows["submission"],
            answer=rows["answer"],
        )
        second = _current_evidence(
            db,
            owner_id=rows["owner"].id,
            submission=rows["submission"],
            answer=rows["answer"],
        )
        assert first[0].id == second[0].id
        assert first[1] == second[1]
        assert block.updated_at == before_updated_at
        assert before_counts == {
            model: db.scalar(select(func.count()).select_from(model)) for model in before_counts
        }
    finally:
        db.close()


def test_every_revision_content_field_changes_processing_content_hash() -> None:
    db, rows = _evidence_fixture()
    try:
        previous = _current_evidence(
            db,
            owner_id=rows["owner"].id,
            submission=rows["submission"],
            answer=rows["answer"],
        )[1][0]["revision"]["content_hash"]
        revision = rows["revision"]
        assert isinstance(revision, RecognitionRevision)
        for field, value in (
            ("raw_text", "changed raw"),
            ("normalized_text", "changed normalized"),
            ("latex", r"x^2"),
            ("warning_codes", ["TEACHER_NOTE"]),
        ):
            setattr(revision, field, value)
            db.commit()
            current = _current_evidence(
                db,
                owner_id=rows["owner"].id,
                submission=rows["submission"],
                answer=rows["answer"],
            )[1][0]["revision"]["content_hash"]
            assert previous != current
            previous = current
    finally:
        db.close()


def test_duplicate_or_old_job_recognition_source_fails_closed() -> None:
    db, rows = _evidence_fixture()
    try:
        evidence = rows["evidence"]
        assert isinstance(evidence, QuestionRecognitionEvidence)
        evidence.block_sources = [*evidence.block_sources, *evidence.block_sources]
        db.commit()
        with pytest.raises(ProcessingInputError, match="unique") as duplicate:
            _current_evidence(
                db,
                owner_id=rows["owner"].id,
                submission=rows["submission"],
                answer=rows["answer"],
            )
        assert duplicate.value.code == "PROCESSING_INPUT_STALE"

        evidence.block_sources = evidence.block_sources[:1]
        owner = rows["owner"]
        submission = rows["submission"]
        db.add(
            SubmissionRecognitionJob(
                owner_id=owner.id,
                submission_id=submission.id,
                status="failed",
                provider="fake",
                provider_version="v2",
                idempotency_key=str(uuid.uuid4()),
                provider_kind="printed_text",
                config_version="config-v2",
                generation=2,
            )
        )
        db.commit()
        with pytest.raises(ProcessingInputError) as stale:
            _current_evidence(
                db,
                owner_id=owner.id,
                submission=submission,
                answer=rows["answer"],
            )
        assert stale.value.code == "PROCESSING_INPUT_STALE"
    finally:
        db.close()


def test_answer_confirmation_generation_is_independent_of_block_revisions() -> None:
    db, rows = _evidence_fixture()
    try:
        owner = rows["owner"]
        submission = rows["submission"]
        answer = rows["answer"]
        page = rows["page"]
        job = rows["job"]
        block = rows["block"]
        evidence = rows["evidence"]
        assert isinstance(block, SubmissionRecognitionBlock)
        assert isinstance(evidence, QuestionRecognitionEvidence)
        db.add(
            RecognitionRevision(
                recognition_block_id=block.id,
                revision=2,
                source="teacher",
                raw_text="raw-v2",
                normalized_text="answer-v2",
                latex=None,
                warning_codes=[],
                base_recognition_version=block.recognition_version,
                confirmed=True,
            )
        )
        region = rows["region"]
        assert isinstance(region, StudentAnswerRegion)
        image = RegionEvidenceImage(
            owner_id=owner.id,
            submission_id=submission.id,
            submission_page_id=page.id,
            student_answer_region_id=region.id,
            source_kind="original",
            object_key=f"synthetic/evidence/{uuid.uuid4()}",
            content_hash="8" * 64,
            input_hash="9" * 64,
            width=10,
            height=10,
            margin_pixels=0,
            source_page_number=1,
            region_order=1,
            page_version=page.page_version,
            region_version=region.region_version,
            processing_config_version="config-v1",
            status="ready",
        )
        db.add(image)
        db.flush()
        second_block = SubmissionRecognitionBlock(
            submission_recognition_job_id=job.id,
            submission_page_id=page.id,
            block_index=1,
            text="second",
            normalized_text="second",
            confidence=Decimal("1"),
            status="confirmed",
            x=Decimal("0"),
            y=Decimal("0"),
            width=Decimal("0.5"),
            height=Decimal("1"),
            provider="fake",
            provider_version="v1",
            student_answer_region_id=region.id,
            region_evidence_image_id=image.id,
            source_page_number=1,
            reading_order=1,
            recognition_version=1,
            input_hash=image.input_hash,
            output_hash="a" * 64,
            confirmed_by=owner.id,
            confirmed_at=now_utc(),
            requires_review=False,
        )
        db.add(second_block)
        db.flush()
        db.add(
            RecognitionRevision(
                recognition_block_id=second_block.id,
                revision=3,
                source="teacher",
                raw_text="second",
                normalized_text="second",
                latex=None,
                warning_codes=[],
                base_recognition_version=1,
                confirmed=True,
            )
        )
        evidence.block_sources = [
            *evidence.block_sources,
            {
                "region_id": str(region.id),
                "region_version": region.region_version,
                "block_id": str(second_block.id),
                "block_recognition_version": second_block.recognition_version,
                "block_recognition_job_id": str(job.id),
            },
        ]
        flag_modified(evidence, "block_sources")
        db.commit()

        current = _current_evidence(
            db,
            owner_id=owner.id,
            submission=submission,
            answer=answer,
        )
        assert evidence.confirmed_revision == 1
        assert [item["revision"]["revision"] for item in current[1]] == [2, 3]

        db.add(
            RecognitionRevision(
                recognition_block_id=second_block.id,
                revision=4,
                source="teacher",
                raw_text="unconfirmed",
                normalized_text="unconfirmed",
                latex=None,
                warning_codes=[],
                base_recognition_version=1,
                confirmed=False,
            )
        )
        db.commit()
        with pytest.raises(ProcessingInputError) as stale:
            _current_evidence(
                db,
                owner_id=owner.id,
                submission=submission,
                answer=answer,
            )
        assert stale.value.code == "PROCESSING_INPUT_STALE"
    finally:
        db.close()


def test_region_history_is_ignored_but_pending_segmentation_blocks() -> None:
    db, rows = _evidence_fixture()
    try:
        answer = rows["answer"]
        page = rows["page"]
        owner = rows["owner"]
        rejected = StudentAnswerRegion(
            student_answer_id=answer.id,
            submission_page_id=page.id,
            x=Decimal("0"),
            y=Decimal("0"),
            width=Decimal("0.25"),
            height=Decimal("1"),
            status="rejected",
        )
        db.add(rejected)
        db.commit()
        _current_evidence(
            db,
            owner_id=owner.id,
            submission=rows["submission"],
            answer=answer,
        )
        pending = StudentAnswerRegion(
            student_answer_id=answer.id,
            submission_page_id=page.id,
            x=Decimal("0.25"),
            y=Decimal("0"),
            width=Decimal("0.25"),
            height=Decimal("1"),
            status="pending",
        )
        db.add(pending)
        db.commit()
        with pytest.raises(ProcessingInputError) as blocked:
            _current_evidence(
                db,
                owner_id=owner.id,
                submission=rows["submission"],
                answer=answer,
            )
        assert blocked.value.code == "SEGMENTATION_CONFIRMATION_REQUIRED"
    finally:
        db.close()


def test_postgresql_synthetic_snapshot_drift_matrix_rolls_back() -> None:
    url = os.getenv("PROCESSING_SNAPSHOT_PG_URL")
    if not url:
        pytest.skip("isolated PostgreSQL URL not configured")
    engine = create_engine(url)
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, expire_on_commit=False)
    evidence_id: uuid.UUID | None = None
    candidate_id: uuid.UUID | None = None
    try:
        row = db.execute(
            select(
                QuestionRecognitionEvidence,
                StudentAnswer,
                Submission,
                GradingBatch,
                Assignment,
            )
            .join(
                StudentAnswer,
                StudentAnswer.id == QuestionRecognitionEvidence.student_answer_id,
            )
            .join(Submission, Submission.id == StudentAnswer.submission_id)
            .join(GradingBatch, GradingBatch.id == Submission.grading_batch_id)
            .join(Assignment, Assignment.id == Submission.assignment_id)
            .where(
                QuestionRecognitionEvidence.status == "recognized",
                QuestionRecognitionEvidence.stale_at.is_(None),
                Assignment.status == AssignmentStatus.published,
            )
            .order_by(QuestionRecognitionEvidence.created_at.desc())
        ).first()
        assert row is not None
        evidence, answer, submission, batch, assignment = row
        evidence_id = evidence.id
        source = evidence.block_sources[0]
        block = db.get(SubmissionRecognitionBlock, uuid.UUID(source["block_id"]))
        assert block is not None
        revision = db.scalar(
            select(RecognitionRevision)
            .where(RecognitionRevision.recognition_block_id == block.id)
            .order_by(RecognitionRevision.revision.desc(), RecognitionRevision.id)
        )
        region = db.get(StudentAnswerRegion, block.student_answer_region_id)
        image = db.get(RegionEvidenceImage, block.region_evidence_image_id)
        assert revision is not None and region is not None and image is not None

        evidence.status = "confirmed"
        evidence.requires_review = False
        evidence.confirmed_revision = revision.revision
        block.status = "confirmed"
        block.requires_review = False
        revision.confirmed = True
        revision.stale_at = None
        region.status = "confirmed"
        image.status = "ready"
        image.stale_at = None
        db.flush()

        first = build_processing_input_snapshot(
            db,
            owner_id=assignment.owner_id,
            grading_batch_id=batch.id,
            submission_id=submission.id,
            answer_id=answer.id,
        )
        second = build_processing_input_snapshot(
            db,
            owner_id=assignment.owner_id,
            grading_batch_id=batch.id,
            submission_id=submission.id,
            answer_id=answer.id,
        )
        assert first == second

        original_warnings = list(revision.warning_codes)
        revision.warning_codes = [*original_warnings, "PG_DRIFT"]
        flag_modified(revision, "warning_codes")
        db.flush()
        drifted = build_processing_input_snapshot(
            db,
            owner_id=assignment.owner_id,
            grading_batch_id=batch.id,
            submission_id=submission.id,
            answer_id=answer.id,
        )
        assert drifted.input_version != first.input_version
        revision.warning_codes = original_warnings
        flag_modified(revision, "warning_codes")
        db.flush()

        review_session = db.scalar(
            select(AssignmentReviewSession).where(
                AssignmentReviewSession.assignment_id == assignment.id,
                AssignmentReviewSession.status == "published",
            )
        )
        assert review_session is not None
        next_candidate_version = (
            db.scalar(
                select(func.max(AssignmentAnswerDraftCandidate.candidate_version)).where(
                    AssignmentAnswerDraftCandidate.draft_revision_id
                    == review_session.draft_revision_id,
                    AssignmentAnswerDraftCandidate.question_id == answer.question_id,
                )
            )
            or 0
        ) + 1
        candidate = AssignmentAnswerDraftCandidate(
            owner_id=assignment.owner_id,
            assignment_id=assignment.id,
            generation_job_id=review_session.generation_job_id,
            draft_revision_id=review_session.draft_revision_id,
            question_id=answer.question_id,
            question_version="candidate-only",
            candidate_version=next_candidate_version,
            source_type="ai_generated",
            source_region={},
            raw_content="candidate",
            normalized_content="candidate",
            structured_content={},
            alternative_answers=[],
            provenance={"synthetic_marker": "phase3b-pg-rollback"},
            confidence=Decimal("1"),
            evidence=[],
            warning_codes=[],
            status="suggested",
            manual_required=False,
            teacher_edit_version=0,
            source_snapshot_hash="c" * 64,
        )
        db.add(candidate)
        db.flush()
        candidate_id = candidate.id
        assert db.get(AssignmentAnswerDraftCandidate, candidate_id) is not None
        with_candidate = build_processing_input_snapshot(
            db,
            owner_id=assignment.owner_id,
            grading_batch_id=batch.id,
            submission_id=submission.id,
            answer_id=answer.id,
        )
        assert with_candidate.input_version == first.input_version
        candidate.normalized_content = "candidate changed"
        candidate.warning_codes = ["CANDIDATE_ONLY"]
        flag_modified(candidate, "warning_codes")
        db.flush()
        unchanged = build_processing_input_snapshot(
            db,
            owner_id=assignment.owner_id,
            grading_batch_id=batch.id,
            submission_id=submission.id,
            answer_id=answer.id,
        )
        assert unchanged.input_version == first.input_version

        original_sources = list(evidence.block_sources)
        evidence.block_sources = [*original_sources, *original_sources]
        flag_modified(evidence, "block_sources")
        db.flush()
        with pytest.raises(ProcessingInputError) as duplicate:
            build_processing_input_snapshot(
                db,
                owner_id=assignment.owner_id,
                grading_batch_id=batch.id,
                submission_id=submission.id,
                answer_id=answer.id,
            )
        assert duplicate.value.code == "PROCESSING_INPUT_STALE"
        evidence.block_sources = original_sources
        flag_modified(evidence, "block_sources")
        db.flush()

        cross_scope_sources = [dict(item) for item in original_sources]
        cross_scope_sources[0]["block_recognition_job_id"] = str(uuid.uuid4())
        evidence.block_sources = cross_scope_sources
        flag_modified(evidence, "block_sources")
        db.flush()
        with pytest.raises(ProcessingInputError) as cross_scope:
            build_processing_input_snapshot(
                db,
                owner_id=assignment.owner_id,
                grading_batch_id=batch.id,
                submission_id=submission.id,
                answer_id=answer.id,
            )
        assert cross_scope.value.code == "PROCESSING_INPUT_STALE"
        evidence.block_sources = original_sources
        flag_modified(evidence, "block_sources")
        db.flush()

        binding = db.scalar(
            select(AssignmentRubricPublicationBinding).where(
                AssignmentRubricPublicationBinding.assignment_id == assignment.id,
                AssignmentRubricPublicationBinding.invalidated_at.is_(None),
            )
        )
        assert binding is not None
        original_profile = binding.projection_profile
        binding.projection_profile = "legacy-unverified"
        db.flush()
        with pytest.raises(ProcessingInputError) as legacy:
            build_processing_input_snapshot(
                db,
                owner_id=assignment.owner_id,
                grading_batch_id=batch.id,
                submission_id=submission.id,
                answer_id=answer.id,
            )
        assert legacy.value.code == "LEGACY_PROJECTION_STALE"
        binding.projection_profile = original_profile
        db.flush()
    finally:
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()

    assert evidence_id is not None and candidate_id is not None
    verify_engine = create_engine(url)
    with Session(verify_engine) as verify:
        assert verify.get(QuestionRecognitionEvidence, evidence_id).status == "recognized"
        assert verify.get(AssignmentAnswerDraftCandidate, candidate_id) is None
    verify_engine.dispose()
