from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_support.database_isolation import create_marked_target, discover_git_protected_roots

ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "apps/api/alembic/versions/0034_structured_rubric_authority.py"
FORBIDDEN_ROOTS = discover_git_protected_roots(ROOT.resolve())
LEGACY_TABLES = {
    "rubric_versions",
    "question_rubrics",
    "rubric_items",
    "assignment_rubric_publication_bindings",
}
STRUCTURED_TABLES = {"structured_rubric_sets", "structured_rubric_set_items"}


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0034", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    users = sa.Table("users", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    assignments = sa.Table(
        "assignments",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("active_rubric_version_id", sa.Uuid()),
    )
    paper_versions = sa.Table(
        "paper_versions", metadata, sa.Column("id", sa.Uuid(), primary_key=True)
    )
    questions = sa.Table("questions", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    reference_answers = sa.Table(
        "reference_answer_versions", metadata, sa.Column("id", sa.Uuid(), primary_key=True)
    )
    structured_versions = sa.Table(
        "structured_rubric_versions", metadata, sa.Column("id", sa.Uuid(), primary_key=True)
    )
    rubric_criteria = sa.Table(
        "rubric_criteria", metadata, sa.Column("id", sa.Uuid(), primary_key=True)
    )
    generation_jobs = sa.Table(
        "assignment_generation_jobs", metadata, sa.Column("id", sa.Uuid(), primary_key=True)
    )
    draft_revisions = sa.Table(
        "assignment_draft_revisions", metadata, sa.Column("id", sa.Uuid(), primary_key=True)
    )
    rubric_versions = sa.Table(
        "rubric_versions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey(assignments.c.id), nullable=False),
    )
    assignments.append_constraint(
        sa.ForeignKeyConstraint(
            [assignments.c.active_rubric_version_id],
            [rubric_versions.c.id],
            name="fk_assignments_active_rubric",
            ondelete="SET NULL",
        )
    )
    question_rubrics = sa.Table(
        "question_rubrics",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_version_id", sa.Uuid(), sa.ForeignKey(rubric_versions.c.id)),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey(questions.c.id)),
    )
    rubric_items = sa.Table(
        "rubric_items",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("question_rubric_id", sa.Uuid(), sa.ForeignKey(question_rubrics.c.id)),
    )
    grading_jobs = sa.Table(
        "grading_jobs",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_version_id", sa.Uuid(), sa.ForeignKey(rubric_versions.c.id)),
    )
    grading_results = sa.Table(
        "grading_results",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_version_id", sa.Uuid(), sa.ForeignKey(rubric_versions.c.id)),
    )
    sa.Index("ix_grading_results_rubric_version_id", grading_results.c.rubric_version_id)
    sa.Table(
        "grading_criterion_results",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_item_id", sa.Uuid(), sa.ForeignKey(rubric_items.c.id)),
    )
    sa.Table(
        "submission_score_snapshots",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_version_id", sa.Uuid(), sa.ForeignKey(rubric_versions.c.id)),
    )
    sa.Table(
        "math_validation_jobs",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    sa.Table(
        "ai_scoring_jobs",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    review_sessions = sa.Table(
        "assignment_review_sessions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey(users.c.id), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey(assignments.c.id), nullable=False),
        sa.Column(
            "generation_job_id", sa.Uuid(), sa.ForeignKey(generation_jobs.c.id), nullable=False
        ),
        sa.Column(
            "draft_revision_id", sa.Uuid(), sa.ForeignKey(draft_revisions.c.id), nullable=False
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("risk_ledger_hash", sa.String(64), nullable=False),
        sa.Column("blocking_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("info_count", sa.Integer(), nullable=False),
        sa.Column("expected_assignment_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "paper_version_id", sa.Uuid(), sa.ForeignKey(paper_versions.c.id), nullable=False
        ),
        sa.Column("structured_binding_hash", sa.String(64), nullable=False),
        sa.Column("legacy_rubric_version_id", sa.Uuid(), sa.ForeignKey(rubric_versions.c.id)),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey(users.c.id), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Index(
        "uq_assignment_review_active",
        review_sessions.c.assignment_id,
        unique=True,
        sqlite_where=sa.text(
            "status IN ('draft','in_review','changes_required',"
            "'ready_for_binding','ready_to_publish')"
        ),
    )
    bindings = sa.Table(
        "assignment_rubric_publication_bindings",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    sa.Table(
        "assignment_publish_readiness_snapshots",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("legacy_rubric_version_id", sa.Uuid(), sa.ForeignKey(rubric_versions.c.id)),
        sa.Column("binding_id", sa.Uuid(), sa.ForeignKey(bindings.c.id)),
    )
    # Keep otherwise-unused prerequisite tables in metadata so their foreign-key
    # targets exist during SQLite batch recreation.
    assert reference_answers is not None and structured_versions is not None
    assert rubric_criteria is not None and grading_jobs is not None
    return metadata


def _insert_review_session(
    connection: sa.Connection, *, hash_column: str = "structured_binding_hash"
) -> None:
    value = uuid.uuid4().hex
    connection.execute(
        sa.text(
            "INSERT INTO assignment_review_sessions "
            "(id, owner_id, assignment_id, generation_job_id, draft_revision_id, generation, "
            "source_snapshot_hash, review_version, status, risk_ledger_hash, blocking_count, "
            "warning_count, info_count, expected_assignment_updated_at, paper_version_id, "
            f"{hash_column}, created_by, created_at, updated_at) VALUES "
            "(:id, :owner, :assignment, :job, :revision, 1, :hash, 1, 'draft', :hash, "
            "0, 0, 0, CURRENT_TIMESTAMP, :paper, :hash, :owner, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)"
        ),
        {
            "id": value,
            "owner": uuid.uuid4().hex,
            "assignment": uuid.uuid4().hex,
            "job": uuid.uuid4().hex,
            "revision": uuid.uuid4().hex,
            "paper": uuid.uuid4().hex,
            "hash": "a" * 64,
        },
    )


def test_0034_sqlite_upgrade_downgrade_upgrade_boundary(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0034.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    _legacy_metadata().create_all(engine)
    migration = load_migration()

    with engine.begin() as connection:
        migration.__dict__["op"] = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert STRUCTURED_TABLES <= tables
        assert LEGACY_TABLES.isdisjoint(tables)
        for job_table in ("math_validation_jobs", "ai_scoring_jobs"):
            inspector = sa.inspect(connection)
            assert "structured_rubric_set_id" in {
                column["name"] for column in inspector.get_columns(job_table)
            }
            assert any(
                foreign_key["referred_table"] == "structured_rubric_sets"
                and foreign_key["constrained_columns"] == ["structured_rubric_set_id"]
                for foreign_key in inspector.get_foreign_keys(job_table)
            )

        migration.downgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert LEGACY_TABLES <= tables
        assert STRUCTURED_TABLES.isdisjoint(tables)
        for job_table in ("math_validation_jobs", "ai_scoring_jobs"):
            assert "structured_rubric_set_id" not in {
                column["name"] for column in sa.inspect(connection).get_columns(job_table)
            }
        status = next(
            column
            for column in sa.inspect(connection).get_columns("rubric_versions")
            if column["name"] == "status"
        )
        assert cast(sa.String, status["type"]).length == len("superseded")

        migration.upgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert STRUCTURED_TABLES <= tables
        assert LEGACY_TABLES.isdisjoint(tables)


def test_0034_upgrade_refuses_review_session_without_legacy_rows(tmp_path: Path) -> None:
    target = create_marked_target(
        tmp_path, "migration-0034-guard.db", forbidden_roots=FORBIDDEN_ROOTS
    )
    engine = sa.create_engine(target.database_url)
    _legacy_metadata().create_all(engine)
    migration = load_migration()

    with engine.begin() as connection:
        _insert_review_session(connection)
        migration.__dict__["op"] = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="assignment_review_sessions"):
            migration.upgrade()
        assert STRUCTURED_TABLES.isdisjoint(sa.inspect(connection).get_table_names())


@pytest.mark.parametrize("job_table", ["math_validation_jobs", "ai_scoring_jobs"])
def test_0034_upgrade_refuses_unpinned_async_jobs(tmp_path: Path, job_table: str) -> None:
    target = create_marked_target(
        tmp_path, f"migration-0034-{job_table}-guard.db", forbidden_roots=FORBIDDEN_ROOTS
    )
    engine = sa.create_engine(target.database_url)
    _legacy_metadata().create_all(engine)
    migration = load_migration()

    with engine.begin() as connection:
        connection.execute(
            sa.text(f"INSERT INTO {job_table} (id) VALUES (:id)"), {"id": uuid.uuid4().hex}
        )
        migration.__dict__["op"] = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match=job_table):
            migration.upgrade()
        assert STRUCTURED_TABLES.isdisjoint(sa.inspect(connection).get_table_names())


def test_0034_downgrade_refuses_review_session_without_structured_set(tmp_path: Path) -> None:
    target = create_marked_target(
        tmp_path, "migration-0034-downgrade-guard.db", forbidden_roots=FORBIDDEN_ROOTS
    )
    engine = sa.create_engine(target.database_url)
    _legacy_metadata().create_all(engine)
    migration = load_migration()

    with engine.begin() as connection:
        migration.__dict__["op"] = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        _insert_review_session(connection, hash_column="structured_set_hash")
        with pytest.raises(RuntimeError, match="assignment_review_sessions"):
            migration.downgrade()
        assert STRUCTURED_TABLES <= set(sa.inspect(connection).get_table_names())


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _RecordingPostgresBind:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: Any) -> _ScalarResult:
        sql = str(statement)
        self.statements.append(sql)
        return _ScalarResult(0)


class _FakeOp:
    def __init__(self, bind: _RecordingPostgresBind) -> None:
        self.bind = bind

    def get_bind(self) -> _RecordingPostgresBind:
        return self.bind


def test_0034_postgresql_guard_locks_all_tables_before_counting() -> None:
    migration = load_migration()
    bind = _RecordingPostgresBind()
    migration.__dict__["op"] = _FakeOp(bind)

    migration._require_empty_tables(
        "rubric_versions",
        "assignment_rubric_publication_bindings",
        "assignment_review_sessions",
        direction="upgrade",
    )

    assert bind.statements[0] == (
        "LOCK TABLE rubric_versions, assignment_rubric_publication_bindings, "
        "assignment_review_sessions IN ACCESS EXCLUSIVE MODE"
    )
    assert all(statement.startswith("SELECT COUNT(*)") for statement in bind.statements[1:])


def test_0034_reuses_the_exact_0003_versionstatus_contract() -> None:
    migration = load_migration()
    assert migration.LEGACY_VERSION_STATUS_VALUES == (
        "draft",
        "processing",
        "ready",
        "confirmed",
        "superseded",
        "failed",
    )
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "create_type=False" in source
    assert '"retired"' not in source


def test_0034_downgrade_uses_postgresql_safe_original_readiness_fk_name() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    constraint_name = "assignment_publish_readiness_snap_legacy_rubric_version_id_fkey"

    assert len(constraint_name.encode("utf-8")) <= 63
    assert f'"{constraint_name}"' in source
    assert '"assignment_publish_readiness_snapshots_legacy_rubric_version_id_fkey"' not in source
