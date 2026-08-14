import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0041_reference_answer_source_bindings.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0041", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "assignment_draft_revisions",
        "paper_versions",
        "assignment_source_file_analyses",
        "recognition_blocks",
        "questions",
        "paper_pages",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    return metadata


def test_0041_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference-bindings.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert "reference_answer_source_bindings" in sa.inspect(connection).get_table_names()
        assert "reference_answer_source_regions" in sa.inspect(connection).get_table_names()
        migration.downgrade()
        assert "reference_answer_source_bindings" not in sa.inspect(connection).get_table_names()
        migration.upgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("reference_answer_source_bindings")
        }
        assert {"detected_number", "question_id", "source_snapshot_hash"} <= columns


def test_0041_emits_postgresql_upgrade_and_downgrade_sql() -> None:
    output = io.StringIO()
    migration = load_migration()
    migration.op = Operations(
        MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": output},
        )
    )
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()
    assert "reference_answer_source_bindings" in sql
    assert "reference_answer_source_regions" in sql
    assert "DROP TABLE reference_answer_source_bindings" in sql
