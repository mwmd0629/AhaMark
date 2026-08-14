import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0039_pdf_content_sources.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0039", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "assignment_source_file_analyses",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    sa.Table(
        "assignment_page_analyses",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    return metadata


def test_0039_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'pdf-content-sources.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        file_columns = {
            column["name"] for column in inspector.get_columns("assignment_source_file_analyses")
        }
        page_columns = {
            column["name"] for column in inspector.get_columns("assignment_page_analyses")
        }
        assert {"content_mode", "text_source", "content_mode_confidence"} <= file_columns
        assert {
            "content_mode",
            "text_source",
            "content_mode_confidence",
            "text_character_count",
        } <= page_columns
        migration.downgrade()
        assert "content_mode" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("assignment_source_file_analyses")
        }
        migration.upgrade()
        assert "text_character_count" in {
            column["name"]
            for column in sa.inspect(connection).get_columns("assignment_page_analyses")
        }


def test_0039_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "content_mode" in sql
    assert "text_source" in sql
    assert "text_character_count" in sql
