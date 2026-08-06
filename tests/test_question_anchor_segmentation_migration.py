from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "apps/api/alembic/versions/0035_question_anchor_segmentation.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_0035_question_anchor_segmentation", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0035_sqlite_upgrade_downgrade_round_trip() -> None:
    engine = create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "submission_question_anchors",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    sa.Table(
        "student_answer_regions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    metadata.create_all(engine)
    migration = load_migration()

    with engine.begin() as connection:
        context = MigrationContext.configure(connection, opts={"render_as_batch": True})
        migration.op = Operations(context)
        migration.upgrade()

        inspector = inspect(connection)
        anchor_columns = {
            column["name"] for column in inspector.get_columns("submission_question_anchors")
        }
        region_columns = {
            column["name"] for column in inspector.get_columns("student_answer_regions")
        }
        assert {"source_kind", "page_version"} <= anchor_columns
        assert "source_question_anchor_id" in region_columns
        assert {
            foreign_key["name"]
            for foreign_key in inspector.get_foreign_keys("student_answer_regions")
        } == {"fk_student_answer_region_source_anchor"}
        assert {index["name"] for index in inspector.get_indexes("student_answer_regions")} == {
            "ix_student_answer_regions_source_question_anchor_id"
        }

        migration.downgrade()
        inspector = inspect(connection)
        assert {
            column["name"] for column in inspector.get_columns("submission_question_anchors")
        } == {"id"}
        assert {column["name"] for column in inspector.get_columns("student_answer_regions")} == {
            "id"
        }
