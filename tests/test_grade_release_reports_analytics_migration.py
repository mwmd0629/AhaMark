from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.core.config import get_settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, make_url

ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "apps/api/alembic/versions/0007_grade_release_reports_analytics.py"
EXPECTED_HEAD = "0048_class_resources"

EXPECTED_COLUMNS = {
    "grade_releases": (
        "id",
        "owner_id",
        "assignment_id",
        "class_id",
        "version",
        "status",
        "release_mode",
        "scheduled_at",
        "released_at",
        "created_by",
        "notes",
        "idempotency_key",
        "created_at",
        "updated_at",
    ),
    "grade_release_items": (
        "id",
        "grade_release_id",
        "student_id",
        "submission_id",
        "score_snapshot_id",
        "status",
        "created_at",
    ),
    "report_jobs": (
        "id",
        "owner_id",
        "assignment_id",
        "class_id",
        "grade_release_id",
        "report_type",
        "status",
        "progress",
        "stored_file_id",
        "error_code",
        "error_message",
        "idempotency_key",
        "created_at",
        "started_at",
        "completed_at",
        "expires_at",
    ),
    "analytics_snapshots": (
        "id",
        "owner_id",
        "assignment_id",
        "class_id",
        "grade_release_id",
        "schema_version",
        "status",
        "source_snapshot_count",
        "metrics",
        "generated_at",
        "created_at",
    ),
    "teaching_insights": (
        "id",
        "owner_id",
        "analytics_snapshot_id",
        "insight_type",
        "provider",
        "provider_version",
        "prompt_version",
        "status",
        "content",
        "evidence",
        "created_at",
        "updated_at",
    ),
}

EXPECTED_INDEXES = {
    "grade_releases": {
        "ix_grade_releases_assignment_id",
        "ix_grade_releases_class_id",
        "ix_grade_releases_owner_id",
        "ix_grade_releases_status",
    },
    "grade_release_items": {
        "ix_grade_release_items_grade_release_id",
        "ix_grade_release_items_score_snapshot_id",
        "ix_grade_release_items_status",
        "ix_grade_release_items_student_id",
        "ix_grade_release_items_submission_id",
    },
    "report_jobs": {
        "ix_report_jobs_assignment_id",
        "ix_report_jobs_class_id",
        "ix_report_jobs_expires_at",
        "ix_report_jobs_grade_release_id",
        "ix_report_jobs_owner_id",
        "ix_report_jobs_report_type",
        "ix_report_jobs_status",
    },
    "analytics_snapshots": {
        "ix_analytics_snapshots_assignment_id",
        "ix_analytics_snapshots_class_id",
        "ix_analytics_snapshots_grade_release_id",
        "ix_analytics_snapshots_owner_id",
        "ix_analytics_snapshots_status",
    },
    "teaching_insights": {
        "ix_teaching_insights_analytics_snapshot_id",
        "ix_teaching_insights_owner_id",
        "ix_teaching_insights_status",
    },
}


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0007_frozen_history", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0007_uses_the_frozen_historical_schema() -> None:
    migration = load_migration()
    tables = {table.name: table for table in migration.HISTORICAL_TABLES}

    assert tuple(tables) == tuple(EXPECTED_COLUMNS)
    for name, expected_columns in EXPECTED_COLUMNS.items():
        table = tables[name]
        assert tuple(table.c.keys()) == expected_columns
        assert {index.name for index in table.indexes} == EXPECTED_INDEXES[name]
        assert all(column.server_default is None for column in table.c)

    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert "from app import models" not in source
    assert "student_visible_at" not in tables["grade_releases"].c
    assert "student_visible_by" not in tables["grade_releases"].c

    config = Config(str(ROOT / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == [EXPECTED_HEAD]


def _create_sqlite_prerequisites(connection: Connection) -> None:
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "classes",
        "students",
        "submissions",
        "submission_score_snapshots",
        "stored_files",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    metadata.create_all(connection)


def test_0007_sqlite_upgrade_downgrade_round_trip() -> None:
    migration = load_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _create_sqlite_prerequisites(connection)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert set(EXPECTED_COLUMNS) <= set(inspect(connection).get_table_names())
        assert (
            tuple(column["name"] for column in inspect(connection).get_columns("grade_releases"))
            == EXPECTED_COLUMNS["grade_releases"]
        )
        migration.downgrade()
        assert set(EXPECTED_COLUMNS).isdisjoint(inspect(connection).get_table_names())


def _assert_student_portal_objects_once(engine: Engine) -> None:
    inspector = inspect(engine)
    assert {"student_visible_at", "student_visible_by"} <= {
        column["name"] for column in inspector.get_columns("grade_releases")
    }
    assert [
        foreign_key
        for foreign_key in inspector.get_foreign_keys("grade_releases")
        if foreign_key["name"] == "fk_grade_release_student_visible_by"
    ] == [
        {
            "name": "fk_grade_release_student_visible_by",
            "constrained_columns": ["student_visible_by"],
            "referred_schema": None,
            "referred_table": "users",
            "referred_columns": ["id"],
            "options": {"ondelete": "RESTRICT"},
            "comment": None,
        }
    ]
    assert (
        sum(
            index["name"] == "ix_grade_releases_student_visible_at"
            for index in inspector.get_indexes("grade_releases")
        )
        == 1
    )
    with engine.connect() as connection:
        counts = dict(
            connection.execute(
                text(
                    "SELECT column_name, count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'grade_releases' "
                    "AND column_name IN ('student_visible_at', 'student_visible_by') "
                    "GROUP BY column_name"
                )
            ).all()
        )
    assert counts == {"student_visible_at": 1, "student_visible_by": 1}


def _reset_explicitly_isolated_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def test_fresh_postgresql_upgrade_paths() -> None:
    database_url = os.getenv("MIGRATION_HISTORY_PG_URL")
    marker = os.getenv("MIGRATION_HISTORY_PG_MARKER", "")
    if not database_url:
        pytest.skip("requires an explicitly isolated fresh PostgreSQL database")
    if not re.fullmatch(r"[a-z0-9]{12,24}", marker):
        pytest.fail("MIGRATION_HISTORY_PG_MARKER must be 12-24 lowercase alphanumerics")

    parsed_url = make_url(database_url)
    expected_database = f"ahamark_migration_0007_{marker}"
    if parsed_url.drivername != "postgresql+psycopg":
        pytest.fail("MIGRATION_HISTORY_PG_URL must use postgresql+psycopg")
    if parsed_url.host not in {"127.0.0.1", "localhost"}:
        pytest.fail("migration regression database must be an isolated local PostgreSQL")
    if parsed_url.database != expected_database:
        pytest.fail(f"database name must exactly match {expected_database}")

    config = Config(str(ROOT / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == [EXPECTED_HEAD]

    original_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    engine = create_engine(database_url)
    try:
        assert inspect(engine).get_table_names() == []

        command.upgrade(config, "head")
        _assert_student_portal_objects_once(engine)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == EXPECTED_HEAD
            )

        command.upgrade(config, "head")
        _assert_student_portal_objects_once(engine)

        _reset_explicitly_isolated_database(engine)
        command.upgrade(config, "0030_collaborative_grading")
        columns_at_0030 = {
            column["name"] for column in inspect(engine).get_columns("grade_releases")
        }
        assert "student_visible_at" not in columns_at_0030
        assert "student_visible_by" not in columns_at_0030

        command.upgrade(config, "head")
        _assert_student_portal_objects_once(engine)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == EXPECTED_HEAD
            )
    finally:
        engine.dispose()
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
        get_settings.cache_clear()
