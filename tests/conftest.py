import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

from test_support.database_isolation import (
    TestDatabaseSafetyError,
    affected_database_state,
    cleanup_target,
    create_session_target,
    discover_git_protected_roots,
    validate_target,
)

WORKTREE_ROOT = Path(__file__).parents[1].resolve()
FORBIDDEN_DATABASE_ROOTS = discover_git_protected_roots(WORKTREE_ROOT)
AFFECTED_DATABASE = WORKTREE_ROOT / "ahamark.db"
AFFECTED_DATABASE_BASELINE = affected_database_state(AFFECTED_DATABASE)

_early_imports = sorted(
    name for name in sys.modules if name == "app" or name.startswith(("app.", "workers."))
)
if _early_imports:
    raise TestDatabaseSafetyError(
        "application modules were imported before pytest database isolation: "
        + ", ".join(_early_imports[:5])
    )

TEST_DATABASE_TARGET = create_session_target(forbidden_roots=FORBIDDEN_DATABASE_ROOTS)
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_TARGET.database_url

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from sqlalchemy.orm import close_all_sessions  # noqa: E402

validate_target(TEST_DATABASE_TARGET, forbidden_roots=FORBIDDEN_DATABASE_ROOTS)
if Path(engine.url.database or "").resolve(strict=False) != TEST_DATABASE_TARGET.database_path:
    raise TestDatabaseSafetyError(
        "global db.session engine is not bound to the owned pytest database"
    )


def pytest_report_header() -> list[str]:
    baseline = AFFECTED_DATABASE_BASELINE["database"]
    return [
        f"test database: {TEST_DATABASE_TARGET.database_path}",
        f"test database parent: {TEST_DATABASE_TARGET.parent}",
        "test marker: verified",
        "dangerous database target guard: armed",
        f"affected ahamark.db baseline SHA-256: {baseline.sha256 or 'ABSENT'}",
    ]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    current = affected_database_state(AFFECTED_DATABASE)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if current != AFFECTED_DATABASE_BASELINE:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        if reporter is not None:
            reporter.write_sep("!", "TEST-DATABASE-ISOLATION SAFETY FAIL")
            reporter.write_line("affected ahamark.db or sidecar state changed during pytest")
    elif reporter is not None:
        reporter.write_sep("=", "affected ahamark.db unchanged")
    close_all_sessions()
    engine.dispose()
    try:
        cleanup_target(TEST_DATABASE_TARGET, forbidden_roots=FORBIDDEN_DATABASE_ROOTS)
    except TestDatabaseSafetyError as exc:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        if reporter is not None:
            reporter.write_sep("!", "TEST DATABASE CLEANUP REFUSED")
            reporter.write_line(str(exc))


@pytest.fixture(autouse=True)
def database_schema() -> Generator[None, None, None]:
    close_all_sessions()
    validate_target(TEST_DATABASE_TARGET, forbidden_roots=FORBIDDEN_DATABASE_ROOTS)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    close_all_sessions()
    validate_target(TEST_DATABASE_TARGET, forbidden_roots=FORBIDDEN_DATABASE_ROOTS)
    Base.metadata.drop_all(engine)
