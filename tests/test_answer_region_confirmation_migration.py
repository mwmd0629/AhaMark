import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "apps/api/alembic/versions/0011_answer_region_confirmation.py"


def load_revision(filename: str) -> ModuleType:
    path = ROOT / "apps/api/alembic/versions" / filename
    spec = importlib.util.spec_from_file_location(f"migration_{filename[:4]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_migration() -> ModuleType:
    return load_revision(MIGRATION_PATH.name)


def _postgresql_offline_context(output: io.StringIO) -> MigrationContext:
    return MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )


def _full_history_sql(*arguments: str) -> str:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "postgresql+psycopg://synthetic:synthetic@invalid/ahamark"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments, "--sql"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def test_0011_postgresql_offline_sql_is_semantic_and_never_inspects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = load_migration()
    output = io.StringIO()
    migration.op = Operations(_postgresql_offline_context(output))
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: True)

    def reject_inspection(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline migration must not inspect a database")

    monkeypatch.setattr(migration.sa, "inspect", reject_inspection)
    migration.upgrade()
    migration.downgrade()

    sql = output.getvalue().upper()
    assert "ADD COLUMN STATUS VARCHAR(30) DEFAULT 'PENDING' NOT NULL" in sql
    assert "ADD COLUMN CONFIRMED_BY UUID" in sql
    assert "ADD COLUMN CONFIRMED_AT TIMESTAMP WITH TIME ZONE" in sql
    assert "CREATE INDEX IX_STUDENT_ANSWER_REGIONS_STATUS" in sql
    assert "ADD CONSTRAINT FK_STUDENT_ANSWER_REGIONS_CONFIRMED_BY_USERS" in sql
    assert "FOREIGN KEY(CONFIRMED_BY) REFERENCES USERS (ID) ON DELETE SET NULL" in sql
    drop_fk = sql.index("DROP CONSTRAINT FK_STUDENT_ANSWER_REGIONS_CONFIRMED_BY_USERS")
    drop_index = sql.index("DROP INDEX IX_STUDENT_ANSWER_REGIONS_STATUS")
    drop_confirmed_at = sql.index("DROP COLUMN CONFIRMED_AT")
    drop_confirmed_by = sql.index("DROP COLUMN CONFIRMED_BY")
    drop_status = sql.index("DROP COLUMN STATUS")
    assert drop_fk < drop_index < drop_confirmed_at < drop_confirmed_by < drop_status


def test_full_postgresql_offline_upgrade_and_downgrade_include_0011_contract() -> None:
    upgrade_sql = _full_history_sql("upgrade", "head").upper()
    downgrade_sql = _full_history_sql(
        "downgrade", "0023_assignment_provider_invocation_audit:base"
    ).upper()
    assert "0010_REPORT_STUDENT -> 0011_ANSWER_REGION_CONFIRMATION" in upgrade_sql
    assert "ADD COLUMN STATUS VARCHAR(30) DEFAULT 'PENDING' NOT NULL" in upgrade_sql
    assert "ADD CONSTRAINT FK_STUDENT_ANSWER_REGIONS_CONFIRMED_BY_USERS" in upgrade_sql
    assert "ON DELETE SET NULL" in upgrade_sql
    drop_fk = downgrade_sql.index("DROP CONSTRAINT FK_STUDENT_ANSWER_REGIONS_CONFIRMED_BY_USERS")
    drop_index = downgrade_sql.index("DROP INDEX IX_STUDENT_ANSWER_REGIONS_STATUS")
    drop_column = downgrade_sql.rindex("DROP COLUMN CONFIRMED_AT")
    assert drop_fk < drop_index < drop_column


def test_0012_postgresql_offline_sql_is_complete_ordered_and_never_inspects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = load_revision("0012_submission_page_processing.py")
    output = io.StringIO()
    migration.op = Operations(_postgresql_offline_context(output))
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: True)

    def reject_inspection(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline migration must not inspect a database")

    monkeypatch.setattr(migration.sa, "inspect", reject_inspection)
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue().upper()

    for column in (
        "PROCESSING_STATUS",
        "BLUR_SCORE",
        "BRIGHTNESS",
        "CONTRAST",
        "BLANK_PROBABILITY",
        "DUPLICATE_OF_PAGE_ID",
        "ORIENTATION_CONFIDENCE",
        "PREPROCESSING_VERSION",
        "QUALITY_WARNINGS",
        "PROCESSING_ERROR_CODE",
        "PROCESSING_ERROR_MESSAGE",
        "RETRYABLE",
        "PERCEPTUAL_HASH",
        "ALIGNED_PAPER_PAGE_ID",
        "ALIGNMENT_TRANSFORM",
        "ALIGNMENT_CONFIDENCE",
        "ALIGNMENT_FAILURE_REASON",
        "SEGMENTATION_VERSION",
    ):
        assert column in sql
    for name in (
        "FK_SUBMISSION_PAGES_DUPLICATE",
        "FK_SUBMISSION_PAGES_ALIGNED_PAPER_PAGE",
        "IX_SUBMISSION_PAGES_PROCESSING_STATUS",
        "IX_SUBMISSION_PAGES_PROCESSING_ERROR_CODE",
        "IX_SUBMISSION_PAGES_PERCEPTUAL_HASH",
        "IX_SUBMISSION_PAGES_ALIGNED_PAPER_PAGE_ID",
        "SUBMISSION_PROCESSING_JOBS",
        "SUBMISSION_QUESTION_ANCHORS",
        "UQ_SUBMISSION_PROCESSING_KEY",
        "UQ_SUBMISSION_ANCHOR_BLOCK",
        "SUBMISSION_PROCESSING_JOBS_OWNER_ID_FKEY",
        "SUBMISSION_PROCESSING_JOBS_SUBMISSION_ID_FKEY",
        "SUBMISSION_QUESTION_ANCHORS_SUBMISSION_PROCESSING_JOB_ID_FKEY",
        "SUBMISSION_QUESTION_ANCHORS_SUBMISSION_PAGE_ID_FKEY",
        "SUBMISSION_QUESTION_ANCHORS_CANDIDATE_QUESTION_ID_FKEY",
    ):
        assert name in sql
    assert "REFERENCES USERS (ID) ON DELETE RESTRICT" in sql
    assert "REFERENCES SUBMISSIONS (ID) ON DELETE CASCADE" in sql
    assert "REFERENCES QUESTIONS (ID) ON DELETE SET NULL" in sql
    assert sql.index("CREATE TABLE SUBMISSION_PROCESSING_JOBS") < sql.index(
        "CREATE TABLE SUBMISSION_QUESTION_ANCHORS"
    )
    down_anchor = sql.index("DROP TABLE SUBMISSION_QUESTION_ANCHORS")
    down_jobs = sql.index("DROP TABLE SUBMISSION_PROCESSING_JOBS")
    down_fk = sql.index("DROP CONSTRAINT FK_SUBMISSION_PAGES_ALIGNED_PAPER_PAGE")
    down_index = sql.index("DROP INDEX IX_SUBMISSION_PAGES_ALIGNED_PAPER_PAGE_ID")
    down_column = sql.index("DROP COLUMN ALIGNED_PAPER_PAGE_ID")
    assert down_anchor < down_jobs < down_fk < down_index < down_column


def test_0013_postgresql_offline_sql_is_complete_ordered_and_never_inspects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = load_revision("0013_answer_recognition_evidence.py")
    output = io.StringIO()
    migration.op = Operations(_postgresql_offline_context(output))
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: True)

    def reject_inspection(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline migration must not inspect a database")

    monkeypatch.setattr(migration.sa, "inspect", reject_inspection)
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue().upper()

    for name in (
        "PAGE_VERSION",
        "REGION_VERSION",
        "PROVIDER_KIND",
        "CONFIG_VERSION",
        "IX_SUBMISSION_RECOGNITION_JOBS_INPUT_HASH",
        "REGION_EVIDENCE_IMAGES",
        "UQ_REGION_EVIDENCE_IMAGES_OBJECT_KEY",
        "UQ_REGION_EVIDENCE_SOURCE_VERSION",
        "FK_SUBMISSION_RECOGNITION_BLOCKS_REGION",
        "FK_SUBMISSION_RECOGNITION_BLOCKS_EVIDENCE",
        "FK_SUBMISSION_RECOGNITION_BLOCKS_CONFIRMER",
        "RECOGNITION_REVISIONS",
        "UQ_RECOGNITION_REVISION",
        "QUESTION_RECOGNITION_EVIDENCE",
        "UQ_QUESTION_EVIDENCE_VERSION",
    ):
        assert name in sql
    assert "ON DELETE CASCADE" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "ON DELETE SET NULL" in sql
    assert sql.index("CREATE TABLE REGION_EVIDENCE_IMAGES") < sql.index(
        "CREATE TABLE RECOGNITION_REVISIONS"
    )
    assert sql.index("DROP TABLE QUESTION_RECOGNITION_EVIDENCE") < sql.index(
        "DROP TABLE RECOGNITION_REVISIONS"
    )
    assert sql.index("DROP CONSTRAINT FK_SUBMISSION_RECOGNITION_BLOCKS_EVIDENCE") < sql.index(
        "DROP COLUMN REGION_EVIDENCE_IMAGE_ID"
    )


class FakeInspector:
    def __init__(
        self,
        *,
        columns: list[dict[str, Any]] | None = None,
        indexes: list[dict[str, Any]] | None = None,
        foreign_keys: list[dict[str, Any]] | None = None,
        unique_constraints: list[dict[str, Any]] | None = None,
        primary_key: dict[str, Any] | None = None,
    ) -> None:
        self.columns = columns or []
        self.indexes = indexes or []
        self.foreign_keys = foreign_keys or []
        self.unique_constraints = unique_constraints or []
        self.primary_key = primary_key or {"constrained_columns": []}

    def get_columns(self, table: str) -> list[dict[str, Any]]:
        return self.columns

    def get_indexes(self, table: str) -> list[dict[str, Any]]:
        return self.indexes

    def get_foreign_keys(self, table: str) -> list[dict[str, Any]]:
        return self.foreign_keys

    def get_unique_constraints(self, table: str) -> list[dict[str, Any]]:
        return self.unique_constraints

    def get_pk_constraint(self, table: str) -> dict[str, Any]:
        return self.primary_key


def test_0012_online_helpers_keep_compatible_schema_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = load_revision("0012_submission_page_processing.py")
    inspector = FakeInspector(
        columns=[{"name": "status", "type": sa.String(30), "nullable": False}],
        indexes=[{"name": "ix_status", "column_names": ["status"], "unique": False}],
        foreign_keys=[
            {
                "name": "fk_owner",
                "constrained_columns": ["owner_id"],
                "referred_table": "users",
                "referred_columns": ["id"],
                "options": {"ondelete": "RESTRICT"},
            }
        ],
        unique_constraints=[{"name": "uq_key", "column_names": ["owner_id", "key"]}],
        primary_key={"constrained_columns": ["id"]},
    )
    migration.op = SimpleNamespace(get_bind=lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda bind: inspector)
    migration._ensure_column("table", sa.Column("status", sa.String(30), nullable=False))
    migration._ensure_index("table", "ix_status", ["status"])
    migration._ensure_foreign_key("table", "fk_owner", ["owner_id"], "users", ["id"], "RESTRICT")
    migration._ensure_unique("table", "uq_key", ["owner_id", "key"])
    migration._ensure_primary_key("table", ["id"])


@pytest.mark.parametrize("kind", ["column", "index", "foreign_key", "unique", "primary_key"])
def test_0012_online_helpers_remain_fail_closed(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    migration = load_revision("0012_submission_page_processing.py")
    inspector = FakeInspector(
        columns=[{"name": "status", "type": sa.Integer(), "nullable": False}],
        indexes=[{"name": "ix_status", "column_names": ["wrong"], "unique": False}],
        foreign_keys=[
            {
                "name": "fk_owner",
                "constrained_columns": ["owner_id"],
                "referred_table": "users",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            }
        ],
        unique_constraints=[{"name": "uq_key", "column_names": ["wrong"]}],
        primary_key={"constrained_columns": ["wrong"]},
    )
    migration.op = SimpleNamespace(get_bind=lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda bind: inspector)
    operations = {
        "column": lambda: migration._ensure_column(
            "table", sa.Column("status", sa.String(30), nullable=False)
        ),
        "index": lambda: migration._ensure_index("table", "ix_status", ["status"]),
        "foreign_key": lambda: migration._ensure_foreign_key(
            "table", "fk_owner", ["owner_id"], "users", ["id"], "RESTRICT"
        ),
        "unique": lambda: migration._ensure_unique("table", "uq_key", ["owner_id", "key"]),
        "primary_key": lambda: migration._ensure_primary_key("table", ["id"]),
    }
    with pytest.raises(RuntimeError):
        operations[kind]()


def test_0013_online_helpers_keep_compatible_schema_and_reject_incompatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = load_revision("0013_answer_recognition_evidence.py")
    compatible = FakeInspector(
        columns=[{"name": "version", "type": sa.Integer(), "nullable": False}],
        indexes=[{"name": "ix_version", "column_names": ["version"], "unique": False}],
    )
    migration.op = SimpleNamespace(get_bind=lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda bind: compatible)
    migration._ensure_column("table", sa.Column("version", sa.Integer(), nullable=False))
    migration._ensure_index("table", "ix_version", ["version"])

    incompatible_column = FakeInspector(
        columns=[{"name": "version", "type": sa.String(30), "nullable": False}]
    )
    monkeypatch.setattr(migration.sa, "inspect", lambda bind: incompatible_column)
    with pytest.raises(RuntimeError, match="incompatible existing column"):
        migration._ensure_column("table", sa.Column("version", sa.Integer(), nullable=False))
    incompatible_index = FakeInspector(
        indexes=[{"name": "ix_version", "column_names": ["wrong"], "unique": False}]
    )
    monkeypatch.setattr(migration.sa, "inspect", lambda bind: incompatible_index)
    with pytest.raises(RuntimeError, match="incompatible existing index"):
        migration._ensure_index("table", "ix_version", ["version"])


def test_0011_online_compatible_schema_remains_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = load_migration()
    inspector = FakeInspector(
        columns=[
            {"name": "status", "type": sa.String(30), "nullable": False},
            {
                "name": "confirmed_by",
                "type": postgresql.UUID(as_uuid=True),
                "nullable": True,
            },
            {"name": "confirmed_at", "type": sa.DateTime(timezone=True), "nullable": True},
        ],
        indexes=[
            {
                "name": "ix_student_answer_regions_status",
                "column_names": ["status"],
                "unique": False,
            }
        ],
        foreign_keys=[
            {
                "name": "fk_student_answer_regions_confirmed_by_users",
                "constrained_columns": ["confirmed_by"],
                "referred_table": "users",
                "referred_columns": ["id"],
                "options": {"ondelete": "SET NULL"},
            }
        ],
    )
    operations = SimpleNamespace(
        get_bind=lambda: object(),
        add_column=lambda *args, **kwargs: pytest.fail("compatible column was re-added"),
        create_index=lambda *args, **kwargs: pytest.fail("compatible index was re-added"),
        create_foreign_key=lambda *args, **kwargs: pytest.fail(
            "compatible foreign key was re-added"
        ),
        alter_column=lambda *args, **kwargs: None,
    )
    migration.op = operations
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(migration.sa, "inspect", lambda bind: inspector)
    migration.upgrade()


@pytest.mark.parametrize(
    ("kind", "inspector", "operation"),
    [
        (
            "column",
            FakeInspector(columns=[{"name": "status", "type": sa.Integer(), "nullable": False}]),
            lambda migration: migration._ensure_column(
                sa.Column("status", sa.String(30), nullable=False)
            ),
        ),
        (
            "index",
            FakeInspector(
                indexes=[
                    {
                        "name": "ix_student_answer_regions_status",
                        "column_names": ["confirmed_by"],
                        "unique": False,
                    }
                ]
            ),
            lambda migration: migration._ensure_index(
                "ix_student_answer_regions_status", ["status"]
            ),
        ),
        (
            "foreign key",
            FakeInspector(
                foreign_keys=[
                    {
                        "name": "fk_student_answer_regions_confirmed_by_users",
                        "constrained_columns": ["confirmed_by"],
                        "referred_table": "users",
                        "referred_columns": ["id"],
                        "options": {"ondelete": "CASCADE"},
                    }
                ]
            ),
            lambda migration: migration._ensure_foreign_key(),
        ),
    ],
)
def test_0011_online_incompatible_schema_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    inspector: FakeInspector,
    operation: Any,
) -> None:
    migration = load_migration()
    migration.op = SimpleNamespace(get_bind=lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda bind: inspector)
    with pytest.raises(RuntimeError, match=f"incompatible existing {kind}"):
        operation(migration)


def test_0011_revision_and_linear_ancestry_are_unchanged() -> None:
    migration = load_migration()
    assert migration.revision == "0011_answer_region_confirmation"
    assert migration.down_revision == "0010_report_student"
    migration_0012 = load_revision("0012_submission_page_processing.py")
    assert migration_0012.revision == "0012_submission_page_processing"
    assert migration_0012.down_revision == "0011_answer_region_confirmation"
    migration_0013 = load_revision("0013_answer_recognition_evidence.py")
    assert migration_0013.revision == "0013_answer_recognition_evidence"
    assert migration_0013.down_revision == "0012_submission_page_processing"
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    ancestry = list(
        script.iterate_revisions("0023_assignment_provider_invocation_audit", "0010_report_student")
    )
    assert len(ancestry) == 13
    assert ancestry[-1].revision == "0011_answer_region_confirmation"
    assert all(
        revision.down_revision == following.revision
        for revision, following in zip(ancestry, ancestry[1:], strict=False)
    )
