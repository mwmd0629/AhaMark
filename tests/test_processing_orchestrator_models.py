from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.core.config import get_settings
from app.models import CodexWorkItem, ProcessingRun, ProcessingRunCommand, ProcessingStep
from sqlalchemy import CheckConstraint, create_engine, insert, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

OrchestratorModel = ProcessingRun | ProcessingRunCommand | ProcessingStep | CodexWorkItem
ORCHESTRATOR_TABLES = {
    "processing_runs",
    "processing_run_commands",
    "processing_steps",
    "codex_work_items",
}


def _constraint_names(model: type[OrchestratorModel]) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if constraint.name is not None
    }


def _check_values(
    model: type[OrchestratorModel],
    constraint_name: str,
) -> set[str]:
    constraint = next(
        constraint
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    )
    return set(re.findall(r"'([^']+)'", str(constraint.sqltext)))


@pytest.fixture(scope="module")
def migrated_postgresql() -> Iterator[Engine]:
    database_url = os.getenv("PROCESSING_ORCHESTRATOR_PG_URL")
    if not database_url:
        pytest.skip("requires an explicitly isolated PostgreSQL database")
    database_marker = os.getenv("PROCESSING_ORCHESTRATOR_PG_MARKER", "")
    if not re.fullmatch(r"[a-z0-9]{12,64}", database_marker):
        pytest.fail(
            "PROCESSING_ORCHESTRATOR_PG_MARKER must be an explicit 12-64 character "
            "lowercase alphanumeric marker"
        )
    database_name = make_url(database_url).database or ""
    expected_database_name = f"ahamark_processing_{database_marker}"
    if database_name in {"ahamark", "ahamark_business_e2e"}:
        pytest.fail("refusing a protected application or business database")
    if database_name != expected_database_name:
        pytest.fail(
            "PROCESSING_ORCHESTRATOR_PG_URL database must exactly match the explicit marker: "
            f"{expected_database_name}"
        )

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    original_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    engine: Engine | None = None
    try:
        command.upgrade(config, "0027_semantic_projection")
        command.upgrade(config, "0029_processing_auto_confirmation")
        engine = create_engine(database_url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


def test_processing_orchestrator_metadata_contract() -> None:
    assert "idempotency_key" not in ProcessingRun.__table__.c
    assert "input_manifest" in ProcessingRun.__table__.c
    assert _constraint_names(ProcessingRun) >= {
        "uq_processing_run_batch_generation",
        "ck_processing_run_generation_positive",
        "ck_processing_run_status",
        "ck_processing_run_mode",
        "ck_processing_run_counters_nonnegative",
        "ck_processing_run_terminal_counters_bounded",
        "fk_processing_run_owner",
        "fk_processing_run_grading_batch",
    }
    assert _constraint_names(ProcessingRunCommand) >= {
        "uq_processing_run_command_owner_idempotency",
        "ck_processing_run_command_operation",
        "ck_processing_run_command_idempotency_key",
        "ck_processing_run_command_request_hash",
        "ck_processing_run_command_expected_generation_positive",
        "ck_processing_run_command_result_generation_positive",
        "ck_processing_run_command_shape",
        "fk_processing_run_command_owner",
        "fk_processing_run_command_grading_batch",
        "fk_processing_run_command_source_run",
        "fk_processing_run_command_result_run",
    }
    assert _constraint_names(ProcessingStep) >= {
        "uq_processing_step_run_scope_kind_generation",
        "ck_processing_step_generation_positive",
        "ck_processing_step_attempt_bounds",
        "ck_processing_step_kind",
        "ck_processing_step_status",
        "ck_processing_step_dispatch_lease_complete",
        "fk_processing_step_run",
        "fk_processing_step_submission",
        "fk_processing_step_student_answer",
        "fk_processing_step_recognition_job",
        "fk_processing_step_submission_processing_job",
    }
    assert _constraint_names(CodexWorkItem) >= {
        "uq_codex_work_item_processing_step",
        "ck_codex_work_item_generation_positive",
        "ck_codex_work_item_attempt_bounds",
        "ck_codex_work_item_provider",
        "ck_codex_work_item_status",
        "ck_codex_work_item_lease_state",
        "ck_codex_work_item_request_hash",
        "ck_codex_work_item_submission_audit_complete",
        "ck_codex_work_item_submission_state",
        "ck_codex_work_item_applied_refs_complete",
        "ck_codex_work_item_applied_state",
        "fk_codex_work_item_processing_step",
        "fk_codex_work_item_owner",
        "fk_codex_work_item_grading_batch",
        "fk_codex_work_item_submission",
        "fk_codex_work_item_student_answer",
        "fk_codex_work_item_grading_job",
        "fk_codex_work_item_grading_result",
    }

    for model in (ProcessingRun, ProcessingRunCommand, ProcessingStep, CodexWorkItem):
        for foreign_key in model.__table__.foreign_keys:
            assert foreign_key.ondelete == "RESTRICT"
            assert foreign_key.constraint.name is not None

    assert {index.name for index in ProcessingRun.__table__.indexes} == {
        "ix_processing_run_owner_batch_status",
        "ix_processing_run_batch_request_hash",
    }
    assert {index.name for index in ProcessingRunCommand.__table__.indexes} == {
        "ix_processing_run_command_owner_batch_created",
        "ix_processing_run_command_source_operation",
    }
    assert {index.name for index in ProcessingStep.__table__.indexes} == {
        "ix_processing_step_run_status_available",
        "ix_processing_step_submission_kind_status",
        "ix_processing_steps_submission_processing_job_id",
    }
    assert {index.name for index in CodexWorkItem.__table__.indexes} == {
        "ix_codex_work_item_claim",
        "ix_codex_work_item_owner_batch_status_available",
        "ix_codex_work_item_submission_answer_status",
    }
    assert _check_values(ProcessingRun, "ck_processing_run_status") == {
        "queued",
        "running",
        "waiting_input",
        "waiting_codex",
        "awaiting_teacher_review",
        "partially_failed",
        "failed",
        "stale",
        "cancelled",
    }
    assert _check_values(ProcessingRun, "ck_processing_run_mode") == {"codex_local"}
    assert _check_values(ProcessingRunCommand, "ck_processing_run_command_operation") == {
        "continue",
        "retry",
        "reconcile",
    }
    assert _check_values(ProcessingStep, "ck_processing_step_status") == {
        "pending",
        "dispatched",
        "running",
        "succeeded",
        "blocked_review",
        "retryable_failed",
        "terminal_failed",
        "stale",
        "cancelled",
    }
    assert _check_values(CodexWorkItem, "ck_codex_work_item_status") == {
        "queued",
        "leased",
        "submitted",
        "applied",
        "retryable_failed",
        "terminal_failed",
        "stale",
        "cancelled",
    }
    assert _check_values(CodexWorkItem, "ck_codex_work_item_provider") == {"codex_local"}
    assert ProcessingRun.__table__.c.status.default.arg == "queued"
    assert ProcessingRun.__table__.c.input_manifest.server_default is not None
    assert ProcessingRunCommand.__table__.c.request_payload.server_default is not None
    assert ProcessingStep.__table__.c.status.default.arg == "pending"
    assert CodexWorkItem.__table__.c.status.default.arg == "queued"
    assert "lease_token" not in CodexWorkItem.__table__.c
    assert "lease_token_hash" in CodexWorkItem.__table__.c


def test_processing_orchestrator_migration_is_the_single_head() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == [
        "0029_processing_auto_confirmation"
    ]


def test_processing_orchestrator_defaults_relationships_and_sqlite_constraints() -> None:
    engine = create_engine("sqlite://")
    ProcessingRun.metadata.create_all(
        engine,
        tables=[
            ProcessingRun.__table__,
            ProcessingRunCommand.__table__,
            ProcessingStep.__table__,
            CodexWorkItem.__table__,
        ],
    )
    now = datetime.now(UTC)
    owner_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    submission_id = uuid.uuid4()
    answer_id = uuid.uuid4()
    run_id = uuid.uuid4()

    run = ProcessingRun(
        id=run_id,
        owner_id=owner_id,
        grading_batch_id=batch_id,
        generation=1,
        input_version="input-v1",
        request_hash="a" * 64,
        input_manifest={"included": [], "excluded": []},
    )
    step = ProcessingStep(
        submission_id=submission_id,
        student_answer_id=answer_id,
        scope_key=f"answer:{answer_id}",
        kind="codex_suggestion",
        generation=1,
        input_version="input-v1",
        request_hash="b" * 64,
    )
    item = CodexWorkItem(
        owner_id=owner_id,
        grading_batch_id=batch_id,
        submission_id=submission_id,
        student_answer_id=answer_id,
        generation=1,
        input_version="input-v1",
        request_hash="c" * 64,
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        config_version="config-v1",
    )
    run.steps.append(step)
    step.codex_work_item = item
    command = ProcessingRunCommand(
        owner_id=owner_id,
        grading_batch_id=batch_id,
        operation="continue",
        idempotency_key="command-1",
        request_hash="d" * 64,
        request_payload={"operation": "continue"},
        result_run_id=run_id,
        result_generation=1,
    )

    with Session(engine) as session:
        session.add_all([run, command])
        session.flush()
        assert run.status == "queued"
        assert run.mode == "codex_local"
        assert run.submission_count == 0
        assert run.step_count == 0
        assert run.completed_step_count == 0
        assert run.failed_step_count == 0
        assert run.pending_codex_count == 0
        assert run.retryable is True
        assert run.input_manifest == {"included": [], "excluded": []}
        assert command.request_payload == {"operation": "continue"}
        assert step.status == "pending"
        assert step.attempt == 0
        assert step.max_attempts == 3
        assert item.status == "queued"
        assert item.provider == "codex_local"
        assert item.request_payload == {}
        assert item.response_payload is None
        assert item.retryable is True
        assert step.run is run
        assert item.step is step
        assert step.codex_work_item is item

        response_is_sql_null = session.execute(
            select(text("response_payload IS NULL")).select_from(CodexWorkItem.__table__)
        ).scalar_one()
        assert response_is_sql_null == 1
        session.commit()

    with engine.begin() as connection:
        raw_run_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO processing_runs "
                "(id, owner_id, grading_batch_id, status, mode, generation, input_version, "
                "request_hash, submission_count, step_count, completed_step_count, "
                "failed_step_count, pending_codex_count, retryable, created_at, updated_at) "
                "VALUES (:id, :owner_id, :batch_id, 'queued', 'codex_local', 2, "
                "'input-v2', :request_hash, 0, 0, 0, 0, 0, 1, :now, :now)"
            ),
            {
                "id": raw_run_id.hex,
                "owner_id": owner_id.hex,
                "batch_id": batch_id.hex,
                "request_hash": "9" * 64,
                "now": now,
            },
        )
        raw_command_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO processing_run_commands "
                "(id, owner_id, grading_batch_id, operation, idempotency_key, request_hash, "
                "result_run_id, result_generation, created_at, updated_at) "
                "VALUES (:id, :owner_id, :batch_id, 'continue', 'raw-command-default', "
                ":request_hash, :result_run_id, 2, :now, :now)"
            ),
            {
                "id": raw_command_id.hex,
                "owner_id": owner_id.hex,
                "batch_id": batch_id.hex,
                "request_hash": "8" * 64,
                "result_run_id": raw_run_id.hex,
                "now": now,
            },
        )
        assert connection.scalar(
            select(ProcessingRun.input_manifest).where(ProcessingRun.id == raw_run_id)
        ) == {}
        assert connection.scalar(
            select(ProcessingRunCommand.request_payload).where(
                ProcessingRunCommand.id == raw_command_id
            )
        ) == {}

    with Session(engine) as session:
        duplicate = ProcessingRunCommand(
            owner_id=owner_id,
            grading_batch_id=batch_id,
            operation="continue",
            idempotency_key="command-1",
            request_hash="e" * 64,
            result_run_id=run_id,
            result_generation=1,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.flush()

    with Session(engine) as session:
        invalid_retry = ProcessingRunCommand(
            owner_id=owner_id,
            grading_batch_id=batch_id,
            operation="retry",
            idempotency_key="retry-without-source",
            request_hash="f" * 64,
            result_run_id=run_id,
            result_generation=2,
        )
        session.add(invalid_retry)
        with pytest.raises(IntegrityError):
            session.flush()

    invalid_commands = [
        {
            "operation": "continue",
            "idempotency_key": " padded",
            "request_hash": "f" * 64,
            "result_run_id": run_id,
            "result_generation": 1,
        },
        {
            "operation": "continue",
            "idempotency_key": "bad-hash",
            "request_hash": "short",
            "result_run_id": run_id,
            "result_generation": 1,
        },
        {
            "operation": "reconcile",
            "idempotency_key": "reconcile-null-source",
            "request_hash": "f" * 64,
            "result_run_id": run_id,
            "expected_generation": 1,
            "result_generation": 1,
        },
        {
            "operation": "reconcile",
            "idempotency_key": "reconcile-null-expected",
            "request_hash": "f" * 64,
            "source_run_id": run_id,
            "result_run_id": run_id,
            "result_generation": 1,
        },
        {
            "operation": "reconcile",
            "idempotency_key": "reconcile-different-run",
            "request_hash": "f" * 64,
            "source_run_id": uuid.uuid4(),
            "result_run_id": run_id,
            "expected_generation": 1,
            "result_generation": 1,
        },
        {
            "operation": "reconcile",
            "idempotency_key": "reconcile-different-generation",
            "request_hash": "f" * 64,
            "source_run_id": run_id,
            "result_run_id": run_id,
            "expected_generation": 1,
            "result_generation": 2,
        },
    ]
    for command_values in invalid_commands:
        with Session(engine) as session:
            session.add(
                ProcessingRunCommand(
                    owner_id=owner_id,
                    grading_batch_id=batch_id,
                    **command_values,
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()

    with Session(engine) as session:
        session.add(
            ProcessingRunCommand(
                owner_id=owner_id,
                grading_batch_id=batch_id,
                operation="reconcile",
                idempotency_key="reconcile-same-run-generation",
                request_hash="f" * 64,
                source_run_id=run_id,
                result_run_id=run_id,
                expected_generation=1,
                result_generation=1,
            )
        )
        session.commit()

    with Session(engine) as session:
        persisted_step = session.scalar(select(ProcessingStep))
        assert persisted_step is not None
        persisted_step.dispatch_token = "lease-token"
        persisted_step.available_at = now + timedelta(seconds=1)
        with pytest.raises(IntegrityError):
            session.flush()

    with Session(engine) as session:
        persisted_item = session.scalar(select(CodexWorkItem))
        assert persisted_item is not None
        persisted_item.response_hash = "d" * 64
        with pytest.raises(IntegrityError):
            session.flush()

    with Session(engine) as session:
        persisted_item = session.scalar(select(CodexWorkItem))
        assert persisted_item is not None
        persisted_item.status = "applied"
        persisted_item.grading_job_id = uuid.uuid4()
        persisted_item.grading_result_id = uuid.uuid4()
        persisted_item.applied_at = now
        with pytest.raises(IntegrityError):
            session.flush()

    with Session(engine) as session:
        persisted_item = session.scalar(select(CodexWorkItem))
        assert persisted_item is not None
        persisted_item.status = "submitted"
        persisted_item.response_payload = {"suggestion": "teacher-review-only"}
        persisted_item.response_hash = "d" * 64
        persisted_item.submitted_lease_token_hash = "e" * 64
        persisted_item.submitted_at = now
        session.commit()
        assert persisted_item.grading_job_id is None
        assert persisted_item.grading_result_id is None
        assert persisted_item.applied_at is None

    with Session(engine) as session:
        persisted_item = session.scalar(select(CodexWorkItem))
        assert persisted_item is not None
        persisted_item.grading_job_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            session.flush()

    with Session(engine) as session:
        persisted_item = session.scalar(select(CodexWorkItem))
        assert persisted_item is not None
        persisted_item.grading_job_id = uuid.uuid4()
        persisted_item.grading_result_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            session.flush()

    with Session(engine) as session:
        persisted_item = session.scalar(select(CodexWorkItem))
        assert persisted_item is not None
        persisted_item.lease_token_hash = "f" * 64
        with pytest.raises(IntegrityError):
            session.flush()

    with Session(engine) as session:
        persisted_item = session.scalar(select(CodexWorkItem))
        assert persisted_item is not None
        persisted_item.status = "applied"
        persisted_item.grading_job_id = uuid.uuid4()
        persisted_item.grading_result_id = uuid.uuid4()
        persisted_item.applied_at = now
        session.commit()


def test_processing_orchestrator_sqlite_schema_is_inspectable() -> None:
    engine = create_engine("sqlite://")
    ProcessingRun.metadata.create_all(
        engine,
        tables=[
            ProcessingRun.__table__,
            ProcessingRunCommand.__table__,
            ProcessingStep.__table__,
            CodexWorkItem.__table__,
        ],
    )
    inspector = inspect(engine)
    assert ORCHESTRATOR_TABLES <= set(inspector.get_table_names())
    assert next(
        column["default"]
        for column in inspector.get_columns("processing_runs")
        if column["name"] == "input_manifest"
    ) is not None
    assert next(
        column["default"]
        for column in inspector.get_columns("processing_run_commands")
        if column["name"] == "request_payload"
    ) is not None
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("codex_work_items")
    } >= {
        "ck_codex_work_item_lease_state",
        "ck_codex_work_item_request_hash",
        "ck_codex_work_item_submission_audit_complete",
        "ck_codex_work_item_submission_state",
        "ck_codex_work_item_applied_refs_complete",
        "ck_codex_work_item_applied_state",
    }
    work_item_columns = {
        column["name"] for column in inspector.get_columns("codex_work_items")
    }
    assert "lease_token" not in work_item_columns
    assert {
        "lease_token_hash",
        "submitted_lease_token_hash",
        "submitted_at",
    } <= work_item_columns
    assert {
        index["name"] for index in inspector.get_indexes("codex_work_items")
    } >= {"ix_codex_work_item_claim"}


def test_codex_work_item_sqlite_status_and_audit_matrix() -> None:
    engine = create_engine("sqlite://")
    CodexWorkItem.metadata.create_all(engine, tables=[CodexWorkItem.__table__])
    now = datetime.now(UTC)

    def values(status: str, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "processing_step_id": uuid.uuid4(),
            "owner_id": uuid.uuid4(),
            "grading_batch_id": uuid.uuid4(),
            "submission_id": uuid.uuid4(),
            "student_answer_id": uuid.uuid4(),
            "status": status,
            "generation": 1,
            "input_version": "input-v1",
            "request_hash": "a" * 64,
            "request_payload": {},
            "provider": "codex_local",
            "prompt_version": "prompt-v1",
            "schema_version": "schema-v1",
            "config_version": "config-v1",
        }
        result.update(overrides)
        return result

    submitted = {
        "response_payload": {"suggestion": "teacher-review-only"},
        "response_hash": "b" * 64,
        "submitted_lease_token_hash": "c" * 64,
        "submitted_at": now,
    }
    leased = {
        "lease_token_hash": "d" * 64,
        "lease_owner": "synthetic-worker",
        "lease_expires_at": now + timedelta(minutes=5),
    }
    applied = {
        **submitted,
        "grading_job_id": uuid.uuid4(),
        "grading_result_id": uuid.uuid4(),
        "applied_at": now,
    }
    valid_states = [
        values("queued"),
        values("leased", **leased),
        values("submitted", **submitted),
        values("applied", **applied),
        values("retryable_failed"),
        values("terminal_failed"),
        values("terminal_failed", **submitted),
        values("stale"),
        values("stale", **submitted),
        values("cancelled"),
        values("cancelled", **submitted),
    ]
    with engine.begin() as connection:
        for valid in valid_states:
            connection.execute(insert(CodexWorkItem).values(**valid))

    invalid_states = [
        values("queued", **submitted),
        values("queued", **leased),
        values("leased"),
        values("leased", **leased, **submitted),
        values("submitted"),
        values("submitted", **submitted, **leased),
        values("applied", **submitted),
        values("retryable_failed", **submitted),
        values("terminal_failed", response_payload={"partial": True}),
        values("stale", response_hash="e" * 64),
        values("cancelled", submitted_lease_token_hash="f" * 64),
        values("submitted", **{**submitted, "response_hash": "short"}),
        values(
            "submitted",
            **{**submitted, "submitted_lease_token_hash": "short"},
        ),
        values("queued", request_hash="short"),
    ]
    for invalid in invalid_states:
        with Session(engine) as session:
            with pytest.raises(IntegrityError):
                session.execute(insert(CodexWorkItem).values(**invalid))
                session.commit()


def test_processing_orchestrator_postgresql_migration_contract(
    migrated_postgresql: Engine,
) -> None:
    inspector = inspect(migrated_postgresql)
    assert ORCHESTRATOR_TABLES <= set(inspector.get_table_names())
    assert isinstance(
        next(
            column["type"]
            for column in inspector.get_columns("processing_runs")
            if column["name"] == "input_manifest"
        ),
        JSONB,
    )
    assert next(
        column["default"]
        for column in inspector.get_columns("processing_runs")
        if column["name"] == "input_manifest"
    ) is not None
    assert next(
        column["default"]
        for column in inspector.get_columns("processing_run_commands")
        if column["name"] == "request_payload"
    ) is not None
    assert isinstance(
        next(
            column["type"]
            for column in inspector.get_columns("processing_run_commands")
            if column["name"] == "request_payload"
        ),
        JSONB,
    )
    run_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("processing_runs")
    }
    assert ("grading_batch_id", "generation") in run_uniques
    assert ("grading_batch_id", "request_hash") not in run_uniques
    assert all("idempotency_key" not in columns for columns in run_uniques)

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.downgrade(config, "0027_semantic_projection")
    assert ORCHESTRATOR_TABLES.isdisjoint(inspect(migrated_postgresql).get_table_names())
    command.upgrade(config, "0029_processing_auto_confirmation")
    assert ORCHESTRATOR_TABLES <= set(inspect(migrated_postgresql).get_table_names())

    with migrated_postgresql.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        differences = compare_metadata(context, ProcessingRun.metadata)
    orchestrator_differences = [
        difference
        for difference in differences
        if any(table_name in repr(difference) for table_name in ORCHESTRATOR_TABLES)
    ]
    assert orchestrator_differences == []


def test_processing_run_command_postgresql_constraints(
    migrated_postgresql: Engine,
) -> None:
    engine = migrated_postgresql
    owner_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    source_run_id = uuid.uuid4()
    result_run_id = uuid.uuid4()

    def values(**overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "owner_id": owner_id,
            "grading_batch_id": batch_id,
            "operation": "continue",
            "idempotency_key": f"command-{uuid.uuid4()}",
            "request_hash": "a" * 64,
            "request_payload": {"operation": "continue"},
            "result_run_id": result_run_id,
            "result_generation": 1,
        }
        result.update(overrides)
        return result

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql("ALTER TABLE processing_runs DISABLE TRIGGER ALL")
        connection.exec_driver_sql("ALTER TABLE processing_run_commands DISABLE TRIGGER ALL")
        raw_run_id = uuid.uuid4()
        raw_command_id = uuid.uuid4()
        now = datetime.now(UTC)
        connection.execute(
            text(
                "INSERT INTO processing_runs "
                "(id, owner_id, grading_batch_id, status, mode, generation, input_version, "
                "request_hash, submission_count, step_count, completed_step_count, "
                "failed_step_count, pending_codex_count, retryable, created_at, updated_at) "
                "VALUES (:id, :owner_id, :batch_id, 'queued', 'codex_local', 99, "
                "'raw-pg-input', :request_hash, 0, 0, 0, 0, 0, true, :now, :now)"
            ),
            {
                "id": raw_run_id,
                "owner_id": owner_id,
                "batch_id": batch_id,
                "request_hash": "7" * 64,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO processing_run_commands "
                "(id, owner_id, grading_batch_id, operation, idempotency_key, request_hash, "
                "result_run_id, result_generation, created_at, updated_at) "
                "VALUES (:id, :owner_id, :batch_id, 'continue', 'raw-pg-command-default', "
                ":request_hash, :result_run_id, 99, :now, :now)"
            ),
            {
                "id": raw_command_id,
                "owner_id": owner_id,
                "batch_id": batch_id,
                "request_hash": "6" * 64,
                "result_run_id": raw_run_id,
                "now": now,
            },
        )
        assert connection.scalar(
            select(ProcessingRun.input_manifest).where(ProcessingRun.id == raw_run_id)
        ) == {}
        assert connection.scalar(
            select(ProcessingRunCommand.request_payload).where(
                ProcessingRunCommand.id == raw_command_id
            )
        ) == {}

        def assert_rejected(**overrides: object) -> None:
            savepoint = connection.begin_nested()
            try:
                connection.execute(
                    insert(ProcessingRunCommand).values(**values(**overrides))
                )
            except (DataError, IntegrityError):
                savepoint.rollback()
            else:
                savepoint.rollback()
                pytest.fail(f"PostgreSQL accepted illegal command: {sorted(overrides)}")

        assert_rejected(operation="unknown")
        assert_rejected(idempotency_key="")
        assert_rejected(idempotency_key=" padded")
        assert_rejected(idempotency_key="padded ")
        assert_rejected(idempotency_key="x" * 129)
        assert_rejected(request_hash="short")
        assert_rejected(operation="retry", expected_generation=1)
        assert_rejected(
            operation="retry",
            source_run_id=source_run_id,
            expected_generation=None,
        )
        assert_rejected(
            operation="continue",
            source_run_id=source_run_id,
            expected_generation=1,
        )
        assert_rejected(result_generation=0)

        stable_key = "owner-global-stable-key"
        connection.execute(
            insert(ProcessingRunCommand).values(
                **values(idempotency_key=stable_key)
            )
        )
        assert_rejected(
            idempotency_key=stable_key,
            operation="retry",
            source_run_id=source_run_id,
            expected_generation=1,
            result_generation=2,
        )
        assert_rejected(
            operation="reconcile",
            result_run_id=result_run_id,
            expected_generation=2,
            result_generation=2,
        )
        assert_rejected(
            operation="reconcile",
            source_run_id=result_run_id,
            expected_generation=None,
            result_generation=2,
        )
        assert_rejected(
            operation="reconcile",
            source_run_id=source_run_id,
            result_run_id=result_run_id,
            expected_generation=2,
            result_generation=2,
        )
        assert_rejected(
            operation="reconcile",
            source_run_id=result_run_id,
            expected_generation=1,
            result_generation=2,
        )
        connection.execute(
            insert(ProcessingRunCommand).values(
                **values(
                    operation="retry",
                    source_run_id=source_run_id,
                    expected_generation=1,
                    result_generation=2,
                )
            )
        )
        connection.execute(
            insert(ProcessingRunCommand).values(
                **values(
                    operation="reconcile",
                    source_run_id=result_run_id,
                    expected_generation=2,
                    result_generation=2,
                )
            )
        )
        transaction.rollback()


def test_codex_work_item_postgresql_rejects_partial_or_illegal_states(
    migrated_postgresql: Engine,
) -> None:
    engine = migrated_postgresql
    owner_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    submission_id = uuid.uuid4()
    answer_id = uuid.uuid4()
    now = datetime.now(UTC)

    def values(**overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "processing_step_id": uuid.uuid4(),
            "owner_id": owner_id,
            "grading_batch_id": batch_id,
            "submission_id": submission_id,
            "student_answer_id": answer_id,
            "status": "queued",
            "generation": 1,
            "input_version": "input-v1",
            "request_hash": "a" * 64,
            "request_payload": {},
            "provider": "codex_local",
            "prompt_version": "prompt-v1",
            "schema_version": "schema-v1",
            "config_version": "config-v1",
        }
        result.update(overrides)
        return result

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql("ALTER TABLE codex_work_items DISABLE TRIGGER ALL")

        def assert_rejected(**overrides: object) -> None:
            savepoint = connection.begin_nested()
            try:
                connection.execute(insert(CodexWorkItem).values(**values(**overrides)))
            except IntegrityError:
                savepoint.rollback()
            else:
                savepoint.rollback()
                pytest.fail(f"PostgreSQL accepted illegal state: {sorted(overrides)}")

        assert_rejected(
            status="applied",
            response_payload={"suggestion": "review-only"},
            response_hash="b" * 64,
            submitted_lease_token_hash="c" * 64,
            submitted_at=now,
            grading_job_id=uuid.uuid4(),
            grading_result_id=uuid.uuid4(),
        )
        assert_rejected(
            status="submitted",
            response_payload={"suggestion": "review-only"},
            response_hash="b" * 64,
            grading_job_id=uuid.uuid4(),
        )
        assert_rejected(
            status="submitted",
            response_payload={"suggestion": "review-only"},
            response_hash="b" * 64,
            grading_job_id=uuid.uuid4(),
            grading_result_id=uuid.uuid4(),
        )
        assert_rejected(lease_token_hash="a" * 64)
        assert_rejected(response_payload={"suggestion": "missing-hash"})
        assert_rejected(request_hash="short")
        assert_rejected(
            status="leased",
            lease_token_hash="short",
            lease_owner="worker",
            lease_expires_at=now,
        )
        assert_rejected(
            status="queued",
            response_payload={"suggestion": "review-only"},
            response_hash="b" * 64,
            submitted_lease_token_hash="c" * 64,
            submitted_at=now,
        )
        assert_rejected(
            status="submitted",
            response_payload={"suggestion": "review-only"},
            response_hash="short",
            submitted_lease_token_hash="c" * 64,
            submitted_at=now,
        )
        assert_rejected(
            status="submitted",
            response_payload={"suggestion": "review-only"},
            response_hash="b" * 64,
            submitted_lease_token_hash="short",
            submitted_at=now,
        )
        assert_rejected(
            status="retryable_failed",
            response_payload={"suggestion": "review-only"},
            response_hash="b" * 64,
            submitted_lease_token_hash="c" * 64,
            submitted_at=now,
        )
        assert_rejected(status="stale", response_hash="b" * 64)
        assert_rejected(grading_result_id=uuid.uuid4())

        connection.execute(
            insert(CodexWorkItem).values(
                **values(
                    status="leased",
                    lease_token_hash="b" * 64,
                    lease_owner="synthetic-worker",
                    lease_expires_at=now + timedelta(minutes=5),
                )
            )
        )
        connection.execute(
            insert(CodexWorkItem).values(
                **values(
                    status="submitted",
                    response_payload={"suggestion": "review-only"},
                    response_hash="c" * 64,
                    submitted_lease_token_hash="e" * 64,
                    submitted_at=now,
                )
            )
        )
        connection.execute(
            insert(CodexWorkItem).values(
                **values(
                    status="terminal_failed",
                    response_payload={"suggestion": "late-review-only"},
                    response_hash="e" * 64,
                    submitted_lease_token_hash="f" * 64,
                    submitted_at=now,
                )
            )
        )
        connection.execute(
            insert(CodexWorkItem).values(**values(status="stale"))
        )
        connection.execute(
            insert(CodexWorkItem).values(
                **values(
                    status="applied",
                    response_payload={"suggestion": "review-only"},
                    response_hash="d" * 64,
                    submitted_lease_token_hash="f" * 64,
                    submitted_at=now,
                    grading_job_id=uuid.uuid4(),
                    grading_result_id=uuid.uuid4(),
                    applied_at=now,
                )
            )
        )
        transaction.rollback()
