"""Execute and record the continuation local gates without embedding outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def fingerprint(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "length": stat.st_size,
        "mtime_utc_ticks": int(stat.st_mtime_ns // 100 + 621_355_968_000_000_000),
        "wal": Path(str(path) + "-wal").exists(),
        "shm": Path(str(path) + "-shm").exists(),
        "journal": Path(str(path) + "-journal").exists(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--only")
    args = parser.parse_args()
    root = Path(__file__).parents[1].resolve()
    output = root / ".preproduction-assignment-generation" / args.run_id
    logs = output / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    temp = Path(os.environ["TEMP"]) / f"ahamark-stage6-{args.run_id}"
    temp.mkdir(parents=True, exist_ok=True)
    database = root / "ahamark.db"
    before = fingerprint(database)
    environment = os.environ.copy()
    for variable in ("TEMP", "TMP", "TMPDIR"):
        environment[variable] = str(temp)
    environment["MYPY_CACHE_DIR"] = str(temp / "mypy-cache")
    commands = {
        "database_isolation": [
            args.python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_database_isolation.py",
        ],
        "safety_owner_concurrency_publish": [
            args.python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_assignment_generation.py",
            "tests/test_assignment_generation_safety_matrix.py",
            "tests/test_assignment_central_review_publish.py",
            "tests/test_preproduction_security.py",
            "tests/test_assignment_generation_preproduction_guards.py",
        ],
        "provider_materializers_and_worker": [
            args.python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_assignment_generation_openai_provider.py",
            "tests/test_assignment_generation_provider_worker_e2e.py",
            "tests/test_assignment_metadata_file_analysis.py",
            "tests/test_assignment_question_extraction.py",
            "tests/test_assignment_answer_rubric_generation.py",
        ],
        "readiness": [
            args.python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_dependency_readiness.py",
            "tests/test_api.py",
        ],
        "migration_0018_0023": [
            args.python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_assignment_generation_migration.py",
            "tests/test_assignment_metadata_file_analysis_migration.py",
            "tests/test_assignment_question_extraction_migration.py",
            "tests/test_assignment_answer_rubric_generation_migration.py",
            "tests/test_assignment_central_review_publish_migration.py",
            "tests/test_assignment_provider_invocation_audit_migration.py",
        ],
        "backend_full": [args.python, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        "ruff_format": [
            args.python,
            "-m",
            "ruff",
            "format",
            "--no-cache",
            "--check",
            "apps",
            "tests",
            "workers",
            "scripts",
            "test_support",
        ],
        "ruff_check": [
            args.python,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "apps",
            "tests",
            "workers",
            "scripts",
            "test_support",
        ],
        "mypy": [args.python, "-m", "mypy", "apps/api/app", "workers"],
        "frontend_test": ["npm.cmd", "run", "test"],
        "prettier": ["npm.cmd", "run", "format"],
        "eslint": ["npm.cmd", "run", "lint"],
        "typescript": ["npm.cmd", "run", "typecheck"],
        "next_build": ["npm.cmd", "run", "build"],
        "alembic_heads": [args.python, "-m", "alembic", "-c", "alembic.ini", "heads"],
        "alembic_upgrade_offline_sql": [
            args.python,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "0017_ai_grading_suggestions:0023_assignment_provider_invocation_audit",
            "--sql",
        ],
        "alembic_downgrade_offline_sql": [
            args.python,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "downgrade",
            "0023_assignment_provider_invocation_audit:0017_ai_grading_suggestions",
            "--sql",
        ],
        "git_diff_check": ["git", "diff", "--check"],
    }
    frontend = {"frontend_test", "prettier", "eslint", "typescript", "next_build"}
    result_path = output / "local-gates-results.json"
    selected = set(args.only.split(",")) if args.only else set(commands)
    unknown = selected - set(commands)
    if unknown:
        parser.error(f"unknown gate names: {sorted(unknown)}")
    results: dict[str, object] = {}
    if args.only and result_path.exists():
        results.update(json.loads(result_path.read_text(encoding="utf-8"))["commands"])
    for name, command in commands.items():
        if name not in selected:
            continue
        cwd = root / "apps/web" if name in frontend else root
        started = datetime.now(UTC)
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        (logs / f"{name}.log").write_text(completed.stdout, encoding="utf-8")
        results[name] = {
            "exit_code": completed.returncode,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "started_at": started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "log": f"logs/{name}.log",
        }
    after = fingerprint(database)
    payload = {
        "run_id": args.run_id,
        "status": "PASS"
        if all(item["status"] == "PASS" for item in results.values()) and before == after
        else "FAIL",
        "commands": results,
        "affected_database_before": before,
        "affected_database_after": after,
        "affected_database_unchanged": before == after,
    }
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"run_id": args.run_id, "status": payload["status"]}))
    raise SystemExit(0 if payload["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
