import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0047_formula_recognition_candidates.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0047", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table("recognition_jobs", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("paper_pages", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("recognition_blocks", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    return metadata


def test_0047_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'formula-recognition.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert {"formula_regions", "formula_recognition_candidates"}.issubset(
            sa.inspect(connection).get_table_names()
        )
        region_checks = {
            item["name"] for item in sa.inspect(connection).get_check_constraints("formula_regions")
        }
        assert "ck_formula_region_coordinates" in region_checks
        migration.downgrade()
        assert "formula_regions" not in sa.inspect(connection).get_table_names()
        migration.upgrade()
        assert "formula_recognition_candidates" in sa.inspect(connection).get_table_names()


def test_0047_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "CREATE TABLE formula_regions" in sql
    assert "CREATE TABLE formula_recognition_candidates" in sql
    assert "DROP TABLE formula_regions" in sql
