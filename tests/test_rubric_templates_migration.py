import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0037_rubric_templates.py"
TABLES = {
    "rubric_templates",
    "rubric_template_versions",
    "rubric_template_criteria",
    "rubric_template_applications",
}


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0037", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    for table in (
        "users",
        "assignments",
        "questions",
        "reference_answer_versions",
        "structured_rubric_versions",
    ):
        sa.Table(table, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    return metadata


def test_0037_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'templates.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert TABLES <= set(inspector.get_table_names())
        assert {
            "template_version_id",
            "question_version",
            "reference_answer_content_hash",
            "template_content_hash",
            "idempotency_key",
            "request_hash",
        } <= {column["name"] for column in inspector.get_columns("rubric_template_applications")}
        migration.downgrade()
        assert TABLES.isdisjoint(sa.inspect(connection).get_table_names())
        migration.upgrade()
        assert TABLES <= set(sa.inspect(connection).get_table_names())


def test_0037_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "CREATE TABLE rubric_templates" in sql
    assert "CREATE TABLE rubric_template_applications" in sql
    assert "NUMERIC(12, 4)" in sql
    assert "DROP TABLE rubric_templates" in sql
