import importlib.util
import io
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import AssignmentGenerationProviderInvocation

from test_support.database_isolation import create_marked_target

FORBIDDEN_ROOTS = (
    Path(__file__).parents[1].resolve(),
    Path(r"D:\OpenAIData\Workspaces\AhaMark").resolve(),
)


def load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "apps/api/alembic/versions/0023_assignment_provider_invocation_audit.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0023", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def base_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "assignment_generation_provider_invocations",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("stage_result_id", sa.Uuid()),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160)),
        sa.Column("endpoint_mode", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64)),
        sa.Column("provider_request_id", sa.String(160)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_cost", sa.Numeric(12, 6)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    return metadata


def test_0023_upgrade_downgrade_upgrade_and_orm_parity(tmp_path: Path) -> None:
    target = create_marked_target(tmp_path, "migration-0023.db", forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    base_metadata().create_all(engine)
    migration = load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migrated = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                AssignmentGenerationProviderInvocation.__tablename__
            )
        }
        assert migrated == set(AssignmentGenerationProviderInvocation.__table__.columns.keys())
        migration.downgrade()
        assert "model_snapshot" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                AssignmentGenerationProviderInvocation.__tablename__
            )
        }
        migration.upgrade()


def test_0023_chain_and_postgresql_bidirectional_offline_sql() -> None:
    migration = load_migration()
    assert migration.down_revision == "0022_assignment_central_review_publish"
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    migration.op = Operations(context)
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()
    assert "ADD COLUMN model_snapshot" in sql
    assert "DROP COLUMN model_snapshot" in sql
