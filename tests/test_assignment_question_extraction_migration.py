import importlib.util
import io
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
        / "apps/api/alembic/versions/0020_assignment_question_extraction.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0020", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0020_upgrade_downgrade_upgrade_round_trip(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0020.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "assignment_generation_jobs",
        "assignment_draft_revisions",
        "paper_versions",
        "paper_pages",
        "recognition_jobs",
        "question_candidates",
        "questions",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    metadata.create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert "assignment_question_extraction_regions" in sa.inspect(connection).get_table_names()
        migration.downgrade()
        assert "paper_page_organization_suggestions" not in sa.inspect(connection).get_table_names()
        migration.upgrade()
        assert (
            "assignment_question_extraction_candidates" in sa.inspect(connection).get_table_names()
        )


def test_0020_chain_and_constraints() -> None:
    migration = load_migration()
    assert migration.down_revision == "0019_assignment_metadata_file_analysis"


def test_0020_postgresql_offline_upgrade_and_downgrade_sql() -> None:
    migration = load_migration()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    migration.op = Operations(context)
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE paper_page_organization_suggestions" in sql
    assert "CREATE TABLE assignment_question_extraction_candidates" in sql
    assert "DROP TABLE assignment_question_extraction_regions" in sql
