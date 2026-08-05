from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEEDS = (
    "seed_analytics_demo.py",
    "seed_capacity_results.py",
    "seed_recovery_fixture.py",
)
LEGACY_MODELS = {"RubricVersion", "QuestionRubric", "RubricItem"}


@pytest.mark.parametrize("filename", SEEDS)
def test_result_seeds_use_structured_rubric_authority_only(filename: str) -> None:
    path = ROOT / "apps" / "api" / "app" / "cli" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    model_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.models"
        for alias in node.names
    }
    assert model_imports.isdisjoint(LEGACY_MODELS)
    assert {"StructuredRubricSet", "StructuredRubricSetItem"} <= model_imports

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        assert node.func.id not in LEGACY_MODELS
        if node.func.id == "SubmissionScoreSnapshot":
            keyword_names = {keyword.arg for keyword in node.keywords}
            assert "structured_rubric_set_id" in keyword_names
            assert "rubric_version_id" not in keyword_names

    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "active_rubric_version_id" not in attributes
