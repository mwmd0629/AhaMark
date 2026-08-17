import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0045_textbook_question_only_indexes.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0045", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "textbook_content_indexes",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    return metadata


def test_0045_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'question-only-indexes.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("textbook_content_indexes")
        }
        assert "index_policy" in columns
        migration.downgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("textbook_content_indexes")
        }
        assert "index_policy" not in columns
        migration.upgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("textbook_content_indexes")
        }
        assert "index_policy" in columns


def test_0045_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "index_policy" in sql
    assert "legacy-page-windows-v2" in sql
    assert "DROP COLUMN index_policy" in sql
