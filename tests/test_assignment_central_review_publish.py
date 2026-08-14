import inspect as pyinspect
import uuid
from datetime import timedelta
from importlib import import_module
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api.assignment_central_review import (
    STRUCTURED_SET_WRITE_LOCK_ORDER,
    Disposition,
    PublishInput,
    _issue_blocks_review_bundle,
    digest,
    disposition,
    owned_assignment,
    owned_session,
    prepare_publication,
    validate_current_structured_set_under_locks,
)
from app.api.domain import ApiProblem
from app.api.structured_rubrics import (
    _confirm_question_package_against_bundle,
    confirm_question_package,
    confirm_rubric,
    update_reference,
    update_rubric,
)
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentClass,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AssignmentReviewItem,
    AssignmentReviewSession,
    PaperVersion,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    SchoolClass,
    StructuredRubricSetItem,
    StructuredRubricVersion,
    User,
    now_utc,
)
from app.question_versions import question_version_token
from app.semantic_content import (
    reference_answer_semantic_payload,
    semantic_hash,
    semantic_normalize,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text

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


def test_structured_set_write_lock_order_is_postgresql_safe() -> None:
    assert STRUCTURED_SET_WRITE_LOCK_ORDER == (
        "assignment",
        "snapshot",
        "session",
        "paper",
        "questions",
        "structured_set",
        "formal_versions",
        "criteria",
        "structured_set_items",
    )
    validator_source = pyinspect.getsource(validate_current_structured_set_under_locks)
    markers = (
        "select(PaperVersion)",
        "select(Question)",
        "select(ReferenceAnswerVersion)",
        "select(StructuredRubricVersion)",
        "select(RubricCriterion)",
        "select(StructuredRubricSet)",
        "select(StructuredRubricSetItem)",
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
    package_route_source = pyinspect.getsource(confirm_question_package)
    assert package_route_source.index("owned_assignment(") < package_route_source.index(
        "current_bundle = review_bundle("
    )
    package_source = pyinspect.getsource(_confirm_question_package_against_bundle)
    assert (
        package_source.index("select(Question)")
        < package_source.index("select(ReferenceAnswerVersion)")
        < package_source.index("select(StructuredRubricVersion)")
        < package_source.index("select(RubricCriterion)")
    )
    assert package_source.count(".with_for_update()") >= 4
    assert ".with_for_update()" in pyinspect.getsource(update_reference)
    assert ".with_for_update()" in pyinspect.getsource(update_rubric)
    confirm_rubric_source = pyinspect.getsource(confirm_rubric)
    assert ".execution_options(populate_existing=True)" in confirm_rubric_source
    assert ".with_for_update()" in confirm_rubric_source


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
            structured_set_hash="d" * 64,
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


def _structured_publication_fixture() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    client.get("/api/classes")
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
        assert actor is not None
        school_class = SchoolClass(
            owner_id=actor.id,
            name=f"Structured-only-{uuid.uuid4()}",
            status=ArchiveStatus.active,
        )
        assignment = Assignment(
            owner_id=actor.id,
            title="Structured-only publication",
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
            idempotency_key=f"structured-only-{uuid.uuid4()}",
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
            structured_content={},
            content_hash="3" * 64,
            version=1,
            provenance={"teacher": str(actor.id)},
            created_by=actor.id,
            status="confirmed",
            teacher_confirmed_at=now_utc(),
        )
        db.add_all([revision, answer])
        db.flush()
        rubric = StructuredRubricVersion(
            question_id=question.id,
            question_version=question_version_token(question),
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
        db.add(rubric)
        db.flush()
        criterion = RubricCriterion(
            rubric_version_id=rubric.id,
            stable_key="answer",
            title="答案正确",
            max_points=10,
            display_order=1,
            criterion_type="answer",
            required=True,
            validation_mode="manual",
            validation_rule={"answer_type": "exact_scalar"},
        )
        db.add(criterion)
        db.commit()
        return assignment.id, rubric.id, criterion.id


def _bulk_confirmation_payload(assignment_id: uuid.UUID) -> tuple[dict, dict]:
    bundle_response = client.get(f"/api/assignments/{assignment_id}/review-bundle")
    assert bundle_response.status_code == 200, bundle_response.text
    bundle = bundle_response.json()
    packages = []
    for question in bundle["questions"]:
        answer = question["answer"]["materialized"] or question["answer"]["selected"]
        rubric = question["rubric"]["materialized"] or question["rubric"]["selected"]
        assert answer is not None and rubric is not None
        packages.append(
            {
                "question_id": question["id"],
                "expected_question_content_hash": question["content_hash"],
                "reference_answer_version_id": answer["id"],
                "expected_reference_answer_content_hash": answer["content_hash"],
                "structured_rubric_version_id": rubric["id"],
                "expected_structured_rubric_content_hash": rubric["content_hash"],
            }
        )
    return bundle, {
        "expected_bundle_hash": bundle["version"]["bundle_hash"],
        "packages": packages,
        "explicit_confirmation": True,
    }


def test_teacher_can_confirm_the_complete_current_bundle_with_one_request() -> None:
    assignment_id, rubric_id, criterion_id = _structured_publication_fixture()
    with SessionLocal() as db:
        rubric = db.get(StructuredRubricVersion, rubric_id)
        assert rubric is not None
        answer = db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
        assert answer is not None
        answer.status = "draft"
        answer.teacher_confirmed_at = None
        rubric.status = "draft"
        rubric.confirmed_by = None
        rubric.confirmed_at = None
        criterion = db.get(RubricCriterion, criterion_id)
        assert criterion is not None
        criterion.criterion_type = "final_answer"
        criterion.validation_mode = "manual_only"
        criterion.validation_rule = {}
        db.commit()

    _, payload = _bulk_confirmation_payload(assignment_id)
    response = client.post(
        f"/api/assignments/{assignment_id}/confirm-all-answer-rubrics", json=payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["confirmed_count"] == 1
    with SessionLocal() as db:
        rubric = db.get(StructuredRubricVersion, rubric_id)
        assert rubric is not None and rubric.status == "confirmed"
        answer = db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
        assert answer is not None and answer.status == "confirmed"


def test_bulk_confirmation_rejects_stale_content_without_writing() -> None:
    assignment_id, rubric_id, criterion_id = _structured_publication_fixture()
    with SessionLocal() as db:
        rubric = db.get(StructuredRubricVersion, rubric_id)
        assert rubric is not None
        answer = db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
        assert answer is not None
        answer.status = "draft"
        answer.teacher_confirmed_at = None
        rubric.status = "draft"
        rubric.confirmed_by = None
        rubric.confirmed_at = None
        criterion = db.get(RubricCriterion, criterion_id)
        assert criterion is not None
        criterion.criterion_type = "final_answer"
        criterion.validation_mode = "manual_only"
        criterion.validation_rule = {}
        db.commit()

    _, payload = _bulk_confirmation_payload(assignment_id)
    payload["packages"][0]["expected_structured_rubric_content_hash"] = "0" * 64
    response = client.post(
        f"/api/assignments/{assignment_id}/confirm-all-answer-rubrics", json=payload
    )
    assert response.status_code == 409
    assert response.json()["code"] == "QUESTION_PACKAGE_STALE"
    with SessionLocal() as db:
        rubric = db.get(StructuredRubricVersion, rubric_id)
        assert rubric is not None and rubric.status == "draft"
        answer = db.get(ReferenceAnswerVersion, rubric.reference_answer_version_id)
        assert answer is not None and answer.status == "draft"


def _prepare_structured_publication(assignment_id: uuid.UUID) -> dict[str, object]:
    created = client.post(f"/api/assignments/{assignment_id}/review-sessions")
    assert created.status_code == 201, created.text
    session = created.json()
    auto = client.post(
        f"/api/assignment-review-sessions/{session['id']}/auto-confirm",
        json={"expected_review_version": session["review_version"], "explicit_confirmation": True},
    )
    assert auto.status_code == 200, auto.text
    current = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    rubric_set = client.post(
        f"/api/assignment-review-sessions/{session['id']}/structured-rubric-set",
        json={"expected_review_version": current["review_version"], "explicit_confirmation": True},
    )
    assert rubric_set.status_code == 200, rubric_set.text
    current = client.get(f"/api/assignment-review-sessions/{session['id']}").json()
    readiness = client.post(
        f"/api/assignment-review-sessions/{session['id']}/prepare-publication",
        json={"expected_review_version": current["review_version"], "explicit_confirmation": True},
    )
    assert readiness.status_code == 200, readiness.text
    return readiness.json()


def test_structured_set_is_the_only_publication_authority() -> None:
    assignment_id, _, _ = _structured_publication_fixture()
    readiness = _prepare_structured_publication(assignment_id)
    assert readiness["structured_rubric_set_id"]
    assert "binding_id" not in readiness
    published = client.post(
        f"/api/assignments/{assignment_id}/publish",
        json={
            "readiness_snapshot_id": readiness["id"],
            "readiness_hash": readiness["readiness_hash"],
            "expected_assignment_updated_at": now_utc().isoformat(),
            "explicit_confirmation": True,
        },
    )
    assert published.status_code == 200, published.text
    with SessionLocal() as db:
        assignment = db.get(Assignment, assignment_id)
        assert assignment is not None
        assert assignment.status == "published"
        assert assignment.active_structured_rubric_set_id is not None


def test_structured_set_normalizes_duplicate_question_display_order() -> None:
    assignment_id, _, _ = _structured_publication_fixture()
    with SessionLocal() as db:
        assignment = db.get(Assignment, assignment_id)
        assert assignment is not None and assignment.active_paper_version_id is not None
        actor = db.get(User, assignment.owner_id)
        assert actor is not None
        assignment.total_score = 20
        question = Question(
            paper_version_id=assignment.active_paper_version_id,
            question_number="2",
            display_order=1,
            question_type="calculation",
            content_text="2+2",
            max_score=10,
        )
        db.add(question)
        db.flush()
        answer = ReferenceAnswerVersion(
            question_id=question.id,
            source_type="teacher_official",
            raw_content="4",
            normalized_content="4",
            structured_content={},
            content_hash="5" * 64,
            version=1,
            provenance={"teacher": str(actor.id)},
            created_by=actor.id,
            status="confirmed",
            teacher_confirmed_at=now_utc(),
        )
        db.add(answer)
        db.flush()
        rubric = StructuredRubricVersion(
            question_id=question.id,
            question_version=question_version_token(question),
            reference_answer_version_id=answer.id,
            rubric_version=1,
            title="计算正确",
            total_points=10,
            status="confirmed",
            content_hash="6" * 64,
            created_by=actor.id,
            confirmed_by=actor.id,
            confirmed_at=now_utc(),
        )
        db.add(rubric)
        db.flush()
        db.add(
            RubricCriterion(
                rubric_version_id=rubric.id,
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
        db.commit()

    readiness = _prepare_structured_publication(assignment_id)
    with SessionLocal() as db:
        items = list(
            db.scalars(
                select(StructuredRubricSetItem)
                .where(
                    StructuredRubricSetItem.rubric_set_id
                    == uuid.UUID(str(readiness["structured_rubric_set_id"]))
                )
                .order_by(StructuredRubricSetItem.display_order)
            )
        )
        assert [item.display_order for item in items] == [1, 2]


def test_structured_set_content_drift_fails_publish_with_409() -> None:
    assignment_id, _, criterion_id = _structured_publication_fixture()
    readiness = _prepare_structured_publication(assignment_id)
    with SessionLocal() as db:
        criterion = db.get(RubricCriterion, criterion_id)
        assert criterion is not None
        criterion.title = "漂移后的标题"
        db.commit()
    stale = client.post(
        f"/api/assignments/{assignment_id}/publish",
        json={
            "readiness_snapshot_id": readiness["id"],
            "readiness_hash": readiness["readiness_hash"],
            "expected_assignment_updated_at": now_utc().isoformat(),
            "explicit_confirmation": True,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "READINESS_STALE"
