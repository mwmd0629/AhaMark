import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0048_class_resources.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0048", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("classes", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("stored_files", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    return metadata


def test_0048_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'class-resources.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert "class_resources" in sa.inspect(connection).get_table_names()
        indexes = {item["name"] for item in sa.inspect(connection).get_indexes("class_resources")}
        assert {"ix_class_resources_class_id", "ix_class_resources_status"}.issubset(indexes)
        migration.downgrade()
        assert "class_resources" not in sa.inspect(connection).get_table_names()
        migration.upgrade()
        assert "class_resources" in sa.inspect(connection).get_table_names()


def test_0048_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "CREATE TABLE class_resources" in sql
    assert "DROP TABLE class_resources" in sql
