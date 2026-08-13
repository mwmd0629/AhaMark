"""Fail-closed ownership checks for destructive pytest SQLite setup."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from sqlalchemy.engine import make_url

MARKER_NAME = ".ahamark-pytest-owner.json"
MARKER_KIND = "ahamark-pytest-database-v1"


class TestDatabaseSafetyError(RuntimeError):
    __test__ = False

    pass


@dataclass(frozen=True)
class TestDatabaseTarget:
    __test__: ClassVar[bool] = False
    database_url: str
    database_path: Path
    parent: Path
    marker_path: Path
    session_id: str
    worker_id: str


@dataclass(frozen=True)
class FileFingerprint:
    exists: bool
    size: int | None
    mtime_ns: int | None
    sha256: str | None


def fingerprint(path: Path) -> FileFingerprint:
    if not path.exists():
        return FileFingerprint(False, None, None, None)
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return FileFingerprint(True, stat.st_size, stat.st_mtime_ns, digest.hexdigest().upper())


def affected_database_state(path: Path) -> dict[str, FileFingerprint]:
    return {
        "database": fingerprint(path),
        "wal": fingerprint(Path(f"{path}-wal")),
        "shm": fingerprint(Path(f"{path}-shm")),
        "journal": fingerprint(Path(f"{path}-journal")),
    }


def _normalized(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise TestDatabaseSafetyError("pytest database path could not be normalized") from exc
    return os.path.normcase(str(resolved)).replace("/", "\\").casefold()


def _inside(path: Path, root: Path) -> bool:
    value = _normalized(path)
    boundary = _normalized(root).rstrip("\\")
    return value == boundary or value.startswith(boundary + "\\")


def _existing_path(value: str | Path, *, relative_to: Path | None = None) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        if relative_to is None:
            raise TestDatabaseSafetyError("Git returned a non-absolute repository path")
        candidate = relative_to / candidate
    try:
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise TestDatabaseSafetyError("Git repository path could not be normalized") from exc


def _git_output(worktree_root: Path, *arguments: str, git_executable: str) -> str:
    safe_root = _existing_path(worktree_root)
    try:
        completed = subprocess.run(
            [
                git_executable,
                "-c",
                f"safe.directory={safe_root.as_posix()}",
                "-C",
                str(safe_root),
                *arguments,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TestDatabaseSafetyError(
            "Git repository metadata is unavailable; refusing destructive pytest setup"
        ) from exc
    output = completed.stdout.strip()
    if not output:
        raise TestDatabaseSafetyError(
            "Git repository metadata is empty; refusing destructive pytest setup"
        )
    return output


def parse_worktree_list(output: str) -> tuple[Path, ...]:
    roots = tuple(
        Path(line.removeprefix("worktree "))
        for line in output.splitlines()
        if line.startswith("worktree ") and line.removeprefix("worktree ").strip()
    )
    if not roots:
        raise TestDatabaseSafetyError("Git worktree metadata contains no worktrees")
    return roots


def discover_git_protected_roots(
    worktree_root: Path,
    *,
    additional_roots: tuple[Path, ...] = (),
    git_executable: str = "git",
) -> tuple[Path, ...]:
    """Discover every repository-owned root without machine-specific paths."""
    requested_root = _existing_path(worktree_root)
    top_level = _existing_path(
        _git_output(requested_root, "rev-parse", "--show-toplevel", git_executable=git_executable)
    )
    if top_level != requested_root:
        raise TestDatabaseSafetyError("pytest was not started from the expected Git worktree root")
    common_value = _git_output(
        requested_root, "rev-parse", "--git-common-dir", git_executable=git_executable
    )
    common_dir = _existing_path(common_value, relative_to=top_level)
    worktree_output = _git_output(
        requested_root, "worktree", "list", "--porcelain", git_executable=git_executable
    )
    discovered = [top_level, common_dir]
    discovered.extend(_existing_path(path) for path in parse_worktree_list(worktree_output))
    discovered.extend(_existing_path(path) for path in additional_roots)
    unique: dict[str, Path] = {}
    for root in discovered:
        unique.setdefault(_normalized(root), root)
    return tuple(unique.values())


def _require_system_temporary_path(path: Path) -> None:
    temporary_root = _existing_path(tempfile.gettempdir())
    if not _inside(path, temporary_root):
        raise TestDatabaseSafetyError(
            "pytest database must be inside the system temporary directory"
        )


def sqlite_path(database_url: str) -> Path:
    try:
        parsed = make_url(database_url)
    except Exception as exc:
        raise TestDatabaseSafetyError("test DATABASE_URL could not be parsed") from exc
    if parsed.get_backend_name() != "sqlite":
        raise TestDatabaseSafetyError("pytest destructive setup requires an isolated SQLite URL")
    if not parsed.database or parsed.database == ":memory:":
        raise TestDatabaseSafetyError("pytest database must be a named on-disk file")
    candidate = Path(parsed.database)
    if not candidate.is_absolute():
        raise TestDatabaseSafetyError("pytest database path must be absolute")
    # pathlib on some Windows/Python combinations preserves a NUL-containing
    # path instead of rejecting it during resolve().  Reject it explicitly so
    # destructive test setup remains fail-closed across supported runtimes.
    if "\x00" in str(candidate):
        raise TestDatabaseSafetyError("pytest database path could not be normalized")
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise TestDatabaseSafetyError("pytest database path could not be normalized") from exc


def _read_marker(marker_path: Path) -> dict[str, object]:
    try:
        value = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TestDatabaseSafetyError(
            "pytest database ownership marker is missing or invalid"
        ) from exc
    if not isinstance(value, dict) or value.get("kind") != MARKER_KIND:
        raise TestDatabaseSafetyError("pytest database ownership marker has the wrong kind")
    return value


def validate_target(
    target: TestDatabaseTarget,
    *,
    forbidden_roots: tuple[Path, ...],
) -> Path:
    path = sqlite_path(target.database_url)
    try:
        parent = path.parent.resolve(strict=True)
        owned_parent = target.parent.resolve(strict=True)
        marker = target.marker_path.resolve(strict=False)
        recorded_path = target.database_path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise TestDatabaseSafetyError(
            "pytest database ownership paths are missing or invalid"
        ) from exc
    _require_system_temporary_path(path)
    if path != recorded_path:
        raise TestDatabaseSafetyError("pytest database URL does not match the owned database path")
    if parent != owned_parent or marker.parent != parent:
        raise TestDatabaseSafetyError("pytest database and marker must share the owned directory")
    value = _read_marker(marker)
    if value.get("session_id") != target.session_id:
        raise TestDatabaseSafetyError("pytest database marker belongs to another session")
    if value.get("database_name") != path.name:
        raise TestDatabaseSafetyError("pytest database marker names a different database")
    if value.get("worker_id") != target.worker_id:
        raise TestDatabaseSafetyError("pytest database marker belongs to another worker")
    for root in forbidden_roots:
        if _inside(path, root):
            raise TestDatabaseSafetyError(
                "pytest database resolves inside a protected repository root"
            )
    return path


def create_marked_target(
    parent: Path,
    database_name: str,
    *,
    forbidden_roots: tuple[Path, ...],
    worker_id: str | None = None,
    session_id: str | None = None,
) -> TestDatabaseTarget:
    try:
        resolved_parent = parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise TestDatabaseSafetyError("test database parent could not be normalized") from exc
    if Path(database_name).name != database_name or not database_name:
        raise TestDatabaseSafetyError("test database filename must be a simple filename")
    database_path = (resolved_parent / database_name).resolve(strict=False)
    if database_path.exists():
        raise TestDatabaseSafetyError("refusing to claim a pre-existing database")
    for root in forbidden_roots:
        if _inside(database_path, root):
            raise TestDatabaseSafetyError("refusing to create a test database in a protected root")
    _require_system_temporary_path(resolved_parent)
    marker_path = resolved_parent / MARKER_NAME
    if marker_path.exists():
        raise TestDatabaseSafetyError("test directory already has an ownership marker")
    actual_worker = worker_id or os.environ.get("PYTEST_XDIST_WORKER", "main")
    actual_session = session_id or uuid.uuid4().hex
    marker_path.write_text(
        json.dumps(
            {
                "kind": MARKER_KIND,
                "session_id": actual_session,
                "worker_id": actual_worker,
                "database_name": database_path.name,
                "pid": os.getpid(),
                "created_at": datetime.now(UTC).isoformat(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    target = TestDatabaseTarget(
        database_url=f"sqlite:///{database_path.as_posix()}",
        database_path=database_path,
        parent=resolved_parent,
        marker_path=marker_path,
        session_id=actual_session,
        worker_id=actual_worker,
    )
    validate_target(target, forbidden_roots=forbidden_roots)
    return target


def create_session_target(*, forbidden_roots: tuple[Path, ...]) -> TestDatabaseTarget:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    parent = Path(tempfile.mkdtemp(prefix=f"ahamark-pytest-{worker}-")).resolve(strict=True)
    return create_marked_target(
        parent,
        f"ahamark-{worker}-{os.getpid()}-{uuid.uuid4().hex}.sqlite3",
        forbidden_roots=forbidden_roots,
        worker_id=worker,
    )


def cleanup_target(target: TestDatabaseTarget, *, forbidden_roots: tuple[Path, ...]) -> None:
    path = validate_target(target, forbidden_roots=forbidden_roots)
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    ):
        if candidate.exists():
            candidate.unlink()
    target.marker_path.unlink()
    try:
        target.parent.rmdir()
    except OSError:
        pass
