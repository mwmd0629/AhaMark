import importlib.util
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
        Path(__file__).parents[1]
        / "apps/api/alembic/versions/0033_joint_exam_class_authorization.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0033", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0033_backfills_authorizer_and_downgrades(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0033.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    metadata = sa.MetaData()
    users = sa.Table("users", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    assignments = sa.Table(
        "assignments",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey(users.c.id), nullable=False),
    )
    classes = sa.Table("classes", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    assignment_classes = sa.Table(
        "assignment_classes",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey(assignments.c.id), nullable=False),
        sa.Column("class_id", sa.Uuid(), sa.ForeignKey(classes.c.id), nullable=False),
    )
    metadata.create_all(engine)
    owner_id, assignment_id, class_id, link_id = (uuid.uuid4() for _ in range(4))
    migration = load_migration()
    with engine.begin() as connection:
        connection.execute(users.insert().values(id=owner_id))
        connection.execute(assignments.insert().values(id=assignment_id, owner_id=owner_id))
        connection.execute(classes.insert().values(id=class_id))
        connection.execute(
            assignment_classes.insert().values(
                id=link_id, assignment_id=assignment_id, class_id=class_id
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.scalar(
            sa.text("SELECT authorized_by FROM assignment_classes WHERE id = :id"),
            {"id": link_id.hex},
        ) == owner_id.hex
        migration.downgrade()
        assert "authorized_by" not in {
            column["name"] for column in sa.inspect(connection).get_columns("assignment_classes")
        }


def test_0033_chain() -> None:
    migration = load_migration()
    assert migration.revision == "0033_joint_exam_class_authorization"
    assert migration.down_revision == "0032_joint_exam_roster"
