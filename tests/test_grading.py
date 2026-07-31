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


@pytest.mark.parametrize(
    ("answer", "expected", "question_type"),
    [
        ("选Ａ", "A", "single_choice"),
        ("C、A", "AC", "multiple_choice"),
        ("正确", "TRUE", "true_false"),
        ("1.00", "+1", "fill_blank"),
    ],
)
def test_objective_rule_accepts_safe_equivalent_forms(
    answer: str, expected: str, question_type: str
) -> None:
    result = grade_objective(answer, [expected], Decimal("5"), question_type)
    assert result.score == Decimal("5")
    assert result.confidence == Decimal("1")


def test_objective_rule_does_not_guess_ambiguous_choice_text() -> None:
    result = grade_objective("大概选A", ["A"], Decimal("5"), "single_choice")
    assert result.score == Decimal("0")


def test_objective_rule_keeps_exact_text_fallback_for_legacy_question_types() -> None:
    result = grade_objective(" 1. 测试题 ", ["1.测试题"], Decimal("5"), "single_choice")
    assert result.score == Decimal("5")


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
        {
            "rubric_items": [{"id": "criterion-1", "max_points": "10"}],
            "evidence_regions": [{"id": "evidence-1"}],
        },
    )
    assert result.score is None and result.abstain_reason == "evidence_required"


@pytest.mark.parametrize(
    "criteria",
    [
        [{"rubric_item_id": "criterion-1", "score": 5, "evidence_refs": ["evidence-1"]}],
        [
            {"rubric_item_id": "criterion-1", "score": 2, "evidence_refs": ["evidence-1"]},
            {"rubric_item_id": "criterion-1", "score": 3, "evidence_refs": ["evidence-1"]},
        ],
        [
            {"rubric_item_id": "criterion-1", "score": 5, "evidence_refs": ["missing"]},
            {"rubric_item_id": "criterion-2", "score": 5, "evidence_refs": ["evidence-1"]},
        ],
    ],
)
def test_subjective_provider_rejects_incomplete_duplicate_or_unknown_evidence_criteria(
    monkeypatch: pytest.MonkeyPatch, criteria: list[dict[str, object]]
) -> None:
    output = {
        "criteria": criteria,
        "total_suggested_score": 5 if len(criteria) == 1 else 10,
        "evidence": [{"id": "evidence-1"}],
        "reasoning_summary": "不完整或不可追溯的建议",
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
        {
            "rubric_items": [
                {"id": "criterion-1", "max_points": "5"},
                {"id": "criterion-2", "max_points": "5"},
            ],
            "evidence_regions": [{"id": "evidence-1"}],
        },
    )
    assert result.score is None
    assert result.abstain_reason == "invalid_response"


def test_subjective_provider_accepts_complete_rubric_with_traceable_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = {
        "criteria": [
            {
                "rubric_item_id": "criterion-1",
                "score": 4,
                "evidence_refs": ["evidence-1"],
            },
            {
                "rubric_item_id": "criterion-2",
                "score": 3,
                "evidence_refs": ["evidence-1"],
            },
        ],
        "total_suggested_score": 7,
        "evidence": [{"id": "evidence-1", "summary": "学生答案区域"}],
        "reasoning_summary": "两个评分项均有答案区域证据",
        "feedback": "第一步正确，第二步存在计算错误。",
        "error_type": "arithmetic_error",
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
        {
            "rubric_items": [
                {"id": "criterion-1", "max_points": "5"},
                {"id": "criterion-2", "max_points": "5"},
            ],
            "evidence_regions": [{"id": "evidence-1"}],
        },
    )
    assert result.score == Decimal("7")
    assert result.criterion_scores == {
        "criterion-1": Decimal("4"),
        "criterion-2": Decimal("3"),
    }
    assert result.abstain_reason is None
