import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0043_textbook_source_matches.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0043", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prerequisites() -> sa.MetaData:
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "assignment_draft_revisions",
        "paper_versions",
        "questions",
        "assignment_answer_draft_candidates",
        "assignment_source_file_analyses",
        "paper_pages",
        "recognition_blocks",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    return metadata


def test_0043_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'textbook-sources.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        table = "textbook_source_match_candidates"
        assert table in sa.inspect(connection).get_table_names()
        columns = {column["name"] for column in sa.inspect(connection).get_columns(table)}
        assert {
            "answer_candidate_id",
            "source_file_analysis_id",
            "pdf_page_number",
            "solution_content_hash",
            "confirmed_question_id",
        } <= columns
        migration.downgrade()
        assert table not in sa.inspect(connection).get_table_names()
        migration.upgrade()
        assert table in sa.inspect(connection).get_table_names()


def test_0043_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "textbook_source_match_candidates" in sql
    assert "uq_textbook_match_confirmed_question" in sql
    assert "DROP TABLE textbook_source_match_candidates" in sql
