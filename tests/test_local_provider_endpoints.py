import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
from app.ai_grading.providers import (
    OpenAICompatibleAIScoringProvider,
)
from app.ai_grading.providers import (
    provider_from_settings as ai_provider_from_settings,
)
from app.ai_grading.schema import ValidationContext
from app.assignment_generation.providers import (
    OpenAICompatibleAssignmentGenerationProvider,
    select_provider,
)
from app.assignment_generation.providers import (
    provider_from_settings as assignment_provider_from_settings,
)
from app.core.config import Settings
from app.core.provider_endpoints import ProviderEndpointError, safe_provider_base_url
from app.grading.providers import (
    OpenAICompatibleGradingProvider,
)
from app.grading.providers import (
    provider_from_settings as grading_provider_from_settings,
)
from app.recognition.formula import HttpFormulaProvider, formula_provider_from_settings

from scripts.local_formula_provider import (
    FORMULA_BUNDLE_SCHEMA_VERSION,
    get_model,
    validate_formula_bundle,
    verify_formula_bundle_identity,
)


class Response(io.BytesIO):
    headers = {"x-request-id": "local-request-1"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def local_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "grading_provider": "local_openai_compatible",
        "grading_allow_local_provider_requests": True,
        "grading_allowed_local_hosts": ["local-llm"],
        "grading_base_url": "http://local-llm:8080/v1",
        "grading_api_key": "g" * 32,
        "grading_model": "local-model",
        "ai_grading_provider": "local_openai_compatible",
        "ai_grading_allow_local_provider_requests": True,
        "ai_grading_allowed_local_hosts": ["local-llm"],
        "ai_grading_base_url": "http://local-llm:8080/v1",
        "ai_grading_api_key": "a" * 32,
        "ai_grading_model": "local-model",
        "ai_grading_max_retries": 0,
        "assignment_generation_provider": "local_openai_compatible",
        "assignment_generation_allow_local_provider_requests": True,
        "assignment_generation_allowed_local_hosts": ["local-llm"],
        "assignment_generation_base_url": "http://local-llm:8080/v1",
        "assignment_generation_api_key": "p" * 32,
        "assignment_generation_model": "local-model",
        "assignment_generation_max_retries": 0,
        "formula_recognition_provider": "http",
        "formula_recognition_base_url": "http://formula-ocr:8765",
        "formula_recognition_api_key": "f" * 32,
        "formula_recognition_allowed_hosts": ["formula-ocr"],
        "formula_recognition_allow_local_http": True,
    }
    values.update(updates)
    return Settings(**values)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_formula_bundle(root: Path) -> str:
    files = []
    for name, content in {
        "inference.json": b"synthetic-json",
        "inference.pdiparams": b"synthetic-parameters",
        "inference.yml": b"synthetic-yaml",
    }.items():
        path = root / name
        path.write_bytes(content)
        files.append({"path": name, "size": len(content), "sha256": sha256(path)})
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"schema_version": FORMULA_BUNDLE_SCHEMA_VERSION, "files": files},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return sha256(manifest)


