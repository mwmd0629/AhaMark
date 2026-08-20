import importlib.util
import io
import uuid
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_support.database_isolation import create_marked_target, discover_git_protected_roots

WORKTREE_ROOT = Path(__file__).parents[1].resolve()
FORBIDDEN_ROOTS = discover_git_protected_roots(WORKTREE_ROOT)


def load_migration() -> ModuleType:
    path = (
        WORKTREE_ROOT
        / "apps"
        / "api"
        / "alembic"
        / "versions"
        / "0028_optional_student_recovery_email.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0028", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def email_nullable(connection: sa.Connection) -> bool:
    return bool(
        next(
            column
            for column in sa.inspect(connection).get_columns("users")
            if column["name"] == "email"
        )["nullable"]
    )


def test_0028_sqlite_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0028.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
    )
    metadata.create_all(engine)
    migration = load_migration()

    with engine.begin() as connection:
        connection.execute(users.insert().values(id=uuid.uuid4(), email="teacher@example.com"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert email_nullable(connection) is True
        connection.execute(
            sa.text("INSERT INTO users (id, email) VALUES (:id, NULL)"),
            [{"id": uuid.uuid4().hex}, {"id": uuid.uuid4().hex}],
        )

        migration.downgrade()
        assert email_nullable(connection) is False
        emails = connection.scalars(sa.text("SELECT email FROM users")).all()
        assert all(email is not None for email in emails)
        assert len(emails) == len(set(emails))

        migration.upgrade()
        assert email_nullable(connection) is True


def test_0028_chain_and_postgresql_bidirectional_offline_sql() -> None:
    migration = load_migration()
    assert migration.down_revision == "0027_student_login_recovery"
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    migration.op = Operations(context)
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()
    assert "ALTER TABLE users ALTER COLUMN email DROP NOT NULL" in sql
    assert "WHERE email IS NULL" in sql
    assert "ALTER TABLE users ALTER COLUMN email SET NOT NULL" in sql
