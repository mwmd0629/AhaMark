from decimal import Decimal

from app.core.config import Settings
from app.grading.providers import (
    FakeGradingProvider,
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
    production = provider_from_settings(Settings(app_env="production", grading_provider="fake"))
    assert isinstance(development, FakeGradingProvider)
    assert isinstance(production, UnavailableProvider)


def test_grading_schema_keeps_raw_correction_suggestion_and_snapshot_separate() -> None:
    assert {"recognized_text", "corrected_text", "requires_review"} <= set(
        StudentAnswer.__table__.columns.keys()
    )
    assert {"score", "max_score", "grading_method", "rubric_version_id"} <= set(
        GradingResult.__table__.columns.keys()
    )
    assert {"status", "version", "details"} <= set(SubmissionScoreSnapshot.__table__.columns.keys())
