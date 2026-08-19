import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from app.api.assignment_generation import (
    QuestionExtractionDispositionInput,
    dispatch_job,
    disposition_question_extraction,
)
from app.api.domain import ApiProblem
from app.assignment_generation.extraction_stage import build_fake_candidates
from app.assignment_generation.materializers import ProviderSemanticError, materialize_questions
from app.assignment_generation.providers import select_provider
from app.assignment_generation.question_extraction import ExtractionOutput
from app.assignment_generation.reference_bindings import build_reference_answer_bindings
from app.assignment_generation.service import (
    create_job,
    ensure_current,
    transition,
)
from app.assignment_generation.snapshot import canonical_hash, canonical_json, source_snapshot_hash
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AssignmentPageAnalysis,
    AssignmentQuestionExtractionCandidate,
    AssignmentQuestionExtractionRegion,
    AssignmentRubricDraftCandidate,
    AssignmentSourceFileAnalysis,
    AuditLog,
    GenerationStageResult,
    PaperPage,
    PaperVersion,
    Question,
    QuestionCandidate,
    QuestionCandidateRegion,
    RecognitionBlock,
    RecognitionJob,
    RecognitionStatus,
    ReferenceAnswerSourceBinding,
    ReferenceAnswerSourceRegion,
    ReferenceAnswerVersion,
    StoredFile,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

from workers.tasks.assignment_generation import _guarded_run, _run

client = TestClient(app)


def extraction_candidate_payload(page_id: uuid.UUID, ref: str, text: str) -> dict[str, object]:
    return {
        "ref": ref,
        "question_number": ref,
        "question_type": "calculation",
        "content_text": text,
        "content_latex": None,
        "max_score": "5",
        "difficulty": None,
        "knowledge_points": ["合成知识点"],
        "field_confidences": {
            key: "0.9"
            for key in (
                "question_number",
                "parent_relation",
                "question_type",
                "content_text",
                "content_latex",
                "max_score",
                "difficulty",
                "knowledge_points",
                "regions",
            )
        },
        "overall_confidence": "0.9",
        "evidence": {},
        "warning_codes": [],
        "manual_required": True,
        "regions": [
            {
                "page_id": str(page_id),
                "display_order": 0,
                "region_type": "stem",
                "x": "0",
                "y": "0",
                "width": "1",
                "height": "1",
                "confidence": "0.9",
            }
        ],
    }


def actor_and_assignment() -> tuple[User, Assignment]:
    client.get("/api/classes")
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
        assert actor is not None
        assignment = Assignment(owner_id=actor.id, title="编排测试")
        db.add(assignment)
        db.commit()
        db.refresh(actor)
        db.refresh(assignment)
        db.expunge(actor)
        db.expunge(assignment)
        return actor, assignment


