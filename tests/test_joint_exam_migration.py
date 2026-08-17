import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import AssignmentParticipantSnapshot

from test_support.database_isolation import create_marked_target, discover_git_protected_roots

WORKTREE_ROOT = Path(__file__).parents[1].resolve()
FORBIDDEN_ROOTS = discover_git_protected_roots(WORKTREE_ROOT)


def load_migration() -> ModuleType:
    path = Path(__file__).parents[1] / "apps/api/alembic/versions/0032_joint_exam_roster.py"
    spec = importlib.util.spec_from_file_location("migration_0032", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0032_upgrade_downgrade_and_orm_parity(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0032.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    metadata = sa.MetaData()
    for table_name in ("assignments", "classes", "students"):
        sa.Table(table_name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    metadata.create_all(engine)
    migration = load_migration()
    assignment_id = uuid.uuid4()

    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO assignments (id) VALUES (:id)"),
            {"id": assignment_id.hex},
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assignment_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("assignments")
        }
        assert "delivery_mode" in assignment_columns
        assert (
            connection.scalar(
                sa.text("SELECT delivery_mode FROM assignments WHERE id = :id"),
                {"id": assignment_id.hex},
            )
            == "class_assignment"
        )
        snapshot_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                AssignmentParticipantSnapshot.__tablename__
            )
        }
        assert snapshot_columns == set(AssignmentParticipantSnapshot.__table__.columns.keys())
        migration.downgrade()
        assert "assignment_participant_snapshots" not in sa.inspect(connection).get_table_names()
        assert "delivery_mode" not in {
            column["name"] for column in sa.inspect(connection).get_columns("assignments")
        }


def test_0032_chain() -> None:
    migration = load_migration()
    assert migration.revision == "0032_joint_exam_roster"
    assert migration.down_revision == "0031_student_portal"
