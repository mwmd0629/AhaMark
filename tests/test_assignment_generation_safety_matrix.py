from __future__ import annotations

import json
from pathlib import Path

from app.assignment_generation.providers import select_provider
from app.core.config import Settings

from scripts.assignment_generation_safety import materialize, validate_matrix

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "scripts/assignment-generation-safety-matrix-v1.json"


def test_safety_matrix_has_thirty_traceable_scenarios() -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert len(matrix["scenarios"]) == 30
    assert validate_matrix(matrix, ROOT) == []


def test_safety_matrix_materializes_without_secret_values(tmp_path: Path) -> None:
    output = tmp_path / "safety-matrix.json"
    result = materialize(MATRIX, output, "assignment-generation-v1-test-safety")
    assert result["status"] == "passed"
    rendered = output.read_text(encoding="utf-8").casefold()
    assert "secret-value" not in rendered


def test_production_fake_provider_is_unavailable() -> None:
    settings = Settings.model_construct(
        app_env="production", assignment_generation_provider="unavailable"
    )
    provider = select_provider(settings, "fake")
    assert provider.name == "unavailable"
    assert provider.available is False
    assert provider.error_code == "FAKE_PROVIDER_DISABLED_IN_PRODUCTION"


def test_worker_provider_module_has_no_publish_capability() -> None:
    worker = (ROOT / "workers/tasks/assignment_generation.py").read_text(encoding="utf-8")
    provider = (ROOT / "apps/api/app/assignment_generation/providers.py").read_text(
        encoding="utf-8"
    )
    assert "publish_assignment" not in worker
    assert "publish_assignment" not in provider
