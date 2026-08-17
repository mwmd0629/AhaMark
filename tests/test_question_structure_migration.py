import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0038_question_structure_reviews.py"
TABLES = {"question_structure_reviews", "question_structure_items"}


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0038", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    for table in ("users", "assignments", "paper_versions", "questions"):
        sa.Table(table, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    return metadata


def test_0038_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'question-structure.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert TABLES <= set(sa.inspect(connection).get_table_names())
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("question_structure_items")
        }
        assert {
            "display_number",
            "parent_number",
            "sub_number",
            "display_order",
            "max_score",
            "source_kind",
            "confidence",
        } <= columns
        migration.downgrade()
        assert TABLES.isdisjoint(sa.inspect(connection).get_table_names())
        migration.upgrade()
        assert TABLES <= set(sa.inspect(connection).get_table_names())


def test_0038_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "CREATE TABLE question_structure_reviews" in sql
    assert "CREATE TABLE question_structure_items" in sql
    assert "DROP TABLE question_structure_reviews" in sql
