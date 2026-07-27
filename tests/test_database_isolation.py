import json
import os
import subprocess
from pathlib import Path

import conftest as suite
import pytest
from app.core.config import get_settings
from app.db.session import engine
from sqlalchemy.engine import make_url

from test_support.database_isolation import (
    MARKER_KIND,
    MARKER_NAME,
    TestDatabaseSafetyError,
    TestDatabaseTarget,
    affected_database_state,
    cleanup_target,
    create_marked_target,
    create_session_target,
    discover_git_protected_roots,
    fingerprint,
    parse_worktree_list,
    sqlite_path,
    validate_target,
)


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={path.resolve().as_posix()}",
            "-C",
            str(path),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def test_unset_caller_database_url_still_uses_owned_temporary_database() -> None:
    assert os.environ["DATABASE_URL"] == suite.TEST_DATABASE_TARGET.database_url
    assert suite.TEST_DATABASE_TARGET.parent != suite.WORKTREE_ROOT
    assert suite.TEST_DATABASE_TARGET.marker_path.exists()


def test_global_engine_and_settings_use_the_same_absolute_test_path() -> None:
    expected = suite.TEST_DATABASE_TARGET.database_path
    assert Path(engine.url.database or "").resolve() == expected
    assert sqlite_path(get_settings().database_url) == expected
    assert expected.is_absolute()


def test_affected_worktree_database_matches_session_baseline() -> None:
    assert affected_database_state(suite.AFFECTED_DATABASE) == suite.AFFECTED_DATABASE_BASELINE


def test_runtime_discovery_protects_current_worktree_common_dir_and_main_worktree() -> None:
    protected = set(discover_git_protected_roots(suite.WORKTREE_ROOT))
    common_value = _git(suite.WORKTREE_ROOT, "rev-parse", "--git-common-dir")
    common_dir = Path(common_value)
    if not common_dir.is_absolute():
        common_dir = suite.WORKTREE_ROOT / common_dir
    listed = parse_worktree_list(_git(suite.WORKTREE_ROOT, "worktree", "list", "--porcelain"))
    assert suite.WORKTREE_ROOT in protected
    assert common_dir.resolve(strict=True) in protected
    assert {path.resolve(strict=True) for path in listed} <= protected


