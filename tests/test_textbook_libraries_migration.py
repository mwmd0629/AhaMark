import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0046_textbook_libraries.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0046", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "assignment_source_file_analyses",
        "paper_pages",
        "recognition_blocks",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table(
        "textbook_source_match_candidates",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_file_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("source_page_id", sa.Uuid(), nullable=False),
    )
    return metadata


def test_0046_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'textbook-libraries.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert {
            "textbook_libraries",
            "textbook_library_questions",
            "assignment_textbook_library_selections",
        }.issubset(inspector.get_table_names())
        columns = {
            column["name"]: column
            for column in inspector.get_columns("textbook_source_match_candidates")
        }
        assert "library_question_id" in columns
        assert columns["source_file_analysis_id"]["nullable"]
        assert columns["source_page_id"]["nullable"]
        migration.downgrade()
        assert "textbook_libraries" not in sa.inspect(connection).get_table_names()
        migration.upgrade()
        assert "textbook_libraries" in sa.inspect(connection).get_table_names()


def test_0046_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "CREATE TABLE textbook_libraries" in sql
    assert "CREATE TABLE textbook_library_questions" in sql
    assert "library_question_id" in sql
    assert "DROP TABLE textbook_libraries" in sql
