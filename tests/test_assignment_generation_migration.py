import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_support.database_isolation import create_marked_target

FORBIDDEN_ROOTS = (
    Path(__file__).parents[1].resolve(),
    Path(r"D:\OpenAIData\Workspaces\AhaMark").resolve(),
)


def load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "apps"
        / "api"
        / "alembic"
        / "versions"
        / "0018_assignment_generation_orchestration.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0018", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0018_upgrade_downgrade_upgrade_round_trip(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0018.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table("assignments", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    metadata.create_all(engine)
    migration = load_migration()

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert {
            "assignment_generation_jobs",
            "assignment_draft_revisions",
            "generation_stage_results",
            "generation_issues",
            "assignment_generation_provider_invocations",
        } <= tables
        active_index_sql = connection.scalar(
            sa.text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'uq_assignment_generation_active'"
            )
        )
        assert active_index_sql is not None
        assert " WHERE status IN " in active_index_sql
        provider_fks = sa.inspect(connection).get_foreign_keys(
            "assignment_generation_provider_invocations"
        )
        assert any(fk["referred_table"] == "generation_stage_results" for fk in provider_fks)
        stage_fks = sa.inspect(connection).get_foreign_keys("generation_stage_results")
        assert all(
            fk["referred_table"] != "assignment_generation_provider_invocations" for fk in stage_fks
        )

        migration.downgrade()
        tables_after_down = set(sa.inspect(connection).get_table_names())
        assert "assignment_generation_jobs" not in tables_after_down
        assert "assignment_draft_revisions" not in tables_after_down

        migration.upgrade()
        tables_after_second_up = set(sa.inspect(connection).get_table_names())
        assert "assignment_generation_jobs" in tables_after_second_up
        assert "assignment_generation_provider_invocations" in tables_after_second_up
