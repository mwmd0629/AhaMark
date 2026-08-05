import io
import json
import uuid

from app.assignment_generation.question_extraction import materialize
from app.assignment_generation.service import transition
from app.core.config import Settings
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentFieldSuggestion,
    AssignmentGenerationJob,
    AssignmentGenerationProviderInvocation,
    AssignmentPageAnalysis,
    AssignmentPublishReadinessSnapshot,
    AssignmentQuestionExtractionCandidate,
    AssignmentQuestionExtractionRegion,
    AssignmentReviewSession,
    AssignmentRubricCriterionDraft,
    AssignmentRubricDraftCandidate,
    AssignmentRubricValidationResult,
    AssignmentSourceFileAnalysis,
    FileStatus,
    GradeRelease,
    PaperPage,
    PaperPageOrganizationSuggestion,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    StoredFile,
    StructuredRubricVersion,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from workers.tasks.assignment_generation import _claim_job, _execute_stage


class ProviderResponse(io.BytesIO):
    headers = {"x-request-id": "worker-e2e-request"}

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def configured() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        assignment_generation_provider="openai_compatible",
        assignment_generation_allow_external_provider_requests=True,
        assignment_generation_base_url="https://provider.invalid/v1",
        assignment_generation_api_key="worker-e2e-secret-never-log",
        assignment_generation_model="worker-e2e-model",
        assignment_generation_model_snapshot="worker-e2e-model-2026-07-26",
        assignment_generation_max_retries=0,
    )


def fake_configured() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        assignment_generation_provider="fake",
    )


def source_assignment() -> tuple[Assignment, StoredFile, PaperPage]:
    TestClient(app).get("/api/classes")
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
        assert actor is not None
        assignment = Assignment(owner_id=actor.id, title="Provider Worker E2E")
        db.add(assignment)
        db.flush()
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            created_by=actor.id,
            source_type="upload",
        )
        db.add(paper)
        db.flush()
        assignment.active_paper_version_id = paper.id
        stored = StoredFile(
            owner_id=actor.id,
            storage_key=f"assignments/provider-e2e/{uuid.uuid4()}.pdf",
            original_name="provider-e2e.pdf",
            content_type="application/pdf",
            size=1024,
            checksum="b" * 64,
            status=FileStatus.ready,
        )
        db.add(stored)
        db.flush()
        page = PaperPage(
            paper_version_id=paper.id,
            stored_file_id=stored.id,
            page_number=1,
            source_page_number=1,
            status="ready",
        )
        db.add(page)
        db.commit()
        for item in (assignment, stored, page):
            db.refresh(item)
            db.expunge(item)
        return assignment, stored, page


def answer_rubric_output(question_id: str) -> dict[str, object]:
    evidence = [{"kind": "question", "reference_id": question_id, "summary": "题目本身"}]
    return {
        "raw_content": "2",
        "normalized_content": "2",
        "structured_content": {"answer_type": "exact_scalar", "value": "2"},
        "alternative_answers": [],
        "title": "答案与评分标准草稿",
        "requested_scoring_mode": "deterministic",
        "total_points": "5",
        "allow_partial_credit": True,
        "domain_requirements": {},
        "validation_config": {"answer_type": "exact_scalar"},
        "common_error_types": [],
        "feedback_templates": {},
        "confidence": 0.95,
        "evidence": evidence,
        "degradation_reason": None,
        "warning_codes": [],
        "criteria": [
            {
                "criterion_key": "result",
                "title": "结果正确",
                "description": "答案为 2",
                "points": "5",
                "criterion_type": "result",
                "required": True,
                "dependency_keys": [],
                "alternative_group": None,
                "partial_credit_rule": {},
                "deduction_rule": {},
                "validation_rule": {"answer_type": "exact_scalar"},
                "common_error_codes": [],
                "feedback_template": "检查计算结果",
                "confidence": 0.95,
                "evidence": evidence,
                "degradation_reason": None,
                "manual_required": False,
            }
        ],
    }


