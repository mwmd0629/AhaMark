from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).parents[1].resolve()


def _private_ocr_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    parts = tuple(part for part in normalized.split("/") if part)
    name = parts[-1] if parts else ""
    suffix = Path(name).suffix
    private_directory = any(
        part
        in {
            ".rapidocr-artifacts",
            "rapidocr-artifacts",
            "rapidocr-bundle",
            "rapidocr_bundle",
            "ocr-artifacts",
            "ocr-bundle",
            "ocr_bundle",
        }
        for part in parts[:-1]
    )
    if suffix in {".onnx", ".pdmodel", ".pdiparams"}:
        return True
    if name == "fzytk.ttf" or (private_directory and suffix in {".ttf", ".otf"}):
        return True
    if name == "rec_keys.txt" or ("ppocr" in name and "dict" in name and suffix == ".txt"):
        return True
    if name == "manifest.json" and (private_directory or "models" in parts[:-1]):
        return True
    return "approval" in name and any(
        marker in name for marker in ("rapidocr", "ocr", "model", "artifact", "license")
    )


def _docker_context_files() -> list[str]:
    excluded_directories = {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
    files: list[str] = []
    for root, directories, names in os.walk(REPOSITORY_ROOT, topdown=True):
        directories[:] = [name for name in directories if name not in excluded_directories]
        root_path = Path(root)
        files.extend((root_path / name).relative_to(REPOSITORY_ROOT).as_posix() for name in names)
    return files


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
        ".pytest-*",
        ".pytest-cache-*",
        ".mypy-*",
        ".ruff-cache-*",
        ".tmp-*",
        "pytest-*",
        "formula-eval-build/",
        "formula-synthetic-acceptance/",
        "/*资料*/",
        "local-work/",
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


def test_git_tree_and_docker_context_contain_no_private_ocr_artifacts() -> None:
    tracked = (
        subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    tracked_findings = sorted(path for path in tracked if path and _private_ocr_artifact(path))
    context_findings = sorted(
        path for path in _docker_context_files() if _private_ocr_artifact(path)
    )

    assert tracked_findings == []
    assert context_findings == []


def test_tesseract_image_is_explicit_pinned_and_not_the_default_image() -> None:
    default = (REPOSITORY_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    tesseract = (REPOSITORY_ROOT / "apps" / "api" / "Dockerfile.tesseract").read_text(
        encoding="utf-8"
    )

    assert "tesseract-ocr" not in default.lower()
    assert "python:3.12-slim-bookworm@sha256:" in tesseract
    assert "tesseract-ocr=5.3.0-2" in tesseract
    assert "libtesseract5=5.3.0-2" in tesseract
    assert "tesseract-ocr-chi-sim=1:4.1.0-2" in tesseract
    assert "tesseract-ocr-eng=1:4.1.0-2" in tesseract
    assert "curl " not in tesseract and "wget " not in tesseract


def test_web_docker_swc_matches_the_version_declared_by_next() -> None:
    lock = json.loads((REPOSITORY_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    next_package = lock["packages"]["node_modules/next"]
    swc_version = next_package["optionalDependencies"]["@next/swc-linux-x64-musl"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", swc_version)
    dockerfile = (REPOSITORY_ROOT / "apps/web/Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"@next/swc-linux-x64-musl@(\d+\.\d+\.\d+)", dockerfile)
    assert match is not None
    assert match.group(1) == swc_version


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


def test_repository_navigation_covers_major_directories() -> None:
    navigation = {
        "apps": "apps/README.md",
        "data": "data/README.md",
        "deploy": "deploy/README.md",
        "docs": "docs/README.md",
        "scripts": "scripts/README.md",
        "tests": "tests/README.md",
        "workers": "workers/README.md",
    }
    root_readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    for directory, readme in navigation.items():
        assert (REPOSITORY_ROOT / directory).is_dir()
        assert (REPOSITORY_ROOT / readme).is_file()
        assert readme in root_readme


def test_markdown_relative_links_resolve() -> None:
    markdown_link = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    missing: list[str] = []
    tracked_markdown = (
        subprocess.run(
            ["git", "ls-files", "-z", "*.md"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    for relative_path in tracked_markdown:
        if not relative_path:
            continue
        markdown_path = REPOSITORY_ROOT / relative_path
        source = markdown_path.read_text(encoding="utf-8")
        for match in markdown_link.finditer(source):
            target = match.group(1).strip().strip("<>")
            if not target or target.startswith(("#", "/", "http://", "https://", "mailto:")):
                continue
            relative_target = unquote(target.split("#", 1)[0])
            if relative_target and not (markdown_path.parent / relative_target).exists():
                missing.append(f"{markdown_path.relative_to(REPOSITORY_ROOT)} -> {target}")
    assert missing == []


def test_root_verification_scratch_directories_are_ignored() -> None:
    rules = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {
        "/.mypy-*/",
        "/.pytest-*/",
        "/.pytest-cache-*/",
        "/.ruff-cache-*/",
        "/.tmp-*/",
        "/pytest-*/",
        "/formula-eval-build/",
        "/formula-synthetic-acceptance/",
    } <= rules
