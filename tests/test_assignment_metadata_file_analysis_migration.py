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
        / "0019_assignment_metadata_file_analysis.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0019", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0019_upgrade_downgrade_upgrade_round_trip(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0019.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "assignment_generation_jobs",
        "assignment_draft_revisions",
        "stored_files",
        "paper_pages",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    metadata.create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert {
            "assignment_field_suggestions",
            "assignment_source_file_analyses",
            "assignment_page_analyses",
        } <= tables
        migration.downgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert "assignment_field_suggestions" not in tables
        migration.upgrade()
        assert "assignment_page_analyses" in set(sa.inspect(connection).get_table_names())
