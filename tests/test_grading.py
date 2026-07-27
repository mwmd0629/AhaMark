import json
import urllib.error
from decimal import Decimal

import pytest
from app.core.config import Settings
from app.grading.providers import (
    FakeGradingProvider,
    OpenAICompatibleGradingProvider,
    UnavailableProvider,
    grade_objective,
    normalize_objective,
    provider_from_settings,
)
from app.models import GradingResult, StudentAnswer, SubmissionScoreSnapshot


def test_objective_rule_normalizes_case_and_spaces() -> None:
    assert normalize_objective(" A b C ") == "abc"
    correct = grade_objective(" TRUE ", ["true"], Decimal("5"))
    wrong = grade_objective("false", ["true"], Decimal("5"))
    assert correct.score == Decimal("5") and correct.confidence == Decimal("1")
    assert wrong.score == Decimal("0") and wrong.error_type == "incorrect"


def test_unavailable_provider_never_invents_score() -> None:
    result = UnavailableProvider().grade("a subjective answer", Decimal("10"))
    assert result.score is None and result.confidence is None
    assert "人工评分" in result.summary


def test_fake_provider_is_test_only_and_production_falls_back() -> None:
    development = provider_from_settings(Settings(app_env="test", grading_provider="fake"))
    assert isinstance(development, FakeGradingProvider)
    with pytest.raises(ValueError, match="GRADING_PROVIDER cannot be fake"):
        Settings(app_env="production", grading_provider="fake")


def test_grading_schema_keeps_raw_correction_suggestion_and_snapshot_separate() -> None:
    assert {"recognized_text", "corrected_text", "requires_review"} <= set(
        StudentAnswer.__table__.columns.keys()
    )
    assert {"score", "max_score", "grading_method", "rubric_version_id"} <= set(
        GradingResult.__table__.columns.keys()
    )
    assert {"status", "version", "details"} <= set(SubmissionScoreSnapshot.__table__.columns.keys())


class ProviderResponse:
    def __init__(self, content: str):
        self.content = content

    def __enter__(self) -> "ProviderResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": self.content}}]}).encode()


def configured_provider() -> OpenAICompatibleGradingProvider:
    return OpenAICompatibleGradingProvider(
        Settings(
            app_env="test",
            grading_provider="openai_compatible",
            grading_base_url="https://provider.invalid/v1",
            grading_api_key="test-only",
            grading_model="test-model",
        )
    )


def test_subjective_provider_invalid_json_and_timeout_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_args, **_kwargs: ProviderResponse("{invalid")
    )
    invalid = configured_provider().grade("answer", Decimal("10"), {"rubric_items": []})
    assert invalid.score is None and invalid.abstain_reason == "invalid_response"

    def timeout(*_args: object, **_kwargs: object) -> ProviderResponse:
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    unavailable = configured_provider().grade("answer", Decimal("10"), {"rubric_items": []})
    assert unavailable.score is None and unavailable.abstain_reason == "timeout"


def test_subjective_provider_without_evidence_cannot_suggest_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = {
        "criteria": [{"rubric_item_id": "criterion-1", "score": 5, "evidence_refs": []}],
        "total_suggested_score": 5,
        "evidence": [],
        "reasoning_summary": "结构有效但没有证据",
        "feedback": None,
        "error_type": None,
        "confidence": 0.9,
        "abstain_reason": None,
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: ProviderResponse(json.dumps(output)),
    )
    result = configured_provider().grade(
        "answer",
        Decimal("10"),
        {"rubric_items": [{"id": "criterion-1", "max_points": "10"}]},
    )
    assert result.score is None and result.abstain_reason == "evidence_required"
