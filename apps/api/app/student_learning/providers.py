from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.openai_client import (
    OpenAIConfigurationError,
    StructuredProviderResult,
    ai_safety_secret,
    build_openai_client,
    openai_connection_configured,
    request_structured_output,
    safety_identifier,
    sanitize_untrusted_text,
)
from app.student_learning.prompts import SYSTEM_PROMPT
from app.student_learning.schema import (
    StudentLearningAnalysisInput,
    StudentLearningAnalysisOutput,
    validate_analysis,
)

LearningProviderResponse = StructuredProviderResult[StudentLearningAnalysisOutput]


class StudentLearningProvider(Protocol):
    name: str
    endpoint_mode: str

    def analyze(
        self, payload: dict[str, Any], *, safety_subject: str
    ) -> LearningProviderResponse: ...


class UnavailableStudentLearningProvider:
    name, endpoint_mode = "unavailable", "none"

    def __init__(self, error: str = "provider_unavailable"):
        self.error = error

    def analyze(self, payload: dict[str, Any], *, safety_subject: str) -> LearningProviderResponse:
        del payload, safety_subject
        return StructuredProviderResult(None, error=self.error)


class FakeStudentLearningProvider:
    name, endpoint_mode = "fake", "deterministic_test_only"

    def analyze(self, payload: dict[str, Any], *, safety_subject: str) -> LearningProviderResponse:
        del safety_subject
        try:
            request = StudentLearningAnalysisInput.model_validate(payload)
        except ValidationError:
            return StructuredProviderResult(None, error="provider_input_invalid")
        output = StudentLearningAnalysisOutput(
            schema_version="student-learning-analysis-v1",
            summary="Test provider generated no personal conclusions.",
            strengths=[],
            weaknesses=[],
            knowledge_gaps=[],
            study_plan=[],
            resource_recommendations=[],
            disclaimer="Test-only analysis; released results remain authoritative.",
        )
        return StructuredProviderResult(validate_analysis(output, request))


class OpenAIStudentLearningProvider:
    name, endpoint_mode = "openai", "responses"

    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self._client = client

    def analyze(self, payload: dict[str, Any], *, safety_subject: str) -> LearningProviderResponse:
        if not self.settings.ai_external_requests_enabled:
            return StructuredProviderResult(None, error="provider_external_requests_disabled")
        if not self.settings.student_learning_model or not openai_connection_configured(
            self.settings
        ):
            return StructuredProviderResult(None, error="provider_configuration_incomplete")
        try:
            request = StudentLearningAnalysisInput.model_validate(payload)
        except ValidationError:
            return StructuredProviderResult(None, error="provider_input_invalid")
        if len(request.released_results) > self.settings.student_learning_max_grade_releases:
            return StructuredProviderResult(None, error="grade_release_limit_exceeded")
        serialized = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        estimated_tokens = max(1, (len(serialized.encode("utf-8")) + 3) // 4)
        if estimated_tokens > self.settings.student_learning_max_input_tokens:
            return StructuredProviderResult(None, error="input_token_limit_exceeded")
        try:
            client = self._client or build_openai_client(
                self.settings,
                timeout_seconds=self.settings.student_learning_timeout_seconds,
            )
            safety_id = safety_identifier(safety_subject, ai_safety_secret(self.settings))
        except (OpenAIConfigurationError, ValueError) as exc:
            return StructuredProviderResult(None, error=getattr(exc, "code", str(exc)))
        result = request_structured_output(
            client,
            model=self.settings.student_learning_model,
            instructions=SYSTEM_PROMPT,
            input_messages=[
                {
                    "role": "user",
                    "content": "UNTRUSTED_DATA\n"
                    + sanitize_untrusted_text(serialized, limit=max(len(serialized) + 1, 12_000)),
                }
            ],
            output_type=StudentLearningAnalysisOutput,
            max_output_tokens=self.settings.student_learning_max_output_tokens,
            max_retries=self.settings.student_learning_max_retries,
            safety_id=safety_id,
            prompt_version=self.settings.student_learning_prompt_version,
            schema_version=self.settings.student_learning_schema_version,
        )
        if result.output is None:
            return result
        try:
            output = validate_analysis(result.output, request)
        except ValueError:
            return StructuredProviderResult(
                None,
                request_id=result.request_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                request_hash=result.request_hash,
                response_hash=result.response_hash,
                error="provider_schema_invalid",
                attempts=result.attempts,
            )
        return StructuredProviderResult(
            output,
            request_id=result.request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_hash=result.request_hash,
            response_hash=result.response_hash,
            attempts=result.attempts,
        )


def provider_from_settings(settings: Settings) -> StudentLearningProvider:
    name = settings.student_learning_provider.lower()
    if name == "fake" and settings.app_env.lower() == "test":
        return FakeStudentLearningProvider()
    if name in {"openai", "openai_compatible"}:
        if not settings.ai_external_requests_enabled:
            return UnavailableStudentLearningProvider("provider_external_requests_disabled")
        if not settings.student_learning_model or not openai_connection_configured(settings):
            return UnavailableStudentLearningProvider("provider_configuration_incomplete")
        return OpenAIStudentLearningProvider(settings)
    return UnavailableStudentLearningProvider()
