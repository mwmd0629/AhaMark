import inspect as pyinspect
import uuid
from datetime import timedelta
from importlib import import_module
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api.assignment_central_review import (
    PROJECTION_WRITE_LOCK_ORDER,
    Disposition,
    PublishInput,
    _issue_blocks_review_bundle,
    _rubric_content_payload,
    digest,
    disposition,
    generated_issues,
    owned_assignment,
    owned_session,
    prepare_publication,
    selected_versions,
    validate_current_projection_under_locks,
)
from app.api.domain import ApiProblem
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentClass,
    AssignmentDraftRevision,
    AssignmentExplicitConfirmation,
    AssignmentGenerationJob,
    AssignmentPublishReadinessSnapshot,
    AssignmentQuestionExtractionCandidate,
    AssignmentReviewItem,
    AssignmentReviewSession,
    AssignmentRubricDraftCandidate,
    AssignmentRubricPublicationBinding,
    AuditLog,
    GenerationIssue,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    RubricItem,
    SchoolClass,
    StructuredRubricVersion,
    User,
    now_utc,
)
from app.semantic_content import (
    reference_answer_semantic_payload,
    semantic_hash,
    semantic_normalize,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, inspect, select, text

client = TestClient(app)


def test_publish_contract_requires_server_readiness_and_explicit_true() -> None:
    response = client.post(
        "/api/assignments/00000000-0000-0000-0000-000000000000/publish",
        json={},
    )
    assert response.status_code == 422
    try:
        PublishInput.model_validate(
            {
                "readiness_snapshot_id": "00000000-0000-0000-0000-000000000000",
                "readiness_hash": "0" * 64,
                "expected_assignment_updated_at": "2026-07-26T00:00:00Z",
                "explicit_confirmation": False,
            }
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("explicit_confirmation=false must be rejected")


def test_stable_hash_ignores_mapping_insertion_order() -> None:
    assert digest({"b": [2, 1], "a": {"y": 2, "x": 1}}) == digest(
        {"a": {"x": 1, "y": 2}, "b": [2, 1]}
    )


def test_bundle_blocker_status_policy_only_blocks_on_unresolved_blocking_issues() -> None:
    assert _issue_blocks_review_bundle("blocking", "open") is True
    assert _issue_blocks_review_bundle("blocking", "acknowledged") is True
    assert _issue_blocks_review_bundle("blocking", "resolved") is False
    assert _issue_blocks_review_bundle("blocking", "rejected") is False
    assert _issue_blocks_review_bundle("warning", "open") is False
    assert _issue_blocks_review_bundle("warning", "acknowledged") is False
    assert _issue_blocks_review_bundle("warning", "resolved") is False
    assert _issue_blocks_review_bundle("warning", "rejected") is False
    assert _issue_blocks_review_bundle("info", "open") is False


def test_semantic_hash_v1_normalizes_text_and_drops_nested_identity_metadata() -> None:
    identity = uuid.uuid4()
    assert semantic_hash(
        {
            "content": "Cafe\u0301  \r\nanswer\t ",
            "aliases": [identity, {"opaque": identity}, str(uuid.uuid4()), "kept"],
            "opaque_time": now_utc(),
            "metadata": {"actor": "ignored", "visible": "yes"},
        }
    ) == semantic_hash(
        {
            "content": "Café\nanswer",
            "aliases": ["kept"],
            "metadata": {"visible": "yes"},
        }
    )
    assert semantic_normalize([identity, uuid.uuid4()]) is None


def test_reference_answer_source_type_is_teacher_visible_semantics() -> None:
    payload = {"source_type": "teacher_official", "normalized_content": "42"}
    assert semantic_hash(payload) != semantic_hash(payload | {"source_type": "ai_generated"})
    common = {
        "source_type": "teacher_official",
        "source_region": {},
        "normalized_content": "Café\n42",
        "structured_content": {},
        "alternative_answers": [],
        "provenance": {},
    }
    assert semantic_hash(
        reference_answer_semantic_payload(raw_content="Cafe\u0301  \r\n42 ", **common)
    ) == semantic_hash(
        reference_answer_semantic_payload(raw_content="untrusted OCR bytes", **common)
    )


def test_projection_write_lock_order_is_postgresql_safe() -> None:
    assert PROJECTION_WRITE_LOCK_ORDER == (
        "assignment",
        "snapshot",
        "session",
        "paper",
        "questions",
        "binding",
        "formal_versions",
        "criteria",
        "legacy_rubric",
        "legacy_items",
        "confirmation",
    )
    validator_source = pyinspect.getsource(validate_current_projection_under_locks)
    markers = (
        "select(PaperVersion)",
        "select(Question)",
        "select(AssignmentRubricPublicationBinding)",
        "select(ReferenceAnswerVersion)",
        "select(StructuredRubricVersion)",
        "select(RubricCriterion)",
        "select(RubricVersion)",
        "select(QuestionRubric)",
        "select(RubricItem)",
        "select(AssignmentExplicitConfirmation)",
    )
    positions = [validator_source.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert validator_source.count(".with_for_update()") >= len(markers)
    assert validator_source.count("populate_existing=True") >= len(markers)
    prepare_source = pyinspect.getsource(prepare_publication)
    assert prepare_source.index("owned_assignment(") < prepare_source.index(
        "owned_session(db, actor.id, session_id, lock=True)"
    )
    disposition_source = pyinspect.getsource(disposition)
    assert (
        disposition_source.index("item_hint =")
        < disposition_source.index(
            "owned_session(db, actor.id, item_hint.review_session_id, lock=True)"
        )
        < disposition_source.index("item = db.scalar(")
    )
    assert "execution_options(populate_existing=True)" in disposition_source


def test_lock_helpers_refresh_stale_identity_map_before_disposition_gate() -> None:
    client.get("/api/classes")
    with SessionLocal() as first_db:
        actor = first_db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
        assert actor is not None
        assignment = Assignment(
            owner_id=actor.id,
            title="stale lock helper",
            total_score=1,
        )
        first_db.add(assignment)
        first_db.flush()
        paper = PaperVersion(
            assignment_id=assignment.id,
            version=1,
            created_by=actor.id,
        )
        first_db.add(paper)
        first_db.flush()
        assignment.active_paper_version_id = paper.id
        job = AssignmentGenerationJob(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation=1,
            status="review_required",
            idempotency_key=f"stale-lock-{uuid.uuid4()}",
            request_fingerprint="a" * 64,
            source_snapshot_hash="b" * 64,
            provider_config_version="test",
            prompt_version="test",
            schema_version="test",
        )
        first_db.add(job)
        first_db.flush()
        revision = AssignmentDraftRevision(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            revision=1,
            source_snapshot_hash=job.source_snapshot_hash,
            created_by_type="teacher",
            created_by=actor.id,
        )
        first_db.add(revision)
        first_db.flush()
        review = AssignmentReviewSession(
            owner_id=actor.id,
            assignment_id=assignment.id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            generation=1,
            source_snapshot_hash=job.source_snapshot_hash,
            review_version=1,
            status="draft",
            risk_ledger_hash="c" * 64,
            expected_assignment_updated_at=assignment.updated_at,
            paper_version_id=paper.id,
            structured_binding_hash="d" * 64,
            created_by=actor.id,
        )
        first_db.add(review)
        first_db.flush()
        review_item = AssignmentReviewItem(
            review_session_id=review.id,
            section="test",
            entity_type="assignment",
            entity_id=str(assignment.id),
            severity="warning",
            issue_code="STALE_IDENTITY_TEST",
            title="stale identity",
            message="stale identity",
            source_hash="e" * 64,
            status="open",
        )
        first_db.add(review_item)
        first_db.commit()
        actor_id = actor.id
        assignment_id = assignment.id
        review_id = review.id
        item_id = review_item.id

        stale_assignment = owned_assignment(first_db, actor_id, assignment_id)
        stale_review = owned_session(first_db, actor_id, review_id)
        assert stale_assignment.title == "stale lock helper"
        assert stale_review.review_version == 1

        with SessionLocal() as concurrent_db:
            concurrent_assignment = concurrent_db.get(Assignment, assignment_id)
            concurrent_review = concurrent_db.get(AssignmentReviewSession, review_id)
            assert concurrent_assignment is not None and concurrent_review is not None
            concurrent_assignment.title = "fresh lock helper"
            concurrent_review.review_version = 2
            concurrent_review.status = "in_review"
            concurrent_db.commit()

        assert (
            owned_assignment(first_db, actor_id, assignment_id, lock=True).title
            == "fresh lock helper"
        )
        refreshed_review = owned_session(first_db, actor_id, review_id, lock=True)
        assert refreshed_review.review_version == 2
        assert refreshed_review.status == "in_review"
        with pytest.raises(ApiProblem) as error:
            disposition(
                item_id,
                Disposition(
                    expected_review_version=1,
                    action="acknowledge",
                    note="must reject stale version",
                ),
                first_db,
                actor,
            )
        assert error.value.status == 409
        assert error.value.code == "REVIEW_VERSION_CONFLICT"


def test_0027_upgrade_downgrade_upgrade_and_backfill(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'semantic-projection.db'}")
    migration = import_module("apps.api.alembic.versions.0027_semantic_confirmation_projection")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE paper_versions (id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE assignment_explicit_confirmations "
            "(id CHAR(32) PRIMARY KEY, source_hash VARCHAR(64) NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE assignment_rubric_publication_bindings "
            "(id CHAR(32) PRIMARY KEY, source_binding_hash VARCHAR(64) NOT NULL)"
        )
        connection.execute(
            text(
                "INSERT INTO assignment_explicit_confirmations (id, source_hash) "
                "VALUES (:id, :hash)"
            ),
            {"id": uuid.uuid4().hex, "hash": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO assignment_rubric_publication_bindings "
                "(id, source_binding_hash) VALUES (:id, :hash)"
            ),
            {"id": uuid.uuid4().hex, "hash": "b" * 64},
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        confirmation = connection.execute(
            text(
                "SELECT confirmation_origin, fingerprint_schema_version "
                "FROM assignment_explicit_confirmations"
            )
        ).one()
        binding = connection.execute(
            text(
                "SELECT source_semantic_hash, projection_profile, loss_report_hash "
                "FROM assignment_rubric_publication_bindings"
            )
        ).one()
        assert confirmation == ("legacy_origin", None)
        assert binding == (
            "b" * 64,
            "legacy-unverified",
            migration.EMPTY_LOSS_REPORT_HASH,
        )
        migration.downgrade()
        assert "fingerprint_schema_version" not in {
            item["name"]
            for item in inspect(connection).get_columns("assignment_explicit_confirmations")
        }
        migration.upgrade()
        assert "source_semantic_hash" in {
            item["name"]
            for item in inspect(connection).get_columns("assignment_rubric_publication_bindings")
        }


def test_assignment_worker_has_no_publish_capability_import() -> None:
    source = (Path(__file__).parents[1] / "workers/tasks/assignment_generation.py").read_text(
        encoding="utf-8"
    )
    assert "teacher_publish" not in source
    assert "AssignmentPublishReadinessSnapshot" not in source
    assert "AssignmentExplicitConfirmation" not in source
    assert "AssignmentStatus.published" not in source


def test_green_teacher_review_binding_readiness_and_publish() -> None:
    client.get("/api/classes")
    db = SessionLocal()
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor is not None
    school_class = SchoolClass(owner_id=actor.id, name="集中审查班", status=ArchiveStatus.active)
    assignment = Assignment(
        owner_id=actor.id,
        title="集中审查作业",
        total_score=10,
        due_at=now_utc() + timedelta(days=7),
    )
    db.add_all([school_class, assignment])
    db.flush()
    db.add(AssignmentClass(assignment_id=assignment.id, class_id=school_class.id))
    paper = PaperVersion(assignment_id=assignment.id, version=1, created_by=actor.id)
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    question = Question(
        paper_version_id=paper.id,
        question_number="1",
        display_order=1,
        question_type="calculation",
        content_text="1+1",
        max_score=10,
    )
    db.add(question)
    db.flush()
    job = AssignmentGenerationJob(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation=1,
        status="review_required",
        idempotency_key="central-review-green",
        request_fingerprint="1" * 64,
        source_snapshot_hash="2" * 64,
        provider_config_version="test",
        prompt_version="test",
        schema_version="test",
    )
    db.add(job)
    db.flush()
    revision = AssignmentDraftRevision(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        revision=1,
        source_snapshot_hash=job.source_snapshot_hash,
        created_by_type="teacher",
        created_by=actor.id,
    )
    answer = ReferenceAnswerVersion(
        question_id=question.id,
        source_type="teacher_official",
        raw_content="2",
        normalized_content="2",
        structured_content={"alternative_answers": ["2.0"]},
        content_hash="3" * 64,
        version=1,
        provenance={"teacher": str(actor.id)},
        created_by=actor.id,
        status="confirmed",
        teacher_confirmed_at=now_utc(),
    )
    db.add_all([revision, answer])
    db.flush()
    structured = StructuredRubricVersion(
        question_id=question.id,
        question_version="1",
        reference_answer_version_id=answer.id,
        rubric_version=1,
        title="计算正确",
        total_points=10,
        status="confirmed",
        content_hash="4" * 64,
        created_by=actor.id,
        confirmed_by=actor.id,
        confirmed_at=now_utc(),
    )
    db.add(structured)
    db.flush()
    db.add(
        RubricCriterion(
            rubric_version_id=structured.id,
            stable_key="answer",
            title="答案正确",
            max_points=10,
            display_order=1,
            criterion_type="answer",
            required=True,
            validation_mode="manual",
            validation_rule={"answer_type": "exact_scalar"},
        )
    )
    recovered_issues = [
        GenerationIssue(
            owner_id=actor.id,
            assignment_id=assignment.id,
            job_id=job.id,
            draft_revision_id=revision.id,
            stage="answer_rubric",
            severity="blocking",
            code=code,
            message=f"{code} before teacher recovery",
            resolution_status="open",
        )
        for code in (
            "PROVIDER_UNAVAILABLE",
            "PAGE_ORGANIZATION_INCOMPLETE",
            "QUESTION_PAPER_ROLE_UNCONFIRMED",
            "VALIDATION_FAILED",
        )
    ]
    db.add_all(recovered_issues)
    db.commit()

    created = client.post(f"/api/assignments/{assignment.id}/review-sessions")
    assert created.status_code == 201, created.text
    session = created.json()
    review_items = client.get(f"/api/assignment-review-sessions/{session['id']}/items").json()[
        "items"
    ]
    for code in (
        "PROVIDER_UNAVAILABLE",
        "PAGE_ORGANIZATION_INCOMPLETE",
        "QUESTION_PAPER_ROLE_UNCONFIRMED",
        "VALIDATION_FAILED",
    ):
        recovered_review = next(item for item in review_items if item["issue_code"] == code)
        assert recovered_review["severity"] == "info"
        assert "无需再处理" in recovered_review["message"]
    original_session_id = session["id"]
    for issue in recovered_issues:
        issue.resolution_status = "resolved"
        issue.resolved_by = actor.id
        issue.resolved_at = now_utc()
    db.commit()
    unchanged = client.post(f"/api/assignments/{assignment.id}/review-sessions")
    assert unchanged.status_code == 201, unchanged.text
    assert unchanged.json()["id"] == original_session_id
    session = unchanged.json()
    automatic = client.post(
        f"/api/assignment-review-sessions/{session['id']}/auto-confirm",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert automatic.status_code == 200, automatic.text
    assert set(automatic.json()["confirmed"]) == {
        "classes",
        "due_at",
        "total_score",
        "reference_answers",
        "structured_rubrics",
    }
    assert automatic.json()["skipped"] == {}
    session["review_version"] = automatic.json()["review_version"]
    automatic_bundle = client.get(f"/api/assignments/{assignment.id}/review-bundle").json()
    assert all(
        item["origin"] == "system_auto"
        for item in automatic_bundle["confirmations"]
        if item["type"] != "legacy_binding"
    )
    answer.content_hash = "5" * 64
    db.commit()
    changed_content = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    assert "reference_answers" in changed_content["confirmations"]
    assert "answer_sources" not in changed_content["confirmations"]
    answer.content_hash = "3" * 64
    answer.normalized_content = "3"
    db.commit()
    semantic_change = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    assert "reference_answers" not in semantic_change["confirmations"]
    answer.normalized_content = "2"
    db.commit()
    restored_content = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    assert "reference_answers" in restored_content["confirmations"]
    original_rubric_semantics = semantic_normalize(_rubric_content_payload(db, structured))
    replacement_answer = ReferenceAnswerVersion(
        question_id=question.id,
        source_type=answer.source_type,
        raw_content="different OCR formatting",
        normalized_content="2",
        structured_content=answer.structured_content,
        content_hash="6" * 64,
        version=2,
        provenance=answer.provenance,
        created_by=actor.id,
        status="confirmed",
        teacher_confirmed_at=now_utc(),
    )
    db.add(replacement_answer)
    db.flush()
    replacement_rubric = StructuredRubricVersion(
        question_id=question.id,
        question_version="2",
        reference_answer_version_id=replacement_answer.id,
        rubric_version=2,
        title=structured.title,
        total_points=structured.total_points,
        status="confirmed",
        content_hash="7" * 64,
        created_by=actor.id,
        confirmed_by=actor.id,
        confirmed_at=now_utc(),
    )
    db.add(replacement_rubric)
    db.flush()
    db.add(
        RubricCriterion(
            rubric_version_id=replacement_rubric.id,
            stable_key="answer",
            title="答案正确",
            max_points=10,
            display_order=1,
            criterion_type="answer",
            required=True,
            validation_mode="manual",
            validation_rule={"answer_type": "exact_scalar"},
        )
    )
    answer.status = "retired"
    structured.status = "retired"
    db.commit()
    assert (
        semantic_normalize(_rubric_content_payload(db, replacement_rubric))
        == original_rubric_semantics
    )
    equivalent_replacement = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    assert {"reference_answers", "structured_rubrics"} <= set(
        equivalent_replacement["confirmations"]
    )
    assignment.description = "不属于单独确认项的发布内容发生变化"
    db.commit()
    recreated = client.post(f"/api/assignments/{assignment.id}/review-sessions")
    assert recreated.status_code == 201, recreated.text
    session = recreated.json()
    assert session["id"] != original_session_id
    assert set(session["confirmations"]) == {
        "classes",
        "due_at",
        "total_score",
        "reference_answers",
        "structured_rubrics",
    }
    inherited_bundle = client.get(f"/api/assignments/{assignment.id}/review-bundle").json()
    assert all(
        item["origin"] == "inherited" and item["inherited"] is True
        for item in inherited_bundle["confirmations"]
    )
    binding = client.post(
        f"/api/assignment-review-sessions/{session['id']}/rubric-binding",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert binding.status_code == 200, binding.text
    assert binding.json()["manual_review_required"] is True
    assert binding.json()["conversion_warnings"] == ["VALIDATION_RULE_NOT_LOSSLESS"]
    draft_binding_bundle = client.get(f"/api/assignments/{assignment.id}/review-bundle").json()
    assert draft_binding_bundle["binding"]["status"] == "confirmed"
    assert next(
        item
        for item in draft_binding_bundle["confirmations"]
        if item["type"] == "legacy_binding"
    )["origin"] == "system_auto"
    assert draft_binding_bundle["binding"]["projection_current"] is True
    assert draft_binding_bundle["binding"]["projection_reason"] is None
    session = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    repeated_binding = client.post(
        f"/api/assignment-review-sessions/{session['id']}/rubric-binding",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert repeated_binding.status_code == 200, repeated_binding.text
    assert repeated_binding.json()["id"] == binding.json()["id"]

    binding_row = db.get(AssignmentRubricPublicationBinding, uuid.UUID(binding.json()["id"]))
    assert binding_row is not None
    binding_row.status = "stale"
    db.commit()
    rebuilt_stale_binding = client.post(
        f"/api/assignment-review-sessions/{session['id']}/rubric-binding",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert rebuilt_stale_binding.status_code == 200, rebuilt_stale_binding.text
    assert rebuilt_stale_binding.json()["id"] != binding.json()["id"]
    binding = rebuilt_stale_binding
    binding_row = db.get(AssignmentRubricPublicationBinding, uuid.UUID(binding.json()["id"]))
    assert binding_row is not None
    session = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    replacement_rubric.title = "语义内容漂移"
    db.commit()
    source_drift = client.post(
        f"/api/assignment-rubric-publication-bindings/{binding.json()['id']}/confirm",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert source_drift.status_code == 409
    assert source_drift.json()["code"] == "BINDING_PROJECTION_STALE"
    replacement_rubric.title = structured.title
    db.commit()

    original_loss_report = list(binding_row.loss_report or [])
    binding_row.loss_report = original_loss_report + [
        {
            "code": "UNKNOWN_LOSS",
            "teacher_message": "unknown",
            "technical": {},
        }
    ]
    db.commit()
    loss_drift = client.post(
        f"/api/assignment-rubric-publication-bindings/{binding.json()['id']}/confirm",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert loss_drift.status_code == 409
    assert loss_drift.json()["code"] == "BINDING_PROJECTION_STALE"
    binding_row.loss_report = original_loss_report
    db.commit()

    rubric_item_id = uuid.UUID(binding.json()["mapping"][0]["criteria"][0]["rubric_item_id"])
    legacy_item = db.get(RubricItem, rubric_item_id)
    assert legacy_item is not None
    original_item_title = legacy_item.title
    legacy_item.title = "legacy target drift"
    db.commit()
    target_drift = client.post(
        f"/api/assignment-rubric-publication-bindings/{binding.json()['id']}/confirm",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert target_drift.status_code == 409
    assert target_drift.json()["code"] == "BINDING_PROJECTION_STALE"
    legacy_item.title = original_item_title
    db.commit()

    confirmed = client.post(
        f"/api/assignment-rubric-publication-bindings/{binding.json()['id']}/confirm",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert confirmed.status_code == 409
    assert confirmed.json()["code"] == "BINDING_NOT_CONFIRMABLE"
    automatic_binding_bundle = client.get(f"/api/assignments/{assignment.id}/review-bundle").json()
    assert automatic_binding_bundle["binding"]["projection_current"] is True
    assert automatic_binding_bundle["binding"]["projection_reason"] is None
    automatic_legacy_confirmation = next(
        item
        for item in automatic_binding_bundle["confirmations"]
        if item["type"] == "legacy_binding"
    )
    assert automatic_legacy_confirmation["origin"] == "system_auto"
    assert automatic_legacy_confirmation["binding_id"] == automatic_binding_bundle["binding"]["id"]
    assert (
        automatic_legacy_confirmation["source_binding_hash"]
        == automatic_binding_bundle["binding"]["source_binding_hash"]
    )
    session = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    manual_blocker = GenerationIssue(
        owner_id=actor.id,
        assignment_id=assignment.id,
        job_id=job.id,
        draft_revision_id=revision.id,
        stage="teacher_review",
        severity="blocking",
        code="TEST_MANUAL_BLOCKER",
        message="first blocking fact",
        entity_type="assignment",
        entity_id=str(assignment.id),
        resolution_status="open",
    )
    db.add(manual_blocker)
    db.commit()
    refreshed = client.post(
        f"/api/assignment-review-sessions/{session['id']}/refresh",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    session = refreshed.json()
    open_bundle = client.get(f"/api/assignments/{assignment.id}/review-bundle").json()
    assert open_bundle["status"] == "action_required"
    open_blockers = [
        item for item in open_bundle["blockers"] if item["code"] == "TEST_MANUAL_BLOCKER"
    ]
    assert len(open_blockers) == 1
    assert open_blockers[0]["status"] == "open"
    first_item = db.scalar(
        select(AssignmentReviewItem).where(
            AssignmentReviewItem.review_session_id == uuid.UUID(session["id"]),
            AssignmentReviewItem.issue_code == "TEST_MANUAL_BLOCKER",
            AssignmentReviewItem.source_hash == open_blockers[0]["source_hash"],
        )
    )
    assert first_item is not None
    resolved = client.patch(
        f"/api/assignment-review-items/{first_item.id}/disposition",
        json={
            "expected_review_version": session["review_version"],
            "action": "resolve_manual",
            "note": "teacher resolved the first fact",
        },
    )
    assert resolved.status_code == 200, resolved.text
    session["review_version"] = resolved.json()["review_version"]
    resolved_bundle = client.get(f"/api/assignments/{assignment.id}/review-bundle").json()
    assert "TEST_MANUAL_BLOCKER" not in {item["code"] for item in resolved_bundle["blockers"]}

    manual_blocker.message = "second blocking fact"
    db.commit()
    review_row = db.get(AssignmentReviewSession, uuid.UUID(session["id"]))
    assert review_row is not None
    second_issue = next(
        item for item in generated_issues(db, review_row) if item["code"] == "TEST_MANUAL_BLOCKER"
    )
    assert second_issue["source_hash"] != first_item.source_hash
    second_item = AssignmentReviewItem(
        review_session_id=review_row.id,
        section=second_issue["section"],
        entity_type=second_issue["entity"],
        entity_id=second_issue["entity_id"],
        severity=second_issue["severity"],
        issue_code=second_issue["code"],
        title=second_issue["code"].replace("_", " "),
        message=second_issue["message"],
        evidence=second_issue["evidence"],
        source_hash=second_issue["source_hash"],
        status="open",
    )
    db.add(second_item)
    db.commit()
    db.refresh(first_item)
    assert first_item.status == "resolved"
    changed_bundle = client.get(f"/api/assignments/{assignment.id}/review-bundle").json()
    changed_blockers = [
        item for item in changed_bundle["blockers"] if item["code"] == "TEST_MANUAL_BLOCKER"
    ]
    assert len(changed_blockers) == 1
    assert changed_blockers[0]["source_hash"] == second_issue["source_hash"]
    assert changed_blockers[0]["status"] == "open"
    resolved_second = client.patch(
        f"/api/assignment-review-items/{second_item.id}/disposition",
        json={
            "expected_review_version": session["review_version"],
            "action": "resolve_manual",
            "note": "teacher resolved the changed fact",
        },
    )
    assert resolved_second.status_code == 200, resolved_second.text
    session["review_version"] = resolved_second.json()["review_version"]
    counts_before_bundle_reads = tuple(
        db.scalar(select(func.count(model.id)))
        for model in (
            AssignmentReviewItem,
            AssignmentExplicitConfirmation,
            AssignmentRubricPublicationBinding,
            AuditLog,
        )
    )
    ready_after_resolution = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    repeated_ready_after_resolution = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    assert ready_after_resolution.status_code == 200
    assert ready_after_resolution.json() == repeated_ready_after_resolution.json()
    assert ready_after_resolution.json()["status"] == "ready_to_publish"
    assert "TEST_MANUAL_BLOCKER" not in {
        item["code"] for item in ready_after_resolution.json()["blockers"]
    }
    assert counts_before_bundle_reads == tuple(
        db.scalar(select(func.count(model.id)))
        for model in (
            AssignmentReviewItem,
            AssignmentExplicitConfirmation,
            AssignmentRubricPublicationBinding,
            AuditLog,
        )
    )
    db.refresh(binding_row)
    confirmed_loss_report = list(binding_row.loss_report or [])
    binding_row.loss_report = confirmed_loss_report + [
        {"code": "POST_CONFIRM_DRIFT", "teacher_message": "drift", "technical": {}}
    ]
    db.commit()
    stale_prepare = client.post(
        f"/api/assignment-review-sessions/{session['id']}/prepare-publication",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert stale_prepare.status_code == 409
    assert stale_prepare.json()["code"] == "BINDING_PROJECTION_STALE"
    binding_row.loss_report = confirmed_loss_report
    db.commit()
    prepared = client.post(
        f"/api/assignment-review-sessions/{session['id']}/prepare-publication",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert prepared.status_code == 200, prepared.text
    repeated_prepared = client.post(
        f"/api/assignment-review-sessions/{session['id']}/prepare-publication",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert repeated_prepared.status_code == 200, repeated_prepared.text
    assert repeated_prepared.json()["id"] == prepared.json()["id"]
    snapshot = db.get(AssignmentPublishReadinessSnapshot, uuid.UUID(prepared.json()["id"]))
    assert snapshot is not None
    snapshot.status = "expired"
    snapshot.expires_at = now_utc() - timedelta(minutes=1)
    snapshot.invalidated_at = now_utc()
    db.commit()
    renewed_prepared = client.post(
        f"/api/assignment-review-sessions/{session['id']}/prepare-publication",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert renewed_prepared.status_code == 200, renewed_prepared.text
    assert renewed_prepared.json()["id"] == prepared.json()["id"]
    assert renewed_prepared.json()["status"] == "ready"
    db.refresh(assignment)
    assert assignment.status == "draft"
    legacy_item.title = "post-confirm target drift"
    db.commit()
    stale_publish = client.post(
        f"/api/assignments/{assignment.id}/publish",
        json={
            "readiness_snapshot_id": prepared.json()["id"],
            "readiness_hash": prepared.json()["readiness_hash"],
            "expected_assignment_updated_at": assignment.updated_at.isoformat(),
            "explicit_confirmation": True,
        },
    )
    assert stale_publish.status_code == 409
    assert stale_publish.json()["code"] == "READINESS_STALE"
    legacy_item.title = original_item_title
    db.commit()
    published = client.post(
        f"/api/assignments/{assignment.id}/publish",
        json={
            "readiness_snapshot_id": prepared.json()["id"],
            "readiness_hash": prepared.json()["readiness_hash"],
            "expected_assignment_updated_at": assignment.updated_at.isoformat(),
            "explicit_confirmation": True,
        },
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    repeated = client.post(
        f"/api/assignments/{assignment.id}/publish",
        json={
            "readiness_snapshot_id": prepared.json()["id"],
            "readiness_hash": prepared.json()["readiness_hash"],
            "expected_assignment_updated_at": assignment.updated_at.isoformat(),
            "explicit_confirmation": True,
        },
    )
    assert repeated.status_code == 200


def test_review_bundle_is_read_only_and_selects_current_formal_lifecycle() -> None:
    client.get("/api/classes")
    db = SessionLocal()
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor is not None
    school_class = SchoolClass(
        owner_id=actor.id,
        name="review bundle class",
        status=ArchiveStatus.active,
    )
    assignment = Assignment(
        owner_id=actor.id,
        title="review bundle lifecycle",
        total_score=10,
        due_at=now_utc() + timedelta(days=7),
    )
    db.add_all([school_class, assignment])
    db.flush()
    db.add(AssignmentClass(assignment_id=assignment.id, class_id=school_class.id))
    paper = PaperVersion(assignment_id=assignment.id, version=1, created_by=actor.id)
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    job = AssignmentGenerationJob(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation=1,
        status="review_required",
        idempotency_key="review-bundle-lifecycle",
        request_fingerprint="a" * 64,
        source_snapshot_hash="b" * 64,
        provider_config_version="test",
        prompt_version="test",
        schema_version="test",
    )
    db.add(job)
    db.flush()
    revision = AssignmentDraftRevision(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        revision=1,
        source_snapshot_hash=job.source_snapshot_hash,
        created_by_type="teacher",
        created_by=actor.id,
    )
    question = Question(
        paper_version_id=paper.id,
        question_number="1",
        display_order=1,
        question_type="calculation",
        content_text="1 + 1",
        max_score=10,
    )
    db.add_all([revision, question])
    db.flush()
    confirmed_answer = ReferenceAnswerVersion(
        question_id=question.id,
        source_type="teacher_official",
        raw_content="2",
        normalized_content="2",
        content_hash="c" * 64,
        version=1,
        provenance={},
        created_by=actor.id,
        status="confirmed",
        teacher_confirmed_at=now_utc(),
    )
    db.add(confirmed_answer)
    db.flush()
    confirmed_rubric = StructuredRubricVersion(
        question_id=question.id,
        question_version="1",
        reference_answer_version_id=confirmed_answer.id,
        rubric_version=1,
        title="已确认评分标准",
        total_points=10,
        status="confirmed",
        content_hash="d" * 64,
        created_by=actor.id,
        confirmed_by=actor.id,
        confirmed_at=now_utc(),
    )
    db.add(confirmed_rubric)
    db.flush()
    criterion = RubricCriterion(
        rubric_version_id=confirmed_rubric.id,
        stable_key="answer",
        title="答案正确",
        max_points=10,
        display_order=1,
        criterion_type="answer",
        required=True,
        validation_mode="manual",
        validation_rule={},
    )
    answer_candidate = AssignmentAnswerDraftCandidate(
        id=uuid.uuid4(),
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        question_id=question.id,
        question_version="1",
        candidate_version=1,
        source_type="teacher_official",
        raw_content="2",
        normalized_content="2",
        confidence=1,
        status="accepted",
        source_snapshot_hash=job.source_snapshot_hash,
        materialized_reference_answer_id=confirmed_answer.id,
    )
    rubric_candidate = AssignmentRubricDraftCandidate(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        question_id=question.id,
        question_version="1",
        answer_candidate_id=answer_candidate.id,
        candidate_version=1,
        title="评分标准候选",
        scoring_mode="points",
        total_points=10,
        confidence=1,
        status="accepted",
        source_snapshot_hash=job.source_snapshot_hash,
        materialized_structured_rubric_id=confirmed_rubric.id,
    )
    db.add_all([criterion, answer_candidate, rubric_candidate])
    db.commit()

    def domain_counts() -> tuple[int, ...]:
        return tuple(
            len(list(db.scalars(select(model))))
            for model in (
                AssignmentReviewSession,
                AssignmentReviewItem,
                AssignmentRubricPublicationBinding,
                AssignmentExplicitConfirmation,
                AuditLog,
                AssignmentAnswerDraftCandidate,
                AssignmentRubricDraftCandidate,
                ReferenceAnswerVersion,
                StructuredRubricVersion,
            )
        )

    counts_before = domain_counts()
    missing_review = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    repeated_missing_review = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    assert missing_review.status_code == 200, missing_review.text
    assert repeated_missing_review.status_code == 200, repeated_missing_review.text
    missing_payload = missing_review.json()
    assert missing_payload == repeated_missing_review.json()
    assert (
        missing_payload["version"]["bundle_hash"]
        == repeated_missing_review.json()["version"]["bundle_hash"]
    )
    assert missing_payload["status"] == "missing_review"
    assert len(missing_payload["questions"]) == 1
    missing_question = missing_payload["questions"][0]
    assert missing_question["answer"]["candidate"]["id"] == str(answer_candidate.id)
    assert missing_question["rubric"]["candidate"]["id"] == str(rubric_candidate.id)
    assert missing_question["answer"]["materialized"]["id"] == str(confirmed_answer.id)
    assert missing_question["rubric"]["materialized"]["id"] == str(confirmed_rubric.id)
    assert domain_counts() == counts_before

    created = client.post(f"/api/assignments/{assignment.id}/review-sessions")
    assert created.status_code == 201, created.text
    session = created.json()
    for kind in (
        "classes",
        "due_at",
        "total_score",
        "file_roles",
        "answer_sources",
        "paper_version",
        "reference_answers",
        "structured_rubrics",
    ):
        response = client.post(
            f"/api/assignment-review-sessions/{session['id']}/confirm/{kind}",
            json={
                "expected_review_version": session["review_version"],
                "explicit_confirmation": True,
            },
        )
        assert response.status_code == 200, response.text
        session["review_version"] = response.json()["review_version"]
    binding = client.post(
        f"/api/assignment-review-sessions/{session['id']}/rubric-binding",
        json={"expected_review_version": session["review_version"], "explicit_confirmation": True},
    )
    assert binding.status_code == 200, binding.text
    assert binding.json()["status"] == "confirmed"
    assert binding.json()["manual_review_required"] is False
    assert binding.json()["loss_report"] == []
    session = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    old_binding = db.get(AssignmentRubricPublicationBinding, uuid.UUID(binding.json()["id"]))
    assert old_binding is not None
    old_binding.projection_profile = "legacy-unverified"
    old_confirmation = db.scalar(
        select(AssignmentExplicitConfirmation).where(
            AssignmentExplicitConfirmation.review_session_id == uuid.UUID(session["id"]),
            AssignmentExplicitConfirmation.confirmation_type == "legacy_binding",
        )
    )
    assert old_confirmation is not None
    old_confirmation.fingerprint_schema_version = None
    old_confirmation.confirmation_origin = "legacy_origin"
    db.commit()
    invalid_bundle = client.get(f"/api/assignments/{assignment.id}/review-bundle").json()
    assert invalid_bundle["status"] == "action_required"
    assert invalid_bundle["binding"]["projection_current"] is False
    assert invalid_bundle["binding"]["projection_reason"] == "BINDING_PROJECTION_STALE"
    assert "legacy_binding" not in {item["type"] for item in invalid_bundle["confirmations"]}
    bypass = client.post(
        f"/api/assignment-review-sessions/{session['id']}/prepare-publication",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert bypass.status_code == 409
    assert bypass.json()["code"] == "BINDING_PROJECTION_STALE"
    rebuilt = client.post(
        f"/api/assignment-review-sessions/{session['id']}/rubric-binding",
        json={
            "expected_review_version": session["review_version"],
            "explicit_confirmation": True,
        },
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["id"] != binding.json()["id"]
    db.refresh(old_binding)
    assert old_binding.status == "stale"
    assert old_binding.invalidated_at is not None
    assert rebuilt.json()["status"] == "confirmed"

    ready_counts_before = domain_counts()
    ready_response = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    assert ready_response.status_code == 200, ready_response.text
    assert domain_counts() == ready_counts_before
    ready_bundle = ready_response.json()
    assert ready_bundle["binding"]["status"] == "confirmed", ready_bundle["binding"]
    assert ready_bundle["binding"]["projection_current"] is True
    assert ready_bundle["binding"]["projection_reason"] is None
    automatic_legacy_confirmation = next(
        item for item in ready_bundle["confirmations"] if item["type"] == "legacy_binding"
    )
    assert automatic_legacy_confirmation["origin"] == "system_auto"
    assert automatic_legacy_confirmation["binding_id"] == ready_bundle["binding"]["id"]
    assert (
        automatic_legacy_confirmation["source_binding_hash"]
        == ready_bundle["binding"]["source_binding_hash"]
    )
    assert all(
        item["binding_id"] is None and item["source_binding_hash"] is None
        for item in ready_bundle["confirmations"]
        if item["type"] != "legacy_binding"
    )
    assert not ready_bundle["blockers"], [
        (item["code"], item["status"]) for item in ready_bundle["blockers"]
    ]
    assert ready_bundle["status"] == "ready_to_publish"
    ready_question = ready_bundle["questions"][0]
    ready_criterion = ready_question["rubric"]["selected"]["criteria"][0]
    assert ready_question["rubric"]["selected"]["total_points"] == "10.00"
    assert ready_criterion["points"] == "10.00"
    assert ready_criterion["key"] == "answer"
    assert ready_criterion["title"] == "答案正确"
    assert ready_criterion["validation_rule"] == {}
    assert ready_question["rubric"]["selected"]["source"] == {
        "kind": "structured_rubric",
        "label": "结构化评分标准",
    }
    assert ready_question["answer"]["selected"]["source"]["label"] == "教师确认答案"

    criterion.description = "必须展示关键计算过程"
    criterion.max_points = 9
    db.commit()
    stale_response = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    repeated_stale = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    assert stale_response.status_code == 200, stale_response.text
    assert stale_response.json() == repeated_stale.json()
    stale_bundle = stale_response.json()
    assert stale_bundle["status"] == "action_required"
    assert stale_bundle["binding"]["status"] == "stale"
    assert stale_bundle["binding"]["projection_current"] is False
    assert stale_bundle["binding"]["projection_reason"] == "BINDING_NOT_CURRENT"
    assert "structured_rubrics" not in {item["type"] for item in stale_bundle["confirmations"]}
    assert {item["code"] for item in stale_bundle["blockers"]} >= {
        "CONFIRM_STRUCTURED_RUBRICS_REQUIRED",
        "LEGACY_BINDING_STALE",
        "RUBRIC_POINTS_MISMATCH",
    }
    changed_criterion = stale_bundle["questions"][0]["rubric"]["selected"]["criteria"][0]
    assert changed_criterion["description"] == "必须展示关键计算过程"
    assert stale_bundle["version"]["bundle_hash"] != ready_bundle["version"]["bundle_hash"]

    criterion.description = None
    criterion.max_points = 10
    db.commit()
    restored_ready = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    assert restored_ready.status_code == 200, restored_ready.text
    assert restored_ready.json()["status"] == "ready_to_publish"

    confirmed_answer.normalized_content = "2.0"
    db.commit()
    answer_stale = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    assert answer_stale.status_code == 200, answer_stale.text
    assert answer_stale.json()["status"] == "action_required"
    assert answer_stale.json()["binding"]["status"] == "stale"
    assert "reference_answers" not in {
        item["type"] for item in answer_stale.json()["confirmations"]
    }
    assert {item["code"] for item in answer_stale.json()["blockers"]} >= {
        "CONFIRM_REFERENCE_ANSWERS_REQUIRED",
        "LEGACY_BINDING_STALE",
    }
    confirmed_answer.normalized_content = "2"
    db.commit()

    assignment.total_score = 20
    draft_only_question = Question(
        paper_version_id=paper.id,
        question_number="2",
        display_order=2,
        question_type="calculation",
        content_text="2 + 2",
        max_score=10,
    )
    db.add(draft_only_question)
    db.flush()
    draft_answer = ReferenceAnswerVersion(
        id=uuid.uuid4(),
        question_id=question.id,
        source_type="teacher_official",
        raw_content="two",
        normalized_content="two",
        content_hash="e" * 64,
        version=2,
        provenance={},
        created_by=actor.id,
        status="draft",
    )
    draft_rubric = StructuredRubricVersion(
        id=uuid.uuid4(),
        question_id=question.id,
        question_version="2",
        reference_answer_version_id=draft_answer.id,
        rubric_version=2,
        title="较新草稿评分标准",
        total_points=10,
        status="draft",
        content_hash="f" * 64,
        created_by=actor.id,
    )
    only_draft_answer = ReferenceAnswerVersion(
        id=uuid.uuid4(),
        question_id=draft_only_question.id,
        source_type="teacher_official",
        raw_content="4",
        normalized_content="4",
        content_hash="1" * 64,
        version=1,
        provenance={},
        created_by=actor.id,
        status="draft",
    )
    only_draft_rubric = StructuredRubricVersion(
        id=uuid.uuid4(),
        question_id=draft_only_question.id,
        question_version="1",
        reference_answer_version_id=only_draft_answer.id,
        rubric_version=1,
        title="仅草稿评分标准",
        total_points=10,
        status="draft",
        content_hash="2" * 64,
        created_by=actor.id,
    )
    retired_answer = ReferenceAnswerVersion(
        id=uuid.uuid4(),
        question_id=question.id,
        source_type="teacher_official",
        raw_content="retired answer",
        normalized_content="retired answer",
        content_hash="7" * 64,
        version=3,
        provenance={},
        created_by=actor.id,
        status="retired",
    )
    retired_rubric = StructuredRubricVersion(
        id=uuid.uuid4(),
        question_id=question.id,
        question_version="3",
        reference_answer_version_id=retired_answer.id,
        rubric_version=3,
        title="已退役评分标准",
        total_points=10,
        status="retired",
        content_hash="8" * 64,
        created_by=actor.id,
    )
    candidate = AssignmentQuestionExtractionCandidate(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        paper_version_id=paper.id,
        candidate_version=1,
        question_number="2",
        question_type="calculation",
        content_text="2 + 2",
        overall_confidence=1,
        extraction_method="teacher",
        status="accepted",
        source_snapshot_hash=job.source_snapshot_hash,
        materialized_question_id=draft_only_question.id,
    )
    retired_candidate = AssignmentQuestionExtractionCandidate(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        paper_version_id=paper.id,
        candidate_version=2,
        question_number="retired",
        question_type="calculation",
        content_text="retired candidate",
        overall_confidence=1,
        extraction_method="teacher",
        status="retired",
        source_snapshot_hash=job.source_snapshot_hash,
    )
    stale_candidate = AssignmentQuestionExtractionCandidate(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        paper_version_id=paper.id,
        candidate_version=3,
        question_number="stale",
        question_type="calculation",
        content_text="stale candidate",
        overall_confidence=1,
        extraction_method="teacher",
        status="stale",
        source_snapshot_hash=job.source_snapshot_hash,
    )
    draft_answer_candidate = AssignmentAnswerDraftCandidate(
        id=uuid.uuid4(),
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        question_id=draft_only_question.id,
        question_version="1",
        candidate_version=1,
        source_type="ai_generated",
        raw_content="4",
        normalized_content="4",
        confidence=1,
        status="accepted",
        source_snapshot_hash=job.source_snapshot_hash,
        materialized_reference_answer_id=only_draft_answer.id,
    )
    draft_rubric_candidate = AssignmentRubricDraftCandidate(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        question_id=draft_only_question.id,
        question_version="1",
        answer_candidate_id=draft_answer_candidate.id,
        candidate_version=1,
        title="第二题评分标准候选",
        scoring_mode="points",
        total_points=10,
        confidence=1,
        status="accepted",
        source_snapshot_hash=job.source_snapshot_hash,
        materialized_structured_rubric_id=only_draft_rubric.id,
    )
    stale_answer_candidate = AssignmentAnswerDraftCandidate(
        id=uuid.uuid4(),
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        question_id=draft_only_question.id,
        question_version="2",
        candidate_version=2,
        source_type="ai_generated",
        raw_content="stale answer candidate",
        normalized_content="stale answer candidate",
        confidence=1,
        status="stale",
        source_snapshot_hash=job.source_snapshot_hash,
    )
    rejected_rubric_candidate = AssignmentRubricDraftCandidate(
        owner_id=actor.id,
        assignment_id=assignment.id,
        generation_job_id=job.id,
        draft_revision_id=revision.id,
        question_id=draft_only_question.id,
        question_version="2",
        answer_candidate_id=stale_answer_candidate.id,
        candidate_version=2,
        title="已拒绝评分标准候选",
        scoring_mode="points",
        total_points=10,
        confidence=1,
        status="rejected",
        source_snapshot_hash=job.source_snapshot_hash,
    )
    db.add_all(
        [
            draft_answer,
            draft_rubric,
            only_draft_answer,
            only_draft_rubric,
            retired_answer,
            retired_rubric,
            candidate,
            retired_candidate,
            stale_candidate,
            draft_answer_candidate,
            draft_rubric_candidate,
            stale_answer_candidate,
            rejected_rubric_candidate,
        ]
    )
    db.commit()

    before_sessions = list(
        db.scalars(
            select(AssignmentReviewSession).where(
                AssignmentReviewSession.assignment_id == assignment.id
            )
        )
    )
    before_audit_count = len(
        list(db.scalars(select(AuditLog).where(AuditLog.resource_id == assignment.id)))
    )
    first = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    second = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    bundle = first.json()
    assert bundle == second.json()
    assert bundle["status"] == "action_required"
    by_number = {item["number"]: item for item in bundle["questions"]}
    assert by_number["1"]["rubric"]["selected"]["id"] == str(confirmed_rubric.id)
    assert by_number["1"]["answer"]["selected"]["id"] == str(confirmed_answer.id)
    assert {item["id"] for item in by_number["1"]["rubric"]["history"]} == {
        str(confirmed_rubric.id),
        str(draft_rubric.id),
        str(retired_rubric.id),
    }
    assert by_number["1"]["rubric"]["selected"]["id"] != str(retired_rubric.id)
    assert by_number["2"]["answer"]["candidate"]["id"] == str(draft_answer_candidate.id)
    assert by_number["2"]["rubric"]["candidate"]["id"] == str(draft_rubric_candidate.id)
    assert by_number["2"]["rubric"]["candidate"]["status"] == "accepted"
    assert by_number["2"]["rubric"]["candidate"]["total_points"] == "10.00"
    assert by_number["2"]["rubric"]["selected"]["id"] == str(only_draft_rubric.id)
    assert by_number["2"]["answer"]["selected"]["id"] == str(only_draft_answer.id)
    assert by_number["2"]["rubric"]["materialized"]["id"] == str(only_draft_rubric.id)
    assert by_number["2"]["answer"]["materialized"]["id"] == str(only_draft_answer.id)
    assert {item["id"] for item in by_number["2"]["answer"]["candidate_history"]} == {
        str(draft_answer_candidate.id),
        str(stale_answer_candidate.id),
    }
    assert {item["id"] for item in by_number["2"]["rubric"]["candidate_history"]} == {
        str(draft_rubric_candidate.id),
        str(rejected_rubric_candidate.id),
    }
    assert {item["code"] for item in bundle["blockers"]} >= {
        "REFERENCE_ANSWER_UNCONFIRMED",
        "STRUCTURED_RUBRIC_UNCONFIRMED",
    }
    assert all(
        not (
            item["code"] == "QUESTION_NOT_MATERIALIZED"
            and item["entity_id"] in {str(retired_candidate.id), str(stale_candidate.id)}
        )
        for item in bundle["blockers"]
    )
    central = {str(row["question"].id): row for row in selected_versions(db, paper.id)}
    assert central[str(question.id)]["rubric"].id == confirmed_rubric.id
    assert central[str(draft_only_question.id)]["rubric"].id == only_draft_rubric.id
    assert len(
        list(
            db.scalars(
                select(AssignmentReviewSession).where(
                    AssignmentReviewSession.assignment_id == assignment.id
                )
            )
        )
    ) == len(before_sessions)
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.resource_id == assignment.id)))) == (
        before_audit_count
    )

    only_draft_rubric.title = "仅草稿评分标准（已修改）"
    db.commit()
    changed = client.get(f"/api/assignments/{assignment.id}/review-bundle")
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"]["bundle_hash"] != bundle["version"]["bundle_hash"]