def test_runtime_discovery_supports_an_ordinary_checkout_with_spaces(tmp_path: Path) -> None:
    checkout = tmp_path / "ordinary checkout with spaces"
    subprocess.run(
        ["git", "init", str(checkout)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    protected = set(discover_git_protected_roots(checkout))
    assert checkout.resolve(strict=True) in protected
    assert (checkout / ".git").resolve(strict=True) in protected


def test_runtime_discovery_supports_a_linked_worktree(tmp_path: Path) -> None:
    checkout = tmp_path / "main checkout"
    linked = tmp_path / "linked checkout"
    subprocess.run(
        ["git", "init", str(checkout)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _git(
        checkout,
        "-c",
        "user.name=AhaMark Tests",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "fixture",
    )
    _git(checkout, "worktree", "add", "--detach", str(linked))
    protected = set(discover_git_protected_roots(linked))
    assert checkout.resolve(strict=True) in protected
    assert linked.resolve(strict=True) in protected
    assert (checkout / ".git").resolve(strict=True) in protected


def test_worktree_parser_preserves_a_different_drive_and_spaces() -> None:
    parsed = parse_worktree_list(
        "worktree E:/Portable Root/AhaMark\nHEAD 0123456789abcdef\ndetached\n"
    )
    assert parsed == (Path("E:/Portable Root/AhaMark"),)


def test_explicit_additional_protected_root_is_preserved(tmp_path: Path) -> None:
    extra = tmp_path / "additional protected root"
    extra.mkdir()
    protected = discover_git_protected_roots(suite.WORKTREE_ROOT, additional_roots=(extra,))
    assert extra.resolve(strict=True) in protected


def test_git_unavailable_fails_closed() -> None:
    with pytest.raises(TestDatabaseSafetyError, match="Git repository metadata is unavailable"):
        discover_git_protected_roots(
            suite.WORKTREE_ROOT, git_executable="ahamark-git-command-does-not-exist"
        )


def test_every_discovered_repository_root_rejects_database_creation() -> None:
    for root in suite.FORBIDDEN_DATABASE_ROOTS:
        with pytest.raises(TestDatabaseSafetyError, match="protected root"):
            create_marked_target(
                root,
                "must-not-exist-ahamark-test.sqlite3",
                forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS,
            )


def test_existing_directory_outside_system_temp_is_rejected() -> None:
    with pytest.raises(TestDatabaseSafetyError, match="system temporary directory"):
        create_marked_target(
            suite.WORKTREE_ROOT.parent,
            "must-not-exist-ahamark-test.sqlite3",
            forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS,
        )


def test_unormalizable_database_path_fails_closed() -> None:
    with pytest.raises(TestDatabaseSafetyError, match="could not be normalized"):
        sqlite_path("sqlite:///C:/invalid\x00database.sqlite3")


@pytest.mark.parametrize(
    "url",
    ["sqlite:///./ahamark.db", "sqlite:///relative.db", "sqlite://", "sqlite:///:memory:"],
)
def test_relative_empty_and_memory_database_targets_are_rejected(url: str) -> None:
    with pytest.raises(TestDatabaseSafetyError):
        sqlite_path(url)


def test_unparseable_database_target_is_rejected() -> None:
    with pytest.raises(TestDatabaseSafetyError):
        sqlite_path("not a database url")


def test_preexisting_database_cannot_be_claimed(tmp_path: Path) -> None:
    (tmp_path / "existing.sqlite3").write_bytes(b"pre-existing")
    with pytest.raises(TestDatabaseSafetyError, match="pre-existing"):
        create_marked_target(
            tmp_path,
            "existing.sqlite3",
            forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS,
        )


def test_marker_is_required_and_must_match_session(tmp_path: Path) -> None:
    target = create_marked_target(
        tmp_path,
        "owned.sqlite3",
        forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS,
    )
    target.marker_path.write_text(
        json.dumps(
            {
                "kind": MARKER_KIND,
                "session_id": "wrong",
                "worker_id": target.worker_id,
                "database_name": target.database_path.name,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(TestDatabaseSafetyError, match="another session"):
        validate_target(target, forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)


def test_worktree_database_url_is_rejected_even_with_external_marker(tmp_path: Path) -> None:
    marker = tmp_path / MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "kind": MARKER_KIND,
                "session_id": "session",
                "worker_id": "main",
                "database_name": "ahamark.db",
            }
        ),
        encoding="utf-8",
    )
    dangerous = TestDatabaseTarget(
        database_url=f"sqlite:///{suite.AFFECTED_DATABASE.as_posix()}",
        database_path=suite.AFFECTED_DATABASE,
        parent=suite.AFFECTED_DATABASE.parent,
        marker_path=marker,
        session_id="session",
        worker_id="main",
    )
    with pytest.raises(TestDatabaseSafetyError):
        validate_target(dangerous, forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)


def test_protected_root_refuses_new_test_database() -> None:
    with pytest.raises(TestDatabaseSafetyError, match="protected root"):
        create_marked_target(
            suite.WORKTREE_ROOT,
            "must-not-exist.sqlite3",
            forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS,
        )


def test_current_marker_allows_destructive_fixture_target() -> None:
    assert (
        validate_target(
            suite.TEST_DATABASE_TARGET,
            forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS,
        )
        == suite.TEST_DATABASE_TARGET.database_path
    )


def test_parallel_workers_get_distinct_directories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    first = create_session_target(forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)
    second = create_session_target(forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)
    try:
        assert first.worker_id == second.worker_id == "gw7"
        assert first.parent != second.parent
        assert first.database_path != second.database_path
    finally:
        cleanup_target(first, forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)
        cleanup_target(second, forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)


def test_cleanup_removes_only_owned_exact_targets(tmp_path: Path) -> None:
    target = create_marked_target(
        tmp_path,
        "cleanup.sqlite3",
        forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS,
    )
    target.database_path.write_bytes(b"owned")
    unrelated = tmp_path / "unrelated.db"
    unrelated.write_bytes(b"keep")
    cleanup_target(target, forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)
    assert not target.database_path.exists()
    assert unrelated.read_bytes() == b"keep"


def test_cleanup_rejects_forged_target_without_touching_worktree_database(
    tmp_path: Path,
) -> None:
    baseline = fingerprint(suite.AFFECTED_DATABASE)
    forged = TestDatabaseTarget(
        database_url=f"sqlite:///{suite.AFFECTED_DATABASE.as_posix()}",
        database_path=suite.AFFECTED_DATABASE,
        parent=suite.AFFECTED_DATABASE.parent,
        marker_path=tmp_path / "missing-marker",
        session_id="forged",
        worker_id="main",
    )
    with pytest.raises(TestDatabaseSafetyError):
        cleanup_target(forged, forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)
    assert fingerprint(suite.AFFECTED_DATABASE) == baseline


def test_worker_import_keeps_the_isolated_global_engine() -> None:
    from workers.tasks import assignment_generation

    worker_session_local = vars(assignment_generation)["SessionLocal"]
    assert worker_session_local.kw["bind"] is engine


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_windows_case_and_slash_variants_resolve_to_owned_target() -> None:
    target = suite.TEST_DATABASE_TARGET
    parsed = make_url(target.database_url)
    variant = str(parsed.database).swapcase().replace("\\", "/")
    changed = TestDatabaseTarget(
        database_url=f"sqlite:///{variant}",
        database_path=target.database_path,
        parent=target.parent,
        marker_path=target.marker_path,
        session_id=target.session_id,
        worker_id=target.worker_id,
    )
    assert validate_target(changed, forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS)


def test_resolved_symlink_cannot_escape_into_protected_root(tmp_path: Path) -> None:
    link = tmp_path / "repo-link"
    try:
        link.symlink_to(suite.WORKTREE_ROOT, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(TestDatabaseSafetyError, match="protected root"):
        create_marked_target(
            link,
            "escaped.sqlite3",
            forbidden_roots=suite.FORBIDDEN_DATABASE_ROOTS,
        )
