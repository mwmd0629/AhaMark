import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0042_answer_candidate_source_binding.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0042", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "reference_answer_source_bindings",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    sa.Table(
        "assignment_answer_draft_candidates",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    return metadata


def test_0042_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'answer-binding-link.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("assignment_answer_draft_candidates")
        }
        assert "source_reference_binding_id" in columns
        migration.downgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("assignment_answer_draft_candidates")
        }
        assert "source_reference_binding_id" not in columns
        migration.upgrade()
        unique_constraints = {
            constraint["name"]
            for constraint in sa.inspect(connection).get_unique_constraints(
                "assignment_answer_draft_candidates"
            )
        }
        assert "uq_answer_candidate_reference_binding" in unique_constraints


def test_0042_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "source_reference_binding_id UUID" in sql
    assert "fk_answer_candidate_reference_binding" in sql
    assert "uq_answer_candidate_reference_binding" in sql
    assert "reference_answer_source_bindings" in sql
    assert "DROP COLUMN source_reference_binding_id" in sql
