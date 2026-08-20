import importlib.util
import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import AuthEmailChallenge, User

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
        / "0027_student_login_recovery.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0027", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def base_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
    )
    sa.Table(
        "students",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_number", sa.String(64), nullable=False),
    )
    sa.Table(
        "student_account_links",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
    )
    return metadata


def seed_legacy_accounts(connection: sa.Connection, metadata: sa.MetaData) -> dict[str, uuid.UUID]:
    users = metadata.tables["users"]
    students = metadata.tables["students"]
    links = metadata.tables["student_account_links"]
    ids = {
        name: uuid.uuid4()
        for name in (
            "valid",
            "duplicate_one",
            "duplicate_two",
            "at_sign",
            "whitespace",
            "too_long",
            "unlinked",
        )
    }
    connection.execute(
        users.insert(),
        [{"id": user_id, "email": f"{name}@example.com"} for name, user_id in ids.items()],
    )
    student_numbers = {
        "valid": "  ＳＴＵ－００１  ",
        "duplicate_one": "ＡＢＣ",
        "duplicate_two": "abc",
        "at_sign": "student@example",
        "whitespace": "student 002",
        "too_long": "x" * 65,
    }
    for name, student_number in student_numbers.items():
        student_id = uuid.uuid4()
        connection.execute(
            students.insert().values(id=student_id, student_number=student_number)
        )
        connection.execute(
            links.insert().values(
                id=uuid.uuid4(), user_id=ids[name], student_id=student_id
            )
        )
    return ids


def login_names(connection: sa.Connection) -> dict[str, str | None]:
    rows = connection.execute(sa.text("SELECT email, login_name FROM users")).mappings()
    return {str(row["email"]): row["login_name"] for row in rows}


def test_0027_upgrade_downgrade_reupgrade_backfill_and_orm_parity(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0027.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    metadata = base_metadata()
    metadata.create_all(engine)
    migration = load_migration()

    with engine.begin() as connection:
        ids = seed_legacy_accounts(connection, metadata)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        assert {"login_name", "email_verified_at"} <= user_columns
        challenge_columns = {
            column["name"] for column in inspector.get_columns("auth_email_challenges")
        }
        assert challenge_columns == set(AuthEmailChallenge.__table__.columns.keys())
        assert {"login_name", "email_verified_at"} <= set(User.__table__.columns.keys())
        assert any(
            constraint["column_names"] == ["login_name"]
            for constraint in inspector.get_unique_constraints("users")
        )
        assert any(
            index["name"] == "ix_users_login_name" and index["column_names"] == ["login_name"]
            for index in inspector.get_indexes("users")
        )

        names = login_names(connection)
        assert names["valid@example.com"] == "stu-001"
        for email in (
            "duplicate_one@example.com",
            "duplicate_two@example.com",
            "at_sign@example.com",
            "whitespace@example.com",
            "too_long@example.com",
            "unlinked@example.com",
        ):
            assert names[email] is None

        now = datetime.now(UTC)
        connection.execute(
            sa.insert(AuthEmailChallenge.__table__).values(
                id=uuid.uuid4(),
                user_id=ids["valid"],
                purpose="reset_password",
                email_snapshot="valid@example.com",
                code_hash="a" * 64,
                expires_at=now + timedelta(minutes=10),
                created_at=now,
            )
        )
        assert connection.scalar(sa.text("SELECT attempts FROM auth_email_challenges")) == 0

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "auth_email_challenges" not in inspector.get_table_names()
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        assert "login_name" not in user_columns
        assert "email_verified_at" not in user_columns

        migration.upgrade()
        assert login_names(connection)["valid@example.com"] == "stu-001"


def test_0027_chain_and_postgresql_bidirectional_offline_sql() -> None:
    migration = load_migration()
    assert migration.down_revision == "0026_student_portal"
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    migration.op = Operations(context)
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()
    assert "ADD COLUMN login_name VARCHAR(64)" in sql
    assert "ADD COLUMN email_verified_at TIMESTAMP WITH TIME ZONE" in sql
    assert "CREATE TABLE auth_email_challenges" in sql
    assert "uq_users_login_name" in sql
    assert "DROP TABLE auth_email_challenges" in sql
    assert "DROP COLUMN login_name" in sql


def test_0027_login_name_normalization_rules() -> None:
    migration = load_migration()
    normalize = migration._normalized_login_name
    assert normalize("  ＡＢＣ－１２３  ") == "abc-123"
    assert normalize("student@example") is None
    assert normalize("student 001") is None
    assert normalize("\tstudent") == "student"
    assert normalize(123) is None
    assert normalize("x" * 65) is None