def test_question_extraction_requires_teacher_confirmed_question_role() -> None:
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        job, revision, _ = create_job(
            db,
            actor.id,
            assignment.id,
            f"teacher-role-{uuid.uuid4()}",
            "fake",
            None,
        )
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=f"tests/{uuid.uuid4()}.pdf",
            original_name="看似试卷但实际用途未确认.pdf",
            content_type="application/pdf",
            size=10,
            checksum="a" * 64,
            status="ready",
        )
        db.add(stored)
        db.flush()
        source_analysis = AssignmentSourceFileAnalysis(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            stored_file_id=stored.id,
            detected_mime_type="application/pdf",
            checksum=stored.checksum,
            page_count=1,
            content_mode="text",
            text_source="pdf_text",
            content_mode_confidence=1,
            suggested_role="question_paper",
            role_confidence=0.99,
            suggested_answer_source="not_applicable",
            answer_source_confidence=1,
            evidence=[],
            warning_codes=[],
            analysis_status="suggested",
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(source_analysis)
        db.flush()

        assert build_fake_candidates(db, job, revision) == {
            "created": 0,
            "blocked": "QUESTION_PAPER_ROLE_UNCONFIRMED",
        }


def test_local_extraction_accepts_legacy_blocks_and_requires_review_for_source_conflict() -> None:
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=f"tests/{uuid.uuid4()}.pdf",
            original_name="synthetic-mixed-source.pdf",
            content_type="application/pdf",
            size=10,
            checksum="b" * 64,
            status="ready",
        )
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status="draft",
            source_type="manual",
            created_by=actor.id,
        )
        db.add_all([stored, paper])
        db.flush()
        page = PaperPage(
            paper_version_id=paper.id,
            stored_file_id=stored.id,
            page_number=1,
            source_page_number=1,
            status="ready",
        )
        db.add(page)
        assignment_row = db.get(Assignment, assignment.id)
        assert assignment_row is not None
        assignment_row.active_paper_version_id = paper.id
        db.flush()
        job, revision, _ = create_job(
            db,
            actor.id,
            assignment.id,
            f"legacy-mixed-{uuid.uuid4()}",
            "unavailable",
            None,
        )
        source_analysis = AssignmentSourceFileAnalysis(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            stored_file_id=stored.id,
            detected_mime_type="application/pdf",
            checksum=stored.checksum,
            page_count=1,
            content_mode="mixed",
            text_source="mixed",
            content_mode_confidence=0.8,
            suggested_role="question_paper",
            role_confidence=1,
            suggested_answer_source="not_applicable",
            answer_source_confidence=1,
            analysis_status="confirmed",
            teacher_confirmed_role="question_paper",
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(source_analysis)
        recognition = RecognitionJob(
            owner_id=actor.id,
            paper_version_id=paper.id,
            assignment_id=assignment.id,
            status=RecognitionStatus.completed,
            stage="completed",
            progress=100,
            provider="rapidocr",
            provider_version="synthetic",
            config_version="test",
            idempotency_key=f"legacy-recognition-{uuid.uuid4()}",
        )
        db.add(recognition)
        db.flush()
        candidate = QuestionCandidate(
            recognition_job_id=recognition.id,
            paper_version_id=paper.id,
            temporary_number="1",
            content_text="1. Synthetic mixed source question",
            confidence=0.9,
            source="mixed:conservative_fusion",
        )
        db.add(candidate)
        db.flush()
        db.add(
            QuestionCandidateRegion(
                question_candidate_id=candidate.id,
                paper_page_id=page.id,
                x=0,
                y=0,
                width=1,
                height=0.5,
                confidence=0.9,
            )
        )
        db.add_all(
            [
                RecognitionBlock(
                    recognition_job_id=recognition.id,
                    paper_page_id=page.id,
                    block_type="question_number",
                    display_order=1,
                    text="1.",
                    confidence=1,
                    x=0.1,
                    y=0.1,
                    width=0.1,
                    height=0.05,
                    source="pdf_text:pypdfium2",
                    status="recognized",
                ),
                RecognitionBlock(
                    recognition_job_id=recognition.id,
                    paper_page_id=page.id,
                    block_type="text",
                    display_order=2,
                    text="legacy adopted text",
                    confidence=0.65,
                    x=0.1,
                    y=0.45,
                    width=0.5,
                    height=0.1,
                    source="rapidocr:synthetic",
                    status="low_confidence",
                ),
                RecognitionBlock(
                    recognition_job_id=recognition.id,
                    paper_page_id=page.id,
                    block_type="text",
                    display_order=3,
                    text="conflicting evidence",
                    confidence=0.8,
                    x=0.1,
                    y=0.2,
                    width=0.5,
                    height=0.05,
                    source="rapidocr:synthetic",
                    status="source_conflict",
                ),
            ]
        )
        db.add(
            AssignmentPageAnalysis(
                owner_id=actor.id,
                assignment_id=assignment.id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                paper_page_id=page.id,
                source_file_analysis_id=source_analysis.id,
                source_snapshot_hash=job.source_snapshot_hash,
                status="low_quality",
                content_mode="mixed",
                text_source="mixed",
                content_mode_confidence=0.8,
                text_character_count=100,
                quality_score=0.2,
                low_quality=True,
                metrics={
                    "page_quality": {
                        "level": "rescan_required",
                        "issues": ["blur", "crop_risk", "internal_metric_key"],
                    },
                    "math_structure": {
                        "risk_codes": ["FORMULA_REVIEW_REQUIRED"],
                        "evidence": [{"block_indexes": [0, 1], "region": [0, 0, 1, 1]}],
                    },
                    "source_conflicts": {"count": 1, "math_symbol_count": 0},
                },
                warning_codes=["PAGE_QUALITY_RESCAN_REQUIRED"],
            )
        )
        db.flush()

        result = build_fake_candidates(db, job, revision)
        assert result["created"] == 1
        extracted = db.scalar(
            select(AssignmentQuestionExtractionCandidate).where(
                AssignmentQuestionExtractionCandidate.source_question_candidate_id == candidate.id
            )
        )
        assert extracted is not None
        assert extracted.extraction_method == "mixed_text_anchor"
        assert extracted.manual_required is True
        assert "SOURCE_TEXT_CONFLICT_REVIEW_REQUIRED" in extracted.warning_codes
        assert "MIXED_TEXT_SOURCE_REVIEW_REQUIRED" in extracted.warning_codes
        assert "PAGE_QUALITY_RESCAN_REQUIRED" in extracted.warning_codes
        assert "FORMULA_REVIEW_REQUIRED" in extracted.warning_codes
        assert "OCR_TEXT_LOW_CONFIDENCE_REVIEW_REQUIRED" in extracted.warning_codes
        assert "MATH_SYMBOL_SOURCE_CONFLICT" not in extracted.warning_codes
        assert extracted.evidence["page_quality"] == {
            str(page.id): {
                "level": "rescan_required",
                "issues": ["blur", "crop_risk"],
            }
        }
        assert len(extracted.evidence["recognition_block_ids"]) == 2
        assert len(extracted.evidence["source_conflict_block_ids"]) == 1
        with pytest.raises(ApiProblem) as exc_info:
            disposition_question_extraction(
                extracted.id,
                QuestionExtractionDispositionInput(
                    action="accept",
                    expected_teacher_edit_version=0,
                    expected_draft_revision_edit_version=revision.teacher_edit_version,
                    expected_paper_version_id=paper.id,
                    expected_source_snapshot=job.source_snapshot_hash,
                ),
                db,
                actor,
            )
        assert exc_info.value.code == "RECOGNITION_PAGE_RESCAN_REQUIRED"

        analysis = db.scalar(
            select(AssignmentPageAnalysis).where(
                AssignmentPageAnalysis.draft_revision_id == revision.id,
                AssignmentPageAnalysis.paper_page_id == page.id,
            )
        )
        assert analysis is not None
        analysis.metrics = {
            **analysis.metrics,
            "source_conflicts": {"count": 1, "math_symbol_count": 1},
        }
        db.flush()
        assert build_fake_candidates(db, job, revision)["created"] == 1
        math_conflict = db.scalar(
            select(AssignmentQuestionExtractionCandidate)
            .where(
                AssignmentQuestionExtractionCandidate.source_question_candidate_id == candidate.id,
                AssignmentQuestionExtractionCandidate.status == "suggested",
            )
            .order_by(AssignmentQuestionExtractionCandidate.candidate_version.desc())
        )
        assert math_conflict is not None
        assert "MATH_SYMBOL_SOURCE_CONFLICT" in math_conflict.warning_codes


def start(aid: uuid.UUID, key: str = "generation-key-0001"):
    return client.post(
        f"/api/assignments/{aid}/generation-jobs",
        json={"idempotency_key": key, "provider_mode": "unavailable"},
    )


def test_materialize_questions_corrupt_batch_is_zero_write_and_preserves_old_candidate() -> None:
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=f"integrity/{uuid.uuid4()}.pdf",
            original_name="synthetic-integrity.pdf",
            content_type="application/pdf",
            size=128,
            checksum="f" * 64,
            status="ready",
        )
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status="draft",
            source_type="manual",
            created_by=actor.id,
        )
        db.add_all([stored, paper])
        db.flush()
        page = PaperPage(
            paper_version_id=paper.id,
            stored_file_id=stored.id,
            page_number=1,
            source_page_number=1,
            rotation=0,
            status="ready",
        )
        db.add(page)
        assignment_row = db.get(Assignment, assignment.id)
        assert assignment_row is not None
        assignment_row.active_paper_version_id = paper.id
        db.flush()
        job, revision, _ = create_job(
            db,
            actor.id,
            assignment.id,
            f"integrity-{uuid.uuid4()}",
            "codex_local",
            None,
        )
        old = AssignmentQuestionExtractionCandidate(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            paper_version_id=paper.id,
            candidate_version=1,
            question_number="old",
            question_type="other",
            content_text="保留的旧候选",
            max_score=None,
            field_confidences={},
            overall_confidence=1,
            extraction_method="codex_local",
            evidence={},
            warning_codes=[],
            status="manual_required",
            manual_required=True,
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(old)
        db.flush()

        output = ExtractionOutput.model_validate(
            {
                "candidates": [
                    extraction_candidate_payload(page.id, "1", "第一道完整合成题"),
                    extraction_candidate_payload(page.id, "2", "第二道完整合成题"),
                ]
            }
        )
        output.candidates[1].content_text = "???? x?+xy+y?=7"

        with pytest.raises(ProviderSemanticError, match="CHARACTER_ENCODING_CORRUPTION_DETECTED"):
            materialize_questions(db, job, revision, output)

        db.flush()
        db.refresh(old)
        assert old.status == "manual_required"
        rows = list(
            db.scalars(
                select(AssignmentQuestionExtractionCandidate).where(
                    AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id
                )
            )
        )
        assert [row.id for row in rows] == [old.id]

        output.candidates[1].content_text = "第二道完整合成题"
        assert materialize_questions(db, job, revision, output) == {
            "created": 2,
            "manual_required": 2,
        }
        created_rows = list(
            db.scalars(
                select(AssignmentQuestionExtractionCandidate).where(
                    AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
                    AssignmentQuestionExtractionCandidate.candidate_version == 2,
                )
            )
        )
        assert len(created_rows) == 2
        assert all(row.evidence["quality_stats"]["character_count"] > 0 for row in created_rows)
        assert all(
            row.evidence["quality_stats"]["suspicious_character_count"] == 0 for row in created_rows
        )
        revision_id = revision.id
        db.commit()

    listed = client.get(
        f"/api/assignment-draft-revisions/{revision_id}/question-extraction-candidates"
    )
    assert listed.status_code == 200
    latest = [item for item in listed.json() if item["candidate_version"] == 2]
    assert len(latest) == 2
    assert all(item["quality_stats"]["character_count"] > 0 for item in latest)
    assert all(item["quality_stats"]["suspicious_character_count"] == 0 for item in latest)


def test_create_idempotency_concurrency_and_new_generation(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    first = start(assignment.id)
    assert first.status_code == 201
    assert first.json()["generation"] == 1
    assert first.json()["revision"]["revision"] == 1
    assert first.json()["status"] == "queued"

    repeated = start(assignment.id)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["reused"] is True

    concurrent = start(assignment.id, "generation-key-0002")
    assert concurrent.status_code == 409
    assert concurrent.json()["code"] == "GENERATION_ALREADY_ACTIVE"

    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(first.json()["id"]))
        assert job is not None
        job.status = "partial"
        job.progress = 100
        db.commit()
    second = start(assignment.id, "generation-key-0003")
    assert second.status_code == 201
    assert second.json()["generation"] == 2
    assert second.json()["revision"]["revision"] == 2
    assert second.json()["revision"]["parent_revision_id"] == first.json()["revision"]["id"]
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(AuditLog).where(
                    AuditLog.actor_id == actor.id,
                    AuditLog.action == "assignment_generation.create",
                )
            )
            is not None
        )


