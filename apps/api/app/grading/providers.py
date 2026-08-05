import json
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import Settings


@dataclass(frozen=True)
class GradeSuggestion:
    score: Decimal | None
    confidence: Decimal | None
    summary: str
    feedback: str | None = None
    error_type: str | None = None
    criterion_scores: dict[str, Decimal] = field(default_factory=dict)
    criterion_reasons: dict[str, str] = field(default_factory=dict)
    criterion_evidence_refs: dict[str, list[str]] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    abstain_reason: str | None = None


class ProviderCriterion(BaseModel):
    criterion_id: str
    score: Decimal = Field(ge=0)
    reason: str | None = Field(None, min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list)


class ProviderOutput(BaseModel):
    criteria: list[ProviderCriterion]
    total_suggested_score: Decimal = Field(ge=0)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1, max_length=4000)
    feedback: str | None = Field(None, max_length=4000)
    error_type: str | None = Field(None, max_length=80)
    confidence: Decimal = Field(ge=0, le=1)
    abstain_reason: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def criterion_sum_matches_total(self) -> "ProviderOutput":
        if sum((item.score for item in self.criteria), Decimal("0")) != self.total_suggested_score:
            raise ValueError("criterion score sum does not equal total")
        return self


class GradingProvider(Protocol):
    name: str
    version: str
    is_demo: bool

    def grade(
        self, answer: str, max_score: Decimal, context: dict[str, Any] | None = None
    ) -> GradeSuggestion: ...


class UnavailableProvider:
    name, version, is_demo = "unavailable", "none", False

    def grade(
        self, answer: str, max_score: Decimal, context: dict[str, Any] | None = None
    ) -> GradeSuggestion:
        return GradeSuggestion(
            None,
            None,
            "主观题评分 Provider 不可用，必须由教师人工评分",
            abstain_reason="provider_unavailable",
        )


class FakeGradingProvider:
    name, version, is_demo = "fake", "test-v1", True

    def grade(
        self, answer: str, max_score: Decimal, context: dict[str, Any] | None = None
    ) -> GradeSuggestion:
        return GradeSuggestion(
            max_score if answer.strip() else Decimal("0"),
            Decimal("0.99"),
            "测试适配器结果",
        )


