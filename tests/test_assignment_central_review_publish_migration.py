import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import (
    AssignmentExplicitConfirmation,
    AssignmentPublishReadinessSnapshot,
    AssignmentReviewItem,
    AssignmentReviewSession,
    AssignmentRubricPublicationBinding,
)

from test_support.database_isolation import create_marked_target, discover_git_protected_roots

WORKTREE_ROOT = Path(__file__).parents[1].resolve()
FORBIDDEN_ROOTS = discover_git_protected_roots(WORKTREE_ROOT)


def load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "apps/api/alembic/versions/0022_assignment_central_review_publish.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0022", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_semantic_projection_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "apps/api/alembic/versions/0027_semantic_confirmation_projection.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0027", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def base_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "assignment_generation_jobs",
        "assignment_draft_revisions",
        "paper_versions",
        "rubric_versions",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    return metadata


def test_0022_upgrade_downgrade_upgrade_and_orm_parity(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0022.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    base_metadata().create_all(engine)
    migration = load_migration()
    semantic_projection = load_semantic_projection_migration()
    models = (
        AssignmentReviewSession,
        AssignmentReviewItem,
        AssignmentExplicitConfirmation,
        AssignmentRubricPublicationBinding,
        AssignmentPublishReadinessSnapshot,
    )
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        semantic_projection.op = Operations(MigrationContext.configure(connection))
        semantic_projection.upgrade()
        for model in models:
            migrated = {
                column["name"] for column in sa.inspect(connection).get_columns(model.__tablename__)
            }
            assert migrated == set(model.__table__.columns.keys())
        semantic_projection.downgrade()
        migration.downgrade()
        assert "assignment_review_sessions" not in sa.inspect(connection).get_table_names()
        migration.upgrade()
        semantic_projection.upgrade()
        assert "assignment_publish_readiness_snapshots" in sa.inspect(connection).get_table_names()


def test_0022_chain_and_postgresql_bidirectional_offline_sql() -> None:
    migration = load_migration()
    assert migration.down_revision == "0021_assignment_answer_rubric_generation"
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    migration.op = Operations(context)
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE assignment_review_sessions" in sql
    assert "CREATE TABLE assignment_publish_readiness_snapshots" in sql
    assert "DROP TABLE assignment_review_sessions" in sql
