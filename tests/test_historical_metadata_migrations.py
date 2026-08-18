import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_support.database_isolation import create_marked_target, discover_git_protected_roots

ROOT = Path(__file__).parents[1].resolve()
VERSIONS = ROOT / "apps" / "api" / "alembic" / "versions"
FORBIDDEN_ROOTS = discover_git_protected_roots(ROOT)


def load_revision(filename: str) -> ModuleType:
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(f"migration_{filename[:4]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def base_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    for name in (
        "users",
        "assignments",
        "classes",
        "students",
        "stored_files",
        "questions",
        "rubric_versions",
        "rubric_items",
        "paper_versions",
    ):
        sa.Table(name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    return metadata


def migrated_engine(tmp_path: Path, name: str) -> sa.Engine:
    target = create_marked_target(tmp_path, name, forbidden_roots=FORBIDDEN_ROOTS)
    engine = sa.create_engine(target.database_url)
    base_metadata().create_all(engine)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        for filename in (
            "0006_submissions_grading_review.py",
            "0007_grade_release_reports_analytics.py",
        ):
            migration = load_revision(filename)
            migration.op = operations
            migration.upgrade()
    return engine


def column_names(connection: sa.Connection, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(connection).get_columns(table)}


def test_historical_revisions_do_not_import_live_orm_metadata() -> None:
    offenders = []
    for path in VERSIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "Base.metadata" in source or "from app import models" in source:
            offenders.append(path.name)
    assert offenders == []


def test_0006_schema_stays_at_its_historical_boundary_and_0026_adds_portal_columns(
    tmp_path: Path,
) -> None:
    engine = migrated_engine(tmp_path, "historical-metadata-clean.db")
    with engine.begin() as connection:
        submissions = column_names(connection, "submissions")
        assert "submitted_by_user_id" not in submissions
        assert "student_idempotency_key" not in submissions
        assert "processing_status" not in column_names(connection, "submission_pages")
        assert "confirmed_by" not in column_names(connection, "student_answer_regions")
        assert "provider_kind" not in column_names(connection, "submission_recognition_jobs")

        migration = load_revision("0026_student_portal.py")
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        submissions = column_names(connection, "submissions")
        assert "submitted_by_user_id" in submissions
        assert "student_idempotency_key" in submissions
        assert "must_change_password" in column_names(connection, "users")


def test_0026_accepts_columns_leaked_by_the_previous_live_metadata_migration(
    tmp_path: Path,
) -> None:
    engine = migrated_engine(tmp_path, "historical-metadata-legacy.db")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("submissions") as batch:
            batch.add_column(sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True))
            batch.add_column(
                sa.Column("student_idempotency_key", sa.String(length=128), nullable=True)
            )
            batch.create_foreign_key(
                "legacy_submitted_by_user_fk",
                "users",
                ["submitted_by_user_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch.create_unique_constraint(
                "uq_student_submission_idempotency",
                ["submitted_by_user_id", "student_idempotency_key"],
            )
            batch.create_index("ix_submissions_submitted_by_user_id", ["submitted_by_user_id"])

        migration = load_revision("0026_student_portal.py")
        migration.op = operations
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert [
            item
            for item in inspector.get_foreign_keys("submissions")
            if item["constrained_columns"] == ["submitted_by_user_id"]
        ]
        assert (
            len(
                [
                    item
                    for item in inspector.get_unique_constraints("submissions")
                    if item["column_names"] == ["submitted_by_user_id", "student_idempotency_key"]
                ]
            )
            == 1
        )
        assert (
            len(
                [
                    item
                    for item in inspector.get_indexes("submissions")
                    if item["name"] == "ix_submissions_submitted_by_user_id"
                ]
            )
            == 1
        )

        migration.downgrade()
        assert "submitted_by_user_id" not in column_names(connection, "submissions")
        assert "student_idempotency_key" not in column_names(connection, "submissions")
        assert "must_change_password" not in column_names(connection, "users")