class OpenAICompatibleGradingProvider:
    name, is_demo = "openai_compatible", False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.version = settings.grading_model or "unconfigured"

    def grade(
        self, answer: str, max_score: Decimal, context: dict[str, Any] | None = None
    ) -> GradeSuggestion:
        if (
            not self.settings.grading_base_url
            or not self.settings.grading_api_key
            or not self.settings.grading_model
        ):
            return GradeSuggestion(
                None,
                None,
                "评分 Provider 配置不完整，已转人工评分",
                abstain_reason="provider_configuration_incomplete",
            )
        payload = {
            "model": self.settings.grading_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是评分建议系统。只返回 JSON；建议不直接成为正式成绩。"
                        "逐项依据 rubric_criteria 评分，每个评分项必须且只能出现一次，不能漏项。"
                        "每个评分项用 reason 简要说明得分依据。"
                        "每个评分项只引用 evidence_regions 中存在的 id；"
                        "没有充分证据时必须 abstain。"
                        "不要因为最终答案正确就默认过程分满分，也不要猜测无法识别的内容。"
                        "字段必须包含 criteria,total_suggested_score,evidence,"
                        "reasoning_summary,feedback,error_type,confidence,abstain_reason。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            **(context or {}),
                            "student_answer": answer,
                            "max_score": str(max_score),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        request = urllib.request.Request(
            self.settings.grading_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Authorization": f"Bearer {self.settings.grading_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.grading_timeout_seconds
            ) as response:
                envelope = json.loads(response.read())
            raw = envelope["choices"][0]["message"]["content"]
            output = ProviderOutput.model_validate(json.loads(raw))
            if output.total_suggested_score > max_score:
                raise ValueError("total score exceeds question maximum")
            if output.abstain_reason:
                return GradeSuggestion(
                    None,
                    output.confidence,
                    output.reasoning_summary,
                    output.feedback,
                    output.error_type,
                    abstain_reason=output.abstain_reason,
                )
            if not output.evidence:
                return GradeSuggestion(
                    None,
                    output.confidence,
                    output.reasoning_summary,
                    output.feedback,
                    output.error_type,
                    abstain_reason="evidence_required",
                )
            criterion_maxima = {
                str(item["id"]): Decimal(str(item["max_points"]))
                for item in (context or {}).get("rubric_criteria", [])
            }
            criterion_ids = [item.criterion_id for item in output.criteria]
            if len(criterion_ids) != len(set(criterion_ids)) or set(criterion_ids) != set(
                criterion_maxima
            ):
                raise ValueError("criteria must cover every rubric item exactly once")
            evidence_ids = {
                str(item["id"])
                for item in (context or {}).get("evidence_regions", [])
                if isinstance(item, dict) and item.get("id")
            }
            for criterion in output.criteria:
                if (
                    criterion.criterion_id not in criterion_maxima
                    or criterion.score > criterion_maxima[criterion.criterion_id]
                ):
                    raise ValueError("criterion is unknown or exceeds maximum")
                if not criterion.evidence_refs or not set(criterion.evidence_refs) <= evidence_ids:
                    raise ValueError("criterion evidence reference is missing or unknown")
            return GradeSuggestion(
                output.total_suggested_score,
                output.confidence,
                output.reasoning_summary,
                output.feedback,
                output.error_type,
                {item.criterion_id: item.score for item in output.criteria},
                {
                    item.criterion_id: item.reason or output.reasoning_summary
                    for item in output.criteria
                },
                {item.criterion_id: item.evidence_refs for item in output.criteria},
                output.evidence,
            )
        except (TimeoutError, urllib.error.URLError):
            return GradeSuggestion(
                None, None, "评分 Provider 超时或不可达，已转人工评分", abstain_reason="timeout"
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
            return GradeSuggestion(
                None,
                None,
                "评分 Provider 返回不符合 Schema，已转人工评分",
                abstain_reason="invalid_response",
            )


def provider_from_settings(settings: Settings) -> GradingProvider:
    name = getattr(settings, "grading_provider", "unavailable").lower()
    if name == "fake" and settings.app_env.lower() != "production":
        return FakeGradingProvider()
    if name in {"openai", "openai_compatible", "compatible"}:
        return OpenAICompatibleGradingProvider(settings)
    return UnavailableProvider()


def normalize_objective(value: str) -> str:
    """Normalize harmless OCR/typing differences without changing mathematical meaning."""
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _choice_value(value: str, *, multiple: bool) -> str | None:
    normalized = normalize_objective(value)
    normalized = re.sub(r"^(?:答案|选择|选)[:：]?", "", normalized)
    labels = re.findall(r"[a-z]", normalized)
    residue = re.sub(r"[a-z,，、;；/|+&.。:：()（）\[\]【】]", "", normalized)
    if residue or not labels:
        return None
    if not multiple and len(labels) != 1:
        return None
    return "".join(sorted(set(labels))) if multiple else labels[0]


def _truth_value(value: str) -> bool | None:
    normalized = normalize_objective(value).strip(".。()（）")
    if normalized in {"true", "t", "1", "对", "正确", "是", "√", "✓"}:
        return True
    if normalized in {"false", "f", "0", "错", "错误", "否", "×", "✗", "x"}:
        return False
    return None


def _numeric_value(value: str) -> Decimal | None:
    normalized = normalize_objective(value).replace("，", ",")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", normalized):
        return None
    try:
        return Decimal(normalized)
    except ArithmeticError:
        return None


def objective_answers_match(answer: str, expected: str, question_type: str | None) -> bool:
    if normalize_objective(answer) == normalize_objective(expected):
        return True
    if question_type in {"single_choice", "multiple_choice"}:
        multiple = question_type == "multiple_choice"
        left = _choice_value(answer, multiple=multiple)
        right = _choice_value(expected, multiple=multiple)
        return left is not None and right is not None and left == right
    if question_type == "true_false":
        left_truth = _truth_value(answer)
        right_truth = _truth_value(expected)
        return left_truth is not None and right_truth is not None and left_truth == right_truth
    if question_type == "fill_blank":
        left_number = _numeric_value(answer)
        right_number = _numeric_value(expected)
        if left_number is not None and right_number is not None:
            return left_number == right_number
    return False


def grade_objective(
    answer: str,
    accepted: list[str],
    max_score: Decimal,
    question_type: str | None = None,
) -> GradeSuggestion:
    normalized = normalize_objective(answer)
    correct = bool(normalized) and any(
        objective_answers_match(answer, expected, question_type) for expected in accepted
    )
    return GradeSuggestion(
        max_score if correct else Decimal("0"),
        Decimal("1"),
        "确定性答案规范化精确匹配",
        None if correct else "答案与标准答案不一致",
        None if correct else "incorrect",
    )
