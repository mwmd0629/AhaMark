from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.core.config import Settings


@dataclass(frozen=True)
class GradeSuggestion:
    score: Decimal | None
    confidence: Decimal | None
    summary: str
    feedback: str | None = None
    error_type: str | None = None


class GradingProvider(Protocol):
    name: str
    version: str
    is_demo: bool

    def grade(self, answer: str, max_score: Decimal) -> GradeSuggestion: ...


class UnavailableProvider:
    name, version, is_demo = "unavailable", "none", False

    def grade(self, answer: str, max_score: Decimal) -> GradeSuggestion:
        return GradeSuggestion(None, None, "主观题评分 Provider 不可用，必须由教师人工评分")


class FakeGradingProvider:
    name, version, is_demo = "fake", "test-v1", True

    def grade(self, answer: str, max_score: Decimal) -> GradeSuggestion:
        return GradeSuggestion(
            max_score if answer.strip() else Decimal("0"), Decimal("0.99"), "测试适配器结果"
        )


def provider_from_settings(settings: Settings) -> GradingProvider:
    name = getattr(settings, "grading_provider", "unavailable").lower()
    if name == "fake" and settings.app_env.lower() != "production":
        return FakeGradingProvider()
    return UnavailableProvider()


def normalize_objective(value: str) -> str:
    return "".join(value.casefold().split())


def grade_objective(answer: str, accepted: list[str], max_score: Decimal) -> GradeSuggestion:
    normalized = normalize_objective(answer)
    correct = bool(normalized) and normalized in {normalize_objective(x) for x in accepted}
    return GradeSuggestion(
        max_score if correct else Decimal("0"),
        Decimal("1"),
        "确定性答案规范化精确匹配",
        None if correct else "答案与标准答案不一致",
        None if correct else "incorrect",
    )