def test_true_concurrent_generation_start_has_one_active_winner(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    barrier = Barrier(2)

    def submit(key: str) -> tuple[int, str | None]:
        barrier.wait()
        with TestClient(app) as concurrent_client:
            response = concurrent_client.post(
                f"/api/assignments/{assignment.id}/generation-jobs",
                json={"idempotency_key": key, "provider_mode": "unavailable"},
            )
            return response.status_code, response.json().get("code")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("concurrent-key-0001", "concurrent-key-0002")))
    assert sorted(status for status, _code in outcomes) == [201, 409]
    assert next(code for status, code in outcomes if status == 409) in {
        "GENERATION_ALREADY_ACTIVE",
        "GENERATION_CONCURRENT_CONFLICT",
    }
    with SessionLocal() as db:
        active = db.scalars(
            select(AssignmentGenerationJob).where(
                AssignmentGenerationJob.assignment_id == assignment.id,
                AssignmentGenerationJob.status == "queued",
            )
        ).all()
        assert len(active) == 1


def test_reference_answer_pdf_anchors_bind_by_number_and_require_teacher_confirmation() -> None:
    actor, assignment = actor_and_assignment()
    snapshot = "b" * 64
    with SessionLocal() as db:
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            source_type="pdf",
            created_by=actor.id,
        )
        source_file = StoredFile(
            owner_id=actor.id,
            storage_key=f"synthetic/reference-{uuid.uuid4()}.pdf",
            original_name="synthetic-reference.pdf",
            content_type="application/pdf",
            size=128,
            checksum="c" * 64,
        )
        job = AssignmentGenerationJob(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation=1,
            status="partial",
            progress=100,
            idempotency_key=f"reference-binding-{uuid.uuid4()}",
            request_fingerprint="d" * 64,
            source_snapshot_hash=snapshot,
            provider_mode="fake",
            provider_config_version="test",
            prompt_version="test",
            schema_version="test",
        )
        db.add_all([paper, source_file, job])
        db.flush()
        revision = AssignmentDraftRevision(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            revision=1,
            source_snapshot_hash=snapshot,
            status="review_required",
        )
        db.add(revision)
        db.flush()
        item = db.get(Assignment, assignment.id)
        assert item is not None
        item.active_paper_version_id = paper.id
        analysis = AssignmentSourceFileAnalysis(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            stored_file_id=source_file.id,
            source_snapshot_hash=snapshot,
            detected_mime_type="application/pdf",
            checksum=source_file.checksum,
            page_count=2,
            content_mode="text",
            text_source="pdf_text",
            content_mode_confidence=1,
            suggested_role="reference_answer",
            role_confidence=1,
            suggested_answer_source="teacher_provided",
            answer_source_confidence=1,
            analysis_status="confirmed",
            teacher_confirmed_role="reference_answer",
            teacher_confirmed_answer_source="teacher_provided",
            confirmed_by=actor.id,
        )
        pages = [
            PaperPage(
                paper_version_id=paper.id,
                stored_file_id=source_file.id,
                page_number=number,
                source_page_number=number,
                status="ready",
            )
            for number in (1, 2)
        ]
        questions = [
            Question(
                paper_version_id=paper.id,
                question_number=number,
                display_order=index,
                question_type="calculation",
                max_score=10,
                status="active",
                source="pdf_text",
            )
            for index, number in enumerate(("2(3)", "2(5)", "12(2)"), start=1)
        ]
        db.add_all([analysis, *pages, *questions])
        db.flush()
        recognition = RecognitionJob(
            owner_id=actor.id,
            paper_version_id=paper.id,
            assignment_id=assignment.id,
            status=RecognitionStatus.completed,
            stage="completed",
            progress=100,
            provider="pdf_text",
            provider_version="test",
            config_version="test",
            idempotency_key=f"reference-recognition-{uuid.uuid4()}",
        )
        db.add(recognition)
        db.flush()
        stale_recognition = RecognitionJob(
            owner_id=actor.id,
            paper_version_id=paper.id,
            assignment_id=assignment.id,
            status=RecognitionStatus.failed,
            stage="failed",
            progress=100,
            provider="pdf_text",
            provider_version="stale-test",
            config_version="test",
            idempotency_key=f"stale-reference-recognition-{uuid.uuid4()}",
        )
        db.add(stale_recognition)
        db.flush()
        blocks = [
            RecognitionBlock(
                recognition_job_id=recognition.id,
                paper_page_id=page.id,
                block_type="question_number",
                display_order=index,
                text=number,
                confidence=0.99,
                x=0.05,
                y=y,
                width=0.1,
                height=0.03,
                source="pdf_text:pypdfium2",
            )
            for index, (page, number, y) in enumerate(
                (
                    (pages[0], "2（3）：", 0.7),
                    (pages[1], "2(5)", 0.2),
                    (pages[1], "12（2）", 0.6),
                )
            )
        ]
        answer_blocks = [
            RecognitionBlock(
                recognition_job_id=recognition.id,
                paper_page_id=pages[0].id,
                block_type="text",
                display_order=10,
                text="first step",
                confidence=0.98,
                x=0.2,
                y=0.78,
                width=0.6,
                height=0.03,
                source="pdf_text:pypdfium2",
            ),
            RecognitionBlock(
                recognition_job_id=recognition.id,
                paper_page_id=pages[1].id,
                block_type="text",
                display_order=11,
                text="continued result",
                confidence=0.97,
                x=0.2,
                y=0.05,
                width=0.6,
                height=0.03,
                source="pdf_text:pypdfium2",
            ),
            RecognitionBlock(
                recognition_job_id=stale_recognition.id,
                paper_page_id=pages[0].id,
                block_type="text",
                display_order=9,
                text="stale failed-job text",
                confidence=0.99,
                x=0.2,
                y=0.75,
                width=0.6,
                height=0.02,
                source="pdf_text:pypdfium2",
            ),
        ]
        db.add_all([*blocks, *answer_blocks])
        db.flush()

        snapshot = source_snapshot_hash(db, item)
        job.source_snapshot_hash = snapshot
        revision.source_snapshot_hash = snapshot
        analysis.source_snapshot_hash = snapshot

        result = build_reference_answer_bindings(db, job, revision)
        db.commit()
        revision_id = revision.id
        paper_id = paper.id
        question_id = questions[0].id

        assert result == {"created": 3, "manual_required": 0, "binding_version": 1}
        bindings = list(
            db.scalars(
                select(ReferenceAnswerSourceBinding).order_by(
                    ReferenceAnswerSourceBinding.detected_number
                )
            )
        )
        by_number = {binding.detected_number: binding for binding in bindings}
        first_regions = list(
            db.scalars(
                select(ReferenceAnswerSourceRegion)
                .where(ReferenceAnswerSourceRegion.binding_id == by_number["2(3)"].id)
                .order_by(ReferenceAnswerSourceRegion.display_order)
            )
        )
        assert by_number["2(3)"].question_id == question_id
        assert [region.paper_page_id for region in first_regions] == [pages[0].id, pages[1].id]
        assert [float(region.height) for region in first_regions] == [0.3, 0.2]
        binding_id = by_number["2(3)"].id

    listed = client.get(f"/api/draft-revisions/{revision_id}/reference-answer-bindings")
    assert listed.status_code == 200
    assert len(listed.json()) == 3
    payload = {
        "action": "confirm",
        "expected_edit_version": 0,
        "expected_draft_revision_edit_version": 0,
        "expected_paper_version_id": str(paper_id),
        "expected_source_snapshot": snapshot,
        "question_id": str(question_id),
    }
    missing_confirmation = client.post(
        f"/api/reference-answer-bindings/{binding_id}/disposition", json=payload
    )
    assert missing_confirmation.status_code == 422
    confirmed = client.post(
        f"/api/reference-answer-bindings/{binding_id}/disposition",
        json={**payload, "explicit_confirmation": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    extraction_payload = {
        "expected_binding_edit_version": 1,
        "expected_draft_revision_edit_version": 1,
        "expected_source_snapshot": snapshot,
    }
    extracted = client.post(
        f"/api/reference-answer-bindings/{binding_id}/extract-answer-candidate",
        json=extraction_payload,
    )
    assert extracted.status_code == 200
    body = extracted.json()
    assert body["raw_content"] == "first step\ncontinued result"
    assert body["normalized_content"] == "first step\ncontinued result"
    assert body["source_reference_binding_id"] == str(binding_id)
    assert body["status"] == "suggested"
    assert body["manual_required"] is True
    assert body["materialized_reference_answer_id"] is None
    repeated = client.post(
        f"/api/reference-answer-bindings/{binding_id}/extract-answer-candidate",
        json=extraction_payload,
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == body["id"]
    with SessionLocal() as db:
        assert db.scalar(select(ReferenceAnswerVersion.id)) is None
        assert db.scalar(select(AssignmentRubricDraftCandidate.id)) is None
        candidates = list(
            db.scalars(
                select(AssignmentAnswerDraftCandidate).where(
                    AssignmentAnswerDraftCandidate.source_reference_binding_id == binding_id
                )
            )
        )
        assert len(candidates) == 1


def test_idempotency_key_reused_for_changed_request_conflicts(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    assert start(assignment.id).status_code == 201
    with SessionLocal() as db:
        item = db.get(Assignment, assignment.id)
        assert item is not None
        item.title = "教师已修改输入"
        db.commit()
    conflict = start(assignment.id)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_idempotency_key_is_normalized_and_scoped_by_owner(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    created = start(assignment.id, "  normalized-key-0001  ")
    assert created.status_code == 201
    repeated = start(assignment.id, "normalized-key-0001")
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created.json()["id"]
    with SessionLocal() as db:
        other = User(
            email="idempotency-owner@example.com",
            password_hash="not-used",
            display_name="Other",
        )
        db.add(other)
        db.flush()
        other_assignment = Assignment(owner_id=other.id, title="Other assignment")
        db.add(other_assignment)
        db.flush()
        other_job, _revision, reused = create_job(
            db,
            other.id,
            other_assignment.id,
            "normalized-key-0001",
            "unavailable",
            None,
        )
        db.commit()
        assert reused is False
        assert other_job.owner_id != actor.id


def test_state_machine_rejects_illegal_transition():
    job = AssignmentGenerationJob(
        owner_id=uuid.uuid4(),
        assignment_id=uuid.uuid4(),
        generation=1,
        status="queued",
        progress=0,
        idempotency_key="state-machine",
        source_snapshot_hash="0" * 64,
        provider_config_version="v1",
        prompt_version="v1",
        schema_version="v1",
    )
    transition(job, "analyzing")
    assert (job.status, job.progress) == ("analyzing", 10)
    try:
        transition(job, "ready")
    except Exception as exc:
        assert getattr(exc, "code", None) == "GENERATION_INVALID_TRANSITION"
    else:
        raise AssertionError("illegal transition was accepted")


def test_cancel_queued_and_teacher_metadata_concurrency(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    revision = created["revision"]
    changed = client.patch(
        f"/api/assignment-draft-revisions/{revision['id']}/metadata",
        json={
            "expected_teacher_edit_version": 0,
            "label": "教师检查版",
            "notes": "<script>不会作为 HTML 渲染</script>",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["teacher_edit_version"] == 1
    stale_edit = client.patch(
        f"/api/assignment-draft-revisions/{revision['id']}/metadata",
        json={"expected_teacher_edit_version": 0, "label": "覆盖"},
    )
    assert stale_edit.status_code == 409
    assert stale_edit.json()["code"] == "DRAFT_MODIFIED_BY_TEACHER"
    cancelled = client.post(f"/api/assignment-generation-jobs/{created['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    with SessionLocal() as db:
        actions = set(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.resource_id.in_([created["id"], revision["id"]])
                )
            ).all()
        )
        assert "assignment_generation.cancel" in actions
        assert "assignment_draft.metadata_update" in actions


def test_true_concurrent_metadata_patch_allows_only_one_writer(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    revision_id = start(assignment.id).json()["revision"]["id"]
    barrier = Barrier(2)

    def patch(label: str) -> tuple[int, str | None]:
        barrier.wait()
        with TestClient(app) as concurrent_client:
            response = concurrent_client.patch(
                f"/api/assignment-draft-revisions/{revision_id}/metadata",
                json={"expected_teacher_edit_version": 0, "label": label},
            )
            return response.status_code, response.json().get("code")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(patch, ("writer-a", "writer-b")))
    assert sorted(status for status, _code in outcomes) == [200, 409]
    assert next(code for status, code in outcomes if status == 409) == "DRAFT_MODIFIED_BY_TEACHER"
    with SessionLocal() as db:
        revision = db.get(AssignmentDraftRevision, uuid.UUID(revision_id))
        assert revision is not None
        assert revision.teacher_edit_version == 1


def test_worker_unavailable_finishes_partial_with_audit_records(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    result = _run(created["id"], None)
    assert result["status"] == "partial"
    detail = client.get(f"/api/assignment-generation-jobs/{created['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "partial"
    assert body["progress"] == 100
    assert {row["stage"] for row in body["stages"]} == {
        "analyzing",
        "processing_pages",
        "extracting_questions",
        "generating_rubrics",
        "validating",
    }
    assert any(row["code"] == "PROVIDER_UNAVAILABLE" for row in body["issues"])
    assert any(row["code"] == "GENERATION_PARTIAL" for row in body["issues"])
    assert body["revision"]["risk_summary"]["blocking"] >= 1
    with SessionLocal() as db:
        assignment_row = db.get(Assignment, assignment.id)
        assert assignment_row is not None
        assert assignment_row.status == "draft"

    duplicate = _run(created["id"], None)
    assert duplicate["status"] == "duplicate_delivery"
    detail_after_duplicate = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    assert len(detail_after_duplicate["stages"]) == 5


def test_source_change_marks_stale_and_late_worker_cannot_restore(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    with SessionLocal() as db:
        item = db.get(Assignment, assignment.id)
        assert item is not None
        item.instructions = "教师新增要求"
        db.commit()
    assert _run(created["id"], None)["status"] == "source_changed"
    assert _run(created["id"], None)["status"] == "discarded_late"
    body = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    assert body["status"] == "stale"
    assert sum(row["code"] == "SOURCE_CHANGED" for row in body["issues"]) == 1


def test_retry_stage_appends_generation_and_keeps_history(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    _run(created["id"], None)
    first = [
        row
        for row in client.get(f"/api/assignment-generation-jobs/{created['id']}").json()["stages"]
        if row["stage"] == "extracting_questions"
    ][0]
    retried = client.post(
        f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
        json={"stage": "extracting_questions"},
    )
    assert retried.status_code == 200
    _run(created["id"], "extracting_questions")
    rows = [
        row
        for row in client.get(f"/api/assignment-generation-jobs/{created['id']}").json()["stages"]
        if row["stage"] == "extracting_questions"
    ]
    assert [row["stage_generation"] for row in rows] == [1, 2]
    assert rows[0]["id"] == first["id"]
    detail = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    assert detail["retryable"] is True
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        assert job is not None
        assert job.attempt == 1


def test_retry_stage_repairs_running_result_left_by_guarded_failure(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    job_id = uuid.UUID(created["id"])

    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, job_id)
        assert job is not None
        transition(job, "analyzing")
        transition(job, "processing_pages")
        transition(job, "extracting_questions")
        job.status = "failed"
        job.error_code = "STAGE_FAILED"
        job.retryable = True
        db.add(
            GenerationStageResult(
                job_id=job.id,
                stage="analyzing",
                stage_generation=1,
                status="completed",
                expected_teacher_edit_version=0,
                input_hash="a" * 64,
            )
        )
        db.add(
            GenerationStageResult(
                job_id=job.id,
                stage="processing_pages",
                stage_generation=1,
                status="completed",
                expected_teacher_edit_version=0,
                input_hash="b" * 64,
            )
        )
        db.add(
            GenerationStageResult(
                job_id=job.id,
                stage="extracting_questions",
                stage_generation=1,
                status="running",
                expected_teacher_edit_version=0,
                input_hash="c" * 64,
            )
        )
        db.commit()

    retried = client.post(
        f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
        json={"stage": "extracting_questions"},
    )
    assert retried.status_code == 200
    rows = [row for row in retried.json()["stages"] if row["stage"] == "extracting_questions"]
    assert [(row["stage_generation"], row["status"]) for row in rows] == [
        (1, "failed"),
        (2, "queued"),
    ]


def test_retry_budget_is_independent_for_sequential_stages(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    _run(created["id"], None)

    for stage in ("extracting_questions", "generating_rubrics"):
        retried = client.post(
            f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
            json={"stage": stage},
        )
        assert retried.status_code == 200
        assert _run(created["id"], stage)["status"] == "partial"

    detail = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    by_stage = {
        stage: [row["stage_generation"] for row in detail["stages"] if row["stage"] == stage]
        for stage in ("extracting_questions", "generating_rubrics")
    }
    assert by_stage == {
        "extracting_questions": [1, 2],
        "generating_rubrics": [1, 2],
    }
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        assert job is not None
        assert job.attempt == 1


def test_retry_budget_is_enforced_per_stage(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    _run(created["id"], None)
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        assert job is not None
        job.max_attempts = 2
        db.commit()

    first_retry = client.post(
        f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
        json={"stage": "extracting_questions"},
    )
    assert first_retry.status_code == 200
    _run(created["id"], "extracting_questions")

    exhausted = client.post(
        f"/api/assignment-generation-jobs/{created['id']}/retry-stage",
        json={"stage": "extracting_questions"},
    )
    assert exhausted.status_code == 409
    assert exhausted.json()["code"] == "GENERATION_MAX_ATTEMPTS_REACHED"


def test_worker_edit_version_guard_and_snapshot_stability(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        job, revision, _ = create_job(
            db, actor.id, assignment.id, "direct-create-key", "unavailable", None
        )
        db.commit()
        first = source_snapshot_hash(db, db.get(Assignment, assignment.id))
        second = source_snapshot_hash(db, db.get(Assignment, assignment.id))
        assert first == second
        expected = revision.teacher_edit_version
        revision.teacher_edit_version += 1
        db.commit()
        assert ensure_current(db, job, revision, expected) == "DRAFT_MODIFIED_BY_TEACHER"
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    assert canonical_hash({"a": 1}) == canonical_hash({"a": 1})


def test_snapshot_changes_for_file_page_and_config_inputs(monkeypatch):
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=f"snapshot/{uuid.uuid4()}",
            original_name="paper.pdf",
            content_type="application/pdf",
            size=128,
            checksum="a" * 64,
            status="ready",
        )
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status="draft",
            source_type="manual",
            created_by=actor.id,
        )
        db.add_all([stored, paper])
        db.flush()
        page = PaperPage(
            paper_version_id=paper.id,
            stored_file_id=stored.id,
            page_number=1,
            source_page_number=1,
            rotation=0,
            status="ready",
        )
        row = db.get(Assignment, assignment.id)
        assert row is not None
        row.active_paper_version_id = paper.id
        db.add(page)
        db.commit()
        baseline = source_snapshot_hash(db, row)

        stored.checksum = "b" * 64
        db.flush()
        assert source_snapshot_hash(db, row) != baseline
        stored.checksum = "a" * 64
        page.rotation = 90
        db.flush()
        assert source_snapshot_hash(db, row) != baseline
        page.rotation = 0
        db.flush()
        assert source_snapshot_hash(db, row) == baseline

        monkeypatch.setattr(
            "app.assignment_generation.snapshot.get_settings",
            lambda: SimpleNamespace(
                assignment_generation_provider_config_version="changed-provider-config",
                assignment_generation_prompt_version="assignment-generation-prompt-v1",
                assignment_generation_schema_version="assignment-generation-schema-v1",
            ),
        )
        assert source_snapshot_hash(db, row) != baseline


def test_teacher_replaces_question_regions_with_snapshot_and_version_guards(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=f"region-draft/{uuid.uuid4()}",
            original_name="synthetic.pdf",
            content_type="application/pdf",
            size=128,
            checksum="c" * 64,
            status="ready",
        )
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status="draft",
            source_type="manual",
            created_by=actor.id,
        )
        db.add_all([stored, paper])
        db.flush()
        pages = [
            PaperPage(
                paper_version_id=paper.id,
                stored_file_id=stored.id,
                page_number=index,
                source_page_number=index,
                rotation=0,
                status="ready",
            )
            for index in (1, 2)
        ]
        row = db.get(Assignment, assignment.id)
        assert row is not None
        row.active_paper_version_id = paper.id
        db.add_all(pages)
        db.flush()
        job, revision, _ = create_job(
            db, actor.id, assignment.id, f"region-edit-{uuid.uuid4()}", "unavailable", None
        )
        source_analysis = AssignmentSourceFileAnalysis(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            stored_file_id=stored.id,
            detected_mime_type="application/pdf",
            checksum=stored.checksum,
            page_count=2,
            content_mode="image",
            text_source="ocr",
            content_mode_confidence=1,
            suggested_role="question_paper",
            role_confidence=1,
            suggested_answer_source="not_applicable",
            answer_source_confidence=1,
            analysis_status="confirmed",
            teacher_confirmed_role="question_paper",
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(source_analysis)
        db.flush()
        db.add_all(
            [
                AssignmentPageAnalysis(
                    owner_id=actor.id,
                    assignment_id=assignment.id,
                    generation_job_id=job.id,
                    draft_revision_id=revision.id,
                    paper_page_id=pages[0].id,
                    source_file_analysis_id=source_analysis.id,
                    source_snapshot_hash=job.source_snapshot_hash,
                    status="ready",
                    content_mode="image",
                    text_source="ocr",
                    content_mode_confidence=1,
                    metrics={"page_quality": {"level": "good", "issues": []}},
                ),
                AssignmentPageAnalysis(
                    owner_id=actor.id,
                    assignment_id=assignment.id,
                    generation_job_id=job.id,
                    draft_revision_id=revision.id,
                    paper_page_id=pages[1].id,
                    source_file_analysis_id=source_analysis.id,
                    source_snapshot_hash=job.source_snapshot_hash,
                    status="low_quality",
                    content_mode="image",
                    text_source="ocr",
                    content_mode_confidence=1,
                    metrics={
                        "page_quality": {
                            "level": "rescan_required",
                            "issues": ["blur"],
                        }
                    },
                ),
            ]
        )
        candidate = AssignmentQuestionExtractionCandidate(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            paper_version_id=paper.id,
            candidate_version=1,
            question_number="2(3)",
            question_type="other",
            content_text="Synthetic question",
            max_score=None,
            field_confidences={},
            overall_confidence=1,
            extraction_method="pdf_text_anchor",
            evidence={},
            warning_codes=["QUESTION_SCORE_MISSING"],
            status="suggested",
            manual_required=False,
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(candidate)
        db.flush()
        db.add(
            AssignmentQuestionExtractionRegion(
                candidate_id=candidate.id,
                paper_page_id=pages[0].id,
                display_order=0,
                region_type="stem",
                x=0,
                y=0.2,
                width=1,
                height=0.8,
                confidence=1,
                evidence={"source": "pdf_text_anchor_partition"},
                source_block_ids=[],
            )
        )
        db.commit()
        candidate_id = candidate.id
        paper_id = paper.id
        page_ids = [page.id for page in pages]
        snapshot = job.source_snapshot_hash
        revision_version = revision.teacher_edit_version

    guard = {
        "expected_teacher_edit_version": 0,
        "expected_draft_revision_edit_version": revision_version,
        "expected_paper_version_id": str(paper_id),
        "expected_source_snapshot": snapshot,
    }
    invalid = client.put(
        f"/api/question-extraction-candidates/{candidate_id}/regions",
        json={
            **guard,
            "regions": [
                {
                    "paper_page_id": str(uuid.uuid4()),
                    "x": 0,
                    "y": 0,
                    "width": 1,
                    "height": 1,
                }
            ],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "QUESTION_REGION_PAGE_INVALID"

    saved = client.put(
        f"/api/question-extraction-candidates/{candidate_id}/regions",
        json={
            **guard,
            "regions": [
                {
                    "paper_page_id": str(page_ids[0]),
                    "x": 0,
                    "y": 0.25,
                    "width": 1,
                    "height": 0.75,
                },
                {
                    "paper_page_id": str(page_ids[1]),
                    "x": 0,
                    "y": 0,
                    "width": 1,
                    "height": 0.4,
                },
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["teacher_edit_version"] == 1
    assert payload["manual_required"] is True
    assert "REGION_TEACHER_ADJUSTED" in payload["warning_codes"]
    assert "PAGE_QUALITY_RESCAN_REQUIRED" in payload["warning_codes"]
    assert len(payload["regions"]) == 2
    assert {item["paper_page_id"] for item in payload["regions"]} == {
        str(page_ids[0]),
        str(page_ids[1]),
    }
    assert all(item["cross_page_group"] == str(candidate_id) for item in payload["regions"])
    assert all(item["evidence"] == {"source": "teacher_adjusted"} for item in payload["regions"])

    stale = client.put(
        f"/api/question-extraction-candidates/{candidate_id}/regions",
        json={
            **guard,
            "regions": [
                {
                    "paper_page_id": str(page_ids[0]),
                    "x": 0,
                    "y": 0.25,
                    "width": 1,
                    "height": 0.75,
                }
            ],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "QUESTION_CANDIDATE_EDIT_CONFLICT"

    disposition_guard = {
        "expected_teacher_edit_version": 1,
        "expected_draft_revision_edit_version": revision_version + 1,
        "expected_paper_version_id": str(paper_id),
        "expected_source_snapshot": snapshot,
    }
    accept = client.patch(
        f"/api/question-extraction-candidates/{candidate_id}/disposition",
        json={**disposition_guard, "action": "accept"},
    )
    assert accept.status_code == 409
    assert accept.json()["code"] == "RECOGNITION_PAGE_RESCAN_REQUIRED"

    modify = client.patch(
        f"/api/question-extraction-candidates/{candidate_id}/disposition",
        json={
            **disposition_guard,
            "action": "modify",
            "teacher_value": {"content_text": "Teacher corrected question"},
        },
    )
    assert modify.status_code == 409
    assert modify.json()["code"] == "RECOGNITION_PAGE_RESCAN_REQUIRED"

    with SessionLocal() as db:
        candidate_row = db.get(AssignmentQuestionExtractionCandidate, candidate_id)
        assert candidate_row is not None
        paper_row = db.get(PaperVersion, paper_id)
        assert paper_row is not None
        paper_row.status = "ready"
        candidate_row.warning_codes = [
            code for code in candidate_row.warning_codes if code != "PAGE_QUALITY_RESCAN_REQUIRED"
        ]
        candidate_row.warning_codes.append("READING_ORDER_CONFLICT")
        rescan_analysis = db.scalar(
            select(AssignmentPageAnalysis).where(
                AssignmentPageAnalysis.draft_revision_id == candidate_row.draft_revision_id,
                AssignmentPageAnalysis.paper_page_id == page_ids[1],
            )
        )
        assert rescan_analysis is not None
        rescan_analysis.metrics = {
            "page_quality": {"level": "good", "issues": []},
            "math_structure": {
                "risk_codes": ["READING_ORDER_CONFLICT"],
                "evidence": [{"block_indexes": [], "region": [0, 0, 1, 1]}],
            },
        }
        db.flush()
        assignment_row = db.get(Assignment, assignment.id)
        revision_row = db.get(AssignmentDraftRevision, candidate_row.draft_revision_id)
        job_row = db.get(AssignmentGenerationJob, candidate_row.generation_job_id)
        source_analysis_row = db.scalar(
            select(AssignmentSourceFileAnalysis).where(
                AssignmentSourceFileAnalysis.draft_revision_id == candidate_row.draft_revision_id
            )
        )
        assert assignment_row is not None
        assert revision_row is not None
        assert job_row is not None
        assert source_analysis_row is not None
        snapshot = source_snapshot_hash(db, assignment_row)
        candidate_row.source_snapshot_hash = snapshot
        revision_row.source_snapshot_hash = snapshot
        job_row.source_snapshot_hash = snapshot
        source_analysis_row.source_snapshot_hash = snapshot
        for analysis in db.scalars(
            select(AssignmentPageAnalysis).where(
                AssignmentPageAnalysis.draft_revision_id == candidate_row.draft_revision_id
            )
        ).all():
            analysis.source_snapshot_hash = snapshot
        db.commit()

    disposition_guard["expected_source_snapshot"] = snapshot

    reading_accept = client.patch(
        f"/api/question-extraction-candidates/{candidate_id}/disposition",
        json={**disposition_guard, "action": "accept"},
    )
    assert reading_accept.status_code == 409
    assert reading_accept.json()["code"] == "READING_ORDER_CONFLICT"

    number_only_modify = client.patch(
        f"/api/question-extraction-candidates/{candidate_id}/disposition",
        json={
            **disposition_guard,
            "action": "modify",
            "teacher_value": {"question_number": "2(3)-checked"},
        },
    )
    assert number_only_modify.status_code == 409
    assert number_only_modify.json()["code"] == "READING_ORDER_CONFLICT"

    no_op_content_modify = client.patch(
        f"/api/question-extraction-candidates/{candidate_id}/disposition",
        json={
            **disposition_guard,
            "action": "modify",
            "teacher_value": {"content_text": "Synthetic question"},
        },
    )
    assert no_op_content_modify.status_code == 409
    assert no_op_content_modify.json()["code"] == "READING_ORDER_CONFLICT"

    reading_modify = client.patch(
        f"/api/question-extraction-candidates/{candidate_id}/disposition",
        json={
            **disposition_guard,
            "action": "modify",
            "teacher_value": {"content_text": "Teacher corrected reading order"},
        },
    )
    assert reading_modify.status_code == 200, reading_modify.text
    assert reading_modify.json()["status"] == "modified"
    with SessionLocal() as db:
        moved_candidate = db.get(AssignmentQuestionExtractionCandidate, candidate_id)
        assert moved_candidate is not None
        assert moved_candidate.paper_version_id != paper_id
        moved_page_ids = set(
            db.scalars(
                select(AssignmentQuestionExtractionRegion.paper_page_id).where(
                    AssignmentQuestionExtractionRegion.candidate_id == candidate_id
                )
            ).all()
        )
        assert moved_page_ids.isdisjoint(page_ids)
        copied_analyses = list(
            db.scalars(
                select(AssignmentPageAnalysis).where(
                    AssignmentPageAnalysis.draft_revision_id == moved_candidate.draft_revision_id,
                    AssignmentPageAnalysis.paper_page_id.in_(moved_page_ids),
                )
            ).all()
        )
        assert {analysis.paper_page_id for analysis in copied_analyses} == moved_page_ids
        assert any(
            "READING_ORDER_CONFLICT"
            in (analysis.metrics.get("math_structure") or {}).get("risk_codes", [])
            for analysis in copied_analyses
        )


def test_production_fake_degrades_to_unavailable():
    settings = SimpleNamespace(
        assignment_generation_provider="fake",
        app_env="production",
    )
    provider = select_provider(settings, "fake")
    assert provider.name == "unavailable"
    assert provider.available is False
    assert provider.error_code == "FAKE_PROVIDER_DISABLED_IN_PRODUCTION"


def test_capabilities_are_server_owned_and_reflect_configured_provider(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "assignment_generation_provider": "local_openai_compatible",
            "assignment_generation_allow_local_provider_requests": True,
            "assignment_generation_allowed_local_hosts": ["local-llm"],
            "assignment_generation_base_url": "http://local-llm:8080/v1",
            "assignment_generation_api_key": "p" * 32,
            "assignment_generation_model": "local-model",
        }
    )
    monkeypatch.setattr("app.api.assignment_generation.get_settings", lambda: settings)
    response = client.get("/api/assignment-generation-capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "provider": "local_openai_compatible",
        "provider_status": "available",
        "provider_error_code": None,
        "external_provider_requests": False,
        "teacher_start_allowed": True,
        "suggestion_only": True,
        "real_provider_quality_passed": False,
    }


def test_non_test_job_uses_the_server_configured_provider(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "app_env": "development",
            "assignment_generation_provider": "local_openai_compatible",
            "assignment_generation_allow_local_provider_requests": True,
            "assignment_generation_allowed_local_hosts": ["local-llm"],
            "assignment_generation_base_url": "http://local-llm:8080/v1",
            "assignment_generation_api_key": "p" * 32,
            "assignment_generation_model": "local-model",
        }
    )
    monkeypatch.setattr("app.assignment_generation.service.get_settings", lambda: settings)
    actor, assignment = actor_and_assignment()

    with SessionLocal() as db:
        job, _revision, _reused = create_job(
            db,
            actor.id,
            assignment.id,
            f"configured-provider-{uuid.uuid4()}",
            "unavailable",
            None,
        )

    assert job.provider_mode == "local_openai_compatible"


def test_published_assignment_rejects_generation_before_job_creation(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        row = db.get(Assignment, assignment.id)
        assert row is not None
        row.status = "published"
        db.commit()
    response = start(assignment.id, "published-generation-key")
    assert response.status_code == 409
    assert response.json()["code"] == "ASSIGNMENT_ALREADY_PUBLISHED"
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(AssignmentGenerationJob.id).where(
                    AssignmentGenerationJob.assignment_id == assignment.id
                )
            )
            is None
        )


def test_dispatch_failure_maps_to_stable_failure(monkeypatch):
    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        job, _revision, _ = create_job(
            db, actor.id, assignment.id, "dispatch-failure-key", "unavailable", None
        )
        db.commit()
        monkeypatch.setattr(
            "workers.celery_app.celery_app.send_task",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broker secret")),
        )
        dispatch_job(db, job)
        assert job.status == "failed"
        assert job.error_code == "WORKER_UNAVAILABLE"
        assert "secret" not in (job.error_message or "")


def test_dispatch_uses_pdf_text_recognition_without_an_ocr_provider(monkeypatch):
    actor, assignment = actor_and_assignment()
    sent: list[tuple[str, list[str]]] = []

    def send_task(name: str, args: list[str], **_kwargs: object) -> SimpleNamespace:
        sent.append((name, args))
        return SimpleNamespace(id=f"task-{len(sent)}")

    monkeypatch.setattr("workers.celery_app.celery_app.send_task", send_task)
    with SessionLocal() as db:
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=f"dispatch-pdf/{uuid.uuid4()}",
            original_name="synthetic-text.pdf",
            content_type="application/pdf",
            size=128,
            checksum="e" * 64,
            status="ready",
        )
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            status="draft",
            source_type="manual",
            created_by=actor.id,
        )
        db.add_all([stored, paper])
        db.flush()
        db.add(
            PaperPage(
                paper_version_id=paper.id,
                stored_file_id=stored.id,
                page_number=1,
                source_page_number=1,
                status="ready",
            )
        )
        row = db.get(Assignment, assignment.id)
        assert row is not None
        row.active_paper_version_id = paper.id
        db.flush()
        job, _revision, _ = create_job(
            db, actor.id, assignment.id, f"dispatch-pdf-{uuid.uuid4()}", "unavailable", None
        )
        db.commit()

        dispatch_job(db, job)

        recognition = db.scalar(
            select(RecognitionJob).where(RecognitionJob.paper_version_id == paper.id)
        )
        assert recognition is not None
        assert recognition.provider == "unavailable"
        assert sent == [
            ("ahamark.recognition.run", [str(recognition.id)]),
            (
                "ahamark.assignment_generation.run_after_recognition",
                [str(job.id), str(recognition.id)],
            ),
        ]


def test_guarded_worker_failure_is_logged_without_exposing_it_to_the_result(monkeypatch):
    job_id = str(uuid.uuid4())
    logged: list[tuple[str, dict[str, object]]] = []

    def fail_run(*_args: object) -> dict[str, object]:
        raise RuntimeError("controlled-worker-detail")

    monkeypatch.setattr("workers.tasks.assignment_generation._run", fail_run)
    monkeypatch.setattr(
        "workers.tasks.assignment_generation.log.exception",
        lambda event, **context: logged.append((event, context)),
    )

    assert _guarded_run(job_id, None) == {"status": "failed"}
    assert logged == [
        (
                "assignment_generation_failed",
                {
                    "job_id": job_id,
                    "retry_stage": None,
                    "exception_type": "RuntimeError",
                },
        )
    ]


def test_owner_isolation_for_job_and_revision():
    from app.api.domain import ApiProblem
    from app.assignment_generation.service import owned_job, owned_revision

    actor, assignment = actor_and_assignment()
    with SessionLocal() as db:
        job, revision, _ = create_job(
            db, actor.id, assignment.id, "owner-isolation-key", "unavailable", None
        )
        other = User(
            email="other-generation-owner@example.com",
            password_hash="not-used",
            display_name="Other",
        )
        db.add(other)
        db.commit()
        for lookup, identifier in ((owned_job, job.id), (owned_revision, revision.id)):
            try:
                lookup(db, other.id, identifier)
            except ApiProblem as exc:
                assert exc.status == 404
            else:
                raise AssertionError("cross-owner generation resource leaked")


def test_activate_is_draft_only_and_audited(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    revision_id = created["revision"]["id"]
    premature = client.post(f"/api/assignment-draft-revisions/{revision_id}/activate")
    assert premature.status_code == 409
    assert premature.json()["code"] == "DRAFT_NOT_ACTIVATABLE"
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        revision = db.get(AssignmentDraftRevision, uuid.UUID(revision_id))
        assert job is not None and revision is not None
        job.status = "review_required"
        job.progress = 100
        revision.status = "review_required"
        db.commit()
    activated = client.post(f"/api/assignment-draft-revisions/{revision_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    with SessionLocal() as db:
        assignment_row = db.get(Assignment, assignment.id)
        assert assignment_row is not None
        assert assignment_row.status == "draft"
        assert (
            db.scalar(
                select(AuditLog).where(
                    AuditLog.actor_id == actor.id,
                    AuditLog.action == "assignment_draft.activate",
                    AuditLog.resource_id == revision_id,
                )
            )
            is not None
        )


def test_running_cancel_is_observed_before_worker_write(monkeypatch):
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    _actor, assignment = actor_and_assignment()
    created = start(assignment.id).json()
    with SessionLocal() as db:
        job = db.get(AssignmentGenerationJob, uuid.UUID(created["id"]))
        assert job is not None
        job.status = "analyzing"
        job.current_stage = "analyzing"
        job.progress = 10
        db.commit()
    response = client.post(f"/api/assignment-generation-jobs/{created['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["cancel_requested_at"] is not None
    assert _run(created["id"], None)["status"] == "cancel_requested"
    final = client.get(f"/api/assignment-generation-jobs/{created['id']}").json()
    assert final["status"] == "cancelled"
    assert final["stages"] == []
