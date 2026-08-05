from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql, sqlite

ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "apps/api/alembic/versions/0006_submissions_grading_review.py"
EXPECTED_TABLES = (
    "grading_batches",
    "submissions",
    "submission_file_matches",
    "submission_pages",
    "student_answers",
    "student_answer_regions",
    "submission_recognition_jobs",
    "grading_jobs",
    "grading_results",
    "grading_criterion_results",
    "grading_evidence",
    "teacher_reviews",
    "score_revisions",
    "submission_score_snapshots",
)


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0006_frozen_history", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0006_is_frozen_to_original_schema_hashes() -> None:
    migration = load_migration()

    assert tuple(table.name for table in migration.TABLES) == EXPECTED_TABLES
    assert migration.ORIGINAL_COMMIT == "f7783f0073592140c1400d6e7f41ffb17638c64e"
    assert (
        hashlib.sha256(migration.compiled_schema(postgresql.dialect()).encode()).hexdigest()
        == migration.ORIGINAL_POSTGRESQL_DDL_SHA256
    )
    assert (
        hashlib.sha256(migration.compiled_schema(sqlite.dialect()).encode()).hexdigest()
        == migration.ORIGINAL_SQLITE_DDL_SHA256
    )
    assert all(column.server_default is None for table in migration.TABLES for column in table.c)

    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert "from app import models" not in source


def test_0006_sqlite_upgrade_downgrade_round_trip() -> None:
    migration = load_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "classes",
        "students",
        "stored_files",
        "questions",
        "rubric_versions",
        "rubric_items",
        "paper_versions",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))

    with engine.begin() as connection:
        metadata.create_all(connection)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert set(EXPECTED_TABLES) <= set(inspect(connection).get_table_names())
        migration.downgrade()
        assert set(EXPECTED_TABLES).isdisjoint(inspect(connection).get_table_names())