def test_mocked_http_provider_worker_materializes_only_versioned_drafts(monkeypatch) -> None:
    assignment, stored, page = source_assignment()
    settings = configured()
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    monkeypatch.setattr("app.assignment_generation.service.get_settings", lambda: settings)
    monkeypatch.setattr("workers.tasks.assignment_generation.get_settings", lambda: settings)

    def urlopen(request, **_kwargs):
        body = json.loads(request.data)
        name = body["text"]["format"]["name"]
        payload = json.loads(body["input"][0]["content"][0]["text"].split("\n", 1)[1])
        if name.endswith("metadata_analysis"):
            output = {
                "suggestions": [
                    {
                        "field_name": "subject",
                        "suggested_value": "数学",
                        "normalized_value": "数学",
                        "confidence": 0.95,
                        "evidence": [
                            {"kind": "file", "reference_id": str(stored.id), "summary": "试卷文件"}
                        ],
                        "source_type": "provider",
                    }
                ]
            }
        elif name.endswith("file_analysis"):
            output = {
                "files": [
                    {
                        "stored_file_id": str(stored.id),
                        "detected_mime_type": "application/pdf",
                        "checksum": stored.checksum,
                        "page_count": 1,
                        "suggested_role": "question_paper",
                        "role_confidence": 0.98,
                        "suggested_answer_source": "not_applicable",
                        "answer_source_confidence": 0.98,
                        "duplicate_of_file_id": None,
                        "evidence": [],
                        "warning_codes": [],
                    }
                ],
                "pages": [
                    {
                        "paper_page_id": str(page.id),
                        "stored_file_id": str(stored.id),
                        "status": "ready",
                        "quality_score": 0.98,
                        "blank_probability": 0.01,
                        "duplicate_probability": 0.0,
                        "duplicate_of_page_id": None,
                        "missing_page_suspected": False,
                        "low_quality": False,
                        "corrupted": False,
                        "mixed_document_suspected": False,
                        "variant_label": None,
                        "metrics": {},
                        "evidence": [],
                        "warning_codes": [],
                    }
                ],
                "prompt_injection_detected": False,
                "prompt_injection_evidence": [],
            }
        elif name.endswith("question_extraction"):
            output = {
                "candidates": [
                    {
                        "ref": "q1",
                        "parent_ref": None,
                        "source_candidate_id": None,
                        "question_number": "1",
                        "question_type": "calculation",
                        "content_text": "计算 1+1。",
                        "content_latex": None,
                        "max_score": "5",
                        "difficulty": "easy",
                        "knowledge_points": ["加法"],
                        "field_confidences": {
                            key: "0.95"
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
                        "overall_confidence": "0.95",
                        "evidence": {},
                        "warning_codes": [],
                        "manual_required": False,
                        "regions": [
                            {
                                "page_id": str(page.id),
                                "display_order": 0,
                                "region_type": "stem",
                                "x": "0.1",
                                "y": "0.1",
                                "width": "0.8",
                                "height": "0.2",
                                "confidence": "0.95",
                                "block_ids": [],
                                "evidence": {},
                                "cross_page_group": None,
                            }
                        ],
                    }
                ]
            }
        else:
            output = answer_rubric_output(payload["question"]["id"])
        envelope = {
            "id": "worker-e2e-response",
            "output_text": json.dumps(output),
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
        return ProviderResponse(json.dumps(envelope).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    response = TestClient(app).post(
        f"/api/assignments/{assignment.id}/generation-jobs",
        json={
            "idempotency_key": "provider-worker-e2e-0001",
            "provider_mode": "openai_compatible",
        },
    )
    assert response.status_code == 201
    job_id = uuid.UUID(response.json()["id"])

    with SessionLocal() as db:
        _job, revision, claim = _claim_job(db, job_id, None)
        assert claim is None and revision is not None
        assert _execute_stage(db, job_id, "analyzing") == "completed"
        assert _execute_stage(db, job_id, "processing_pages") == "completed"
        assert _execute_stage(db, job_id, "extracting_questions") == "completed"
        candidate = db.scalar(
            select(AssignmentQuestionExtractionCandidate).where(
                AssignmentQuestionExtractionCandidate.generation_job_id == job_id
            )
        )
        assert candidate is not None
        regions = list(
            db.scalars(
                select(AssignmentQuestionExtractionRegion).where(
                    AssignmentQuestionExtractionRegion.candidate_id == candidate.id
                )
            )
        )
        candidate.status = "accepted"
        question = materialize(db, candidate, regions)
        db.commit()
        assert _execute_stage(db, job_id, "generating_rubrics") == "completed"
        assert _execute_stage(db, job_id, "validating") == "completed"
        job = db.get(AssignmentGenerationJob, job_id)
        assert job is not None
        transition(job, "review_required")
        db.commit()

        assert db.scalar(select(func.count()).select_from(AssignmentFieldSuggestion)) == 1
        assert db.scalar(select(func.count()).select_from(AssignmentSourceFileAnalysis)) == 1
        assert db.scalar(select(func.count()).select_from(AssignmentPageAnalysis)) == 1
        assert db.scalar(select(func.count()).select_from(PaperPageOrganizationSuggestion)) >= 1
        assert db.scalar(select(func.count()).select_from(AssignmentAnswerDraftCandidate)) == 1
        assert db.scalar(select(func.count()).select_from(AssignmentRubricDraftCandidate)) == 1
        assert db.scalar(select(func.count()).select_from(AssignmentRubricCriterionDraft)) == 1
        assert db.scalar(select(func.count()).select_from(AssignmentRubricValidationResult)) == 1
        assert (
            db.scalar(select(func.count()).select_from(AssignmentGenerationProviderInvocation)) == 5
        )
        invocations = list(db.scalars(select(AssignmentGenerationProviderInvocation)))
        assert all(item.model == "worker-e2e-model" for item in invocations)
        assert all(item.model_snapshot == "worker-e2e-model-2026-07-26" for item in invocations)
        assert all(item.provider_config_version for item in invocations)
        assert all(item.stage_generation == 1 for item in invocations)
        assert all(item.retry_count == 0 for item in invocations)
        assert all(item.image_count == 0 and item.image_bytes == 0 for item in invocations)
        answer = db.scalar(select(AssignmentAnswerDraftCandidate))
        assert answer is not None and answer.source_type == "ai_generated"
        assert answer.provenance["model_snapshot"] == "worker-e2e-model-2026-07-26"
        persisted_assignment = db.get(Assignment, assignment.id)
        assert persisted_assignment is not None and persisted_assignment.status.value == "draft"
        assert db.get(PaperPage, page.id).status == "ready"
        assert db.get(Question, question.id) is not None
        for forbidden in (
            ReferenceAnswerVersion,
            StructuredRubricVersion,
            GradeRelease,
            SubmissionScoreSnapshot,
            TeacherReview,
            AssignmentReviewSession,
            AssignmentPublishReadinessSnapshot,
        ):
            assert db.scalar(select(func.count()).select_from(forbidden)) == 0


def test_fake_worker_uses_provider_dispatch_audit_and_never_legacy_candidates(
    monkeypatch,
) -> None:
    assignment, _stored, _page = source_assignment()
    settings = fake_configured()
    monkeypatch.setattr("app.api.assignment_generation.dispatch_job", lambda *_args: None)
    monkeypatch.setattr("app.assignment_generation.service.get_settings", lambda: settings)
    monkeypatch.setattr("workers.tasks.assignment_generation.get_settings", lambda: settings)

    def legacy_candidates_forbidden(*_args, **_kwargs):
        raise AssertionError("worker fake must not call legacy generate_candidates")

    monkeypatch.setattr(
        "workers.tasks.assignment_generation.generate_candidates",
        legacy_candidates_forbidden,
    )
    response = TestClient(app).post(
        f"/api/assignments/{assignment.id}/generation-jobs",
        json={
            "idempotency_key": "fake-provider-worker-e2e-0001",
            "provider_mode": "fake",
        },
    )
    assert response.status_code == 201
    job_id = uuid.UUID(response.json()["id"])

    with SessionLocal() as db:
        job, revision, claim = _claim_job(db, job_id, None)
        assert claim is None and job is not None and revision is not None
        assert assignment.active_paper_version_id is not None
        question = Question(
            paper_version_id=assignment.active_paper_version_id,
            question_number="1",
            display_order=1,
            question_type="calculation",
            content_text="计算 1+1。",
            max_score=5,
            source="test_fixture",
        )
        db.add(question)
        db.flush()
        db.add(
            AssignmentQuestionExtractionCandidate(
                owner_id=job.owner_id,
                assignment_id=job.assignment_id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                paper_version_id=assignment.active_paper_version_id,
                candidate_version=1,
                question_number="1",
                question_type="calculation",
                content_text="计算 1+1。",
                max_score=5,
                field_confidences={},
                overall_confidence=1,
                extraction_method="test_fixture",
                evidence={},
                warning_codes=[],
                status="accepted",
                manual_required=False,
                source_snapshot_hash=job.source_snapshot_hash,
                materialized_question_id=question.id,
            )
        )
        transition(job, "processing_pages")
        transition(job, "extracting_questions")
        db.commit()

        assert _execute_stage(db, job_id, "generating_rubrics") == "completed"
        invocations = list(
            db.scalars(
                select(AssignmentGenerationProviderInvocation)
                .where(AssignmentGenerationProviderInvocation.job_id == job_id)
                .order_by(AssignmentGenerationProviderInvocation.created_at)
            )
        )
        assert len(invocations) == 2
        assert all(item.provider == "fake" for item in invocations)
        assert all(item.endpoint_mode == "deterministic_test_only" for item in invocations)
        assert all(item.status == "completed" for item in invocations)
        assert all(item.model_snapshot == "deterministic-test-only" for item in invocations)
        assert all(len(item.request_hash) == 64 for item in invocations)
        assert all(item.response_hash and len(item.response_hash) == 64 for item in invocations)

        answer = db.scalar(
            select(AssignmentAnswerDraftCandidate).where(
                AssignmentAnswerDraftCandidate.generation_job_id == job_id
            )
        )
        rubric = db.scalar(
            select(AssignmentRubricDraftCandidate).where(
                AssignmentRubricDraftCandidate.generation_job_id == job_id
            )
        )
        assert answer is not None and rubric is not None
        assert answer.provenance["provider"] == "fake"
        assert answer.provenance["model_snapshot"] == "deterministic-test-only"
        assert answer.evidence and rubric.evidence
        assert rubric.answer_candidate_id == answer.id
