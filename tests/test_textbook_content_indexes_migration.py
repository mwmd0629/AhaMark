import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "apps/api/alembic/versions/0044_textbook_content_indexes.py"


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0044", MIGRATION)
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
        "reference_answer_source_bindings",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    sa.Table(
        "textbook_source_match_candidates",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("draft_revision_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("answer_candidate_id", sa.Uuid(), nullable=False),
        sa.Column("match_version", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
    )
    return metadata


def test_0044_sqlite_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'textbook-indexes.db'}")
    prerequisites().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "textbook_content_indexes" in inspector.get_table_names()
        described_columns = inspector.get_columns("textbook_source_match_candidates")
        match_columns = {column["name"] for column in described_columns}
        assert {
            "source_reference_binding_id",
            "confirmed_source_binding_id",
        } <= match_columns
        nullable_by_name = {column["name"]: column["nullable"] for column in described_columns}
        assert nullable_by_name["question_id"]
        assert nullable_by_name["answer_candidate_id"]
        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "textbook_content_indexes" not in inspector.get_table_names()
        match_columns = {
            column["name"] for column in inspector.get_columns("textbook_source_match_candidates")
        }
        assert "source_reference_binding_id" not in match_columns
        migration.upgrade()
        assert "textbook_content_indexes" in sa.inspect(connection).get_table_names()


def test_0044_emits_postgresql_upgrade_and_downgrade_sql() -> None:
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
    assert "textbook_content_indexes" in sql
    assert "source_reference_binding_id" in sql
    assert "uq_textbook_match_confirmed_source_binding" in sql
    assert "DROP TABLE textbook_content_indexes" in sql
