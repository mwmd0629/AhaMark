import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0040_recognition_character_boxes.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0040", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "recognition_blocks",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    return metadata


def test_0040_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'recognition-character-boxes.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert "character_boxes" in {
            column["name"] for column in sa.inspect(connection).get_columns("recognition_blocks")
        }
        migration.downgrade()
        assert "character_boxes" not in {
            column["name"] for column in sa.inspect(connection).get_columns("recognition_blocks")
        }
        migration.upgrade()
        assert "character_boxes" in {
            column["name"] for column in sa.inspect(connection).get_columns("recognition_blocks")
        }


def test_0040_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "character_boxes" in sql
    assert "DROP COLUMN character_boxes" in sql