def test_formula_bundle_manifest_and_runtime_identity_are_fail_closed(
    monkeypatch, tmp_path: Path
) -> None:
    manifest_sha = write_formula_bundle(tmp_path)
    monkeypatch.setenv("AHAMARK_FORMULA_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("AHAMARK_FORMULA_MANIFEST_SHA256", manifest_sha)
    validate_formula_bundle.cache_clear()
    get_model.cache_clear()
    bundle = validate_formula_bundle()
    verify_formula_bundle_identity(bundle)

    model_file = tmp_path / "inference.yml"
    model_file.write_bytes(b"synthetic-yaml-mutated")
    with pytest.raises(RuntimeError, match="identity changed"):
        verify_formula_bundle_identity(bundle)

    validate_formula_bundle.cache_clear()
    with pytest.raises(RuntimeError, match="identity mismatch"):
        validate_formula_bundle()


def test_local_http_requires_explicit_non_ip_allowlist() -> None:
    assert (
        safe_provider_base_url(
            "http://local-llm:8080/v1",
            allow_external_https=False,
            allow_local_http=True,
            allowed_local_hosts=["local-llm"],
        )
        == "http://local-llm:8080/v1"
    )
    for url, hosts in [
        ("http://local-llm:8080/v1", []),
        ("http://127.0.0.1:8080/v1", ["127.0.0.1"]),
        ("http://metadata.google.internal/v1", ["metadata.google.internal"]),
    ]:
        with pytest.raises(ProviderEndpointError):
            safe_provider_base_url(
                url,
                allow_external_https=False,
                allow_local_http=True,
                allowed_local_hosts=hosts,
            )


def test_local_provider_selection_is_explicit_and_formula_is_available() -> None:
    settings = local_settings()
    assert isinstance(grading_provider_from_settings(settings), OpenAICompatibleGradingProvider)
    assert isinstance(ai_provider_from_settings(settings), OpenAICompatibleAIScoringProvider)
    assert isinstance(
        assignment_provider_from_settings(settings),
        OpenAICompatibleAssignmentGenerationProvider,
    )
    selection = select_provider(settings)
    assert selection.available is True
    assert selection.endpoint_mode == "chat_completions"
    formula = formula_provider_from_settings(settings)
    assert isinstance(formula, HttpFormulaProvider)
    assert formula.available() == (True, None)


def test_local_assignment_provider_uses_schema_chat_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def urlopen(request, **_kwargs):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        envelope = {
            "id": "local-response-1",
            "choices": [{"message": {"content": json.dumps({"suggestions": []})}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }
        return Response(json.dumps(envelope).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = OpenAICompatibleAssignmentGenerationProvider(local_settings()).generate(
        "metadata_analysis", {"document_text": "synthetic"}
    )
    assert result.output is not None
    assert result.input_tokens == 12 and result.output_tokens == 4
    assert captured["url"] == "http://local-llm:8080/v1/chat/completions"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "tools" not in body


def test_local_ai_grading_provider_validates_output(monkeypatch) -> None:
    raw = {
        "schema_version": "criterion-suggestion-v1",
        "criteria": [
            {
                "criterion_stable_key": "criterion-1",
                "status": "suggested_pass",
                "suggested_points": "5",
                "max_points": "5",
                "confidence": "0.7",
                "decision": "evidence supports criterion",
                "evidence_refs": ["evidence-1"],
                "validation_refs": [],
                "error_codes": [],
                "requires_review": True,
                "matched_steps": [],
                "missing_steps": [],
                "detected_errors": [],
                "reasoning_summary": "Synthetic evidence supports the suggestion.",
                "manual_review_reason": None,
                "student_feedback": "",
                "teacher_note": "Review before accepting.",
                "abstained": False,
            }
        ],
        "total_suggested_points": "5",
        "student_feedback": "",
        "teacher_summary": "Synthetic local provider response.",
        "strengths": [],
        "improvements": [],
        "risk_flags": [],
    }
    envelope = {
        "id": "local-response-2",
        "choices": [{"message": {"content": json.dumps(raw)}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_args, **_kwargs: Response(json.dumps(envelope).encode())
    )
    context = ValidationContext(
        criterion_maxima={"criterion-1": Decimal("5")},
        criterion_keys={"criterion-1"},
        evidence_ids={"evidence-1"},
        score_required={"criterion-1"},
        question_max_points=Decimal("5"),
    )
    result = OpenAICompatibleAIScoringProvider(local_settings()).score(
        {"answer": "synthetic"}, context
    )
    assert result.output is not None
    assert result.output.total_suggested_points == Decimal("5")
    assert result.input_tokens == 20 and result.output_tokens == 10


def test_production_local_provider_requires_strong_complete_configuration() -> None:
    base = {
        "_env_file": None,
        "app_env": "production",
        "demo_actor_enabled": False,
        "session_hmac_secret": "s" * 40,
        "minio_access_key": "non-default-access",
        "minio_secret_key": "non-default-secret-value",
        "database_url": "postgresql://db/ahamark",
        "csrf_trusted_origins": ["https://ahamark.invalid"],
        "cors_origins": ["https://ahamark.invalid"],
        "trusted_hosts": ["ahamark.invalid"],
        "auth_cookie_secure": True,
        "grading_provider": "local_openai_compatible",
    }
    with pytest.raises(ValueError, match="local GRADING_PROVIDER"):
        Settings(**base)
