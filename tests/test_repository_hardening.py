from __future__ import annotations

import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1].resolve()


def test_docker_build_context_excludes_sensitive_runtime_artifacts() -> None:
    rules = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required = {
        ".env",
        ".env.*",
        "**/runtime.env",
        "**/*.key",
        "**/*.pem",
        "**/*.p12",
        "**/*.pfx",
        ".preproduction-v8/",
        ".recovery-v7/",
        ".preproduction-assignment-generation/",
        ".pytest-tmp-*",
        ".mypy-tmp-*",
        "test-results/",
        "playwright-report/",
        ".playwright/",
        "tmp/",
        "backups/",
        "reports/",
        "exports/",
        "coverage/",
        ".coverage",
        ".coverage.*",
        "htmlcov/",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "*.dump",
        "*.log",
        "node_modules",
        ".next",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "**/__pycache__",
        "*.pyc",
        "*.tsbuildinfo",
    }
    assert required <= rules
    assert not {rule for rule in rules if rule.startswith("!")}


def test_web_docker_swc_matches_the_exact_next_version() -> None:
    package = json.loads((REPOSITORY_ROOT / "apps/web/package.json").read_text(encoding="utf-8"))
    next_version = package["dependencies"]["next"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", next_version)
    dockerfile = (REPOSITORY_ROOT / "apps/web/Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"@next/swc-linux-x64-musl@(\d+\.\d+\.\d+)", dockerfile)
    assert match is not None
    assert match.group(1) == next_version


def test_migration_tests_contain_no_machine_specific_repository_path() -> None:
    migration_tests = (
        "test_assignment_generation_migration.py",
        "test_assignment_metadata_file_analysis_migration.py",
        "test_assignment_question_extraction_migration.py",
        "test_assignment_answer_rubric_generation_migration.py",
        "test_assignment_central_review_publish_migration.py",
        "test_assignment_provider_invocation_audit_migration.py",
    )
    machine_path = re.compile(r"[A-Za-z]:[\\/](?:Users|OpenAIData)[\\/]", re.IGNORECASE)
    for name in migration_tests:
        source = (REPOSITORY_ROOT / "tests" / name).read_text(encoding="utf-8")
        assert machine_path.search(source) is None
        assert "discover_git_protected_roots(WORKTREE_ROOT)" in source


def test_assignment_generation_example_keeps_safe_defaults() -> None:
    values = {}
    for line in (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    assert values["ASSIGNMENT_GENERATION_ENABLED"] == "true"
    assert values["ASSIGNMENT_GENERATION_PROVIDER"] == "unavailable"
    assert values["ASSIGNMENT_GENERATION_ALLOW_EXTERNAL_PROVIDER_REQUESTS"] == "false"
    assert values["ASSIGNMENT_GENERATION_ALLOW_TEACHER_START"] == "true"
    assert values["ASSIGNMENT_GENERATION_SUGGESTION_ONLY"] == "true"
    assert values["ASSIGNMENT_GENERATION_REAL_PROVIDER_QUALITY_PASSED"] == "false"
