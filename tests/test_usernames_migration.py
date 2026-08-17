import importlib.util
import io
import uuid
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0049_usernames.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0049", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def users_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
    )
    return metadata


def test_0049_sqlite_backfills_unique_username_and_round_trips(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'usernames.db'}")
    users_metadata().create_all(engine)
    user_id = uuid.uuid4()
    migration = load_migration()
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO users (id, email) VALUES (:id, :email)"),
            {"id": user_id.hex, "email": "legacy@example.com"},
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        username = connection.scalar(sa.text("SELECT username FROM users"))
        assert username == f"user-{user_id.hex[:24]}"
        indexes = {item["name"] for item in sa.inspect(connection).get_indexes("users")}
        assert "ix_users_username" in indexes
        migration.downgrade()
        columns = {item["name"] for item in sa.inspect(connection).get_columns("users")}
        assert "username" not in columns


def test_0049_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "ADD COLUMN username VARCHAR(64)" in sql
    assert "CREATE UNIQUE INDEX ix_users_username" in sql
    assert "DROP COLUMN username" in sql
