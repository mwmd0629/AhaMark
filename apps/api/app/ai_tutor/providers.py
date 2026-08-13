from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from app.ai_tutor.prompts import SYSTEM_PROMPT
from app.ai_tutor.schema import WrongQuestionReply, WrongQuestionTutorInput, validate_reply
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

TutorProviderResponse = StructuredProviderResult[WrongQuestionReply]


class AITutorProvider(Protocol):
    name: str
    endpoint_mode: str

    def answer(self, payload: dict[str, Any], *, safety_subject: str) -> TutorProviderResponse: ...


class UnavailableAITutorProvider:
    name, endpoint_mode = "unavailable", "none"

    def __init__(self, error: str = "provider_unavailable"):
        self.error = error

    def answer(self, payload: dict[str, Any], *, safety_subject: str) -> TutorProviderResponse:
        del payload, safety_subject
        return StructuredProviderResult(None, error=self.error)


class FakeAITutorProvider:
    name, endpoint_mode = "fake", "deterministic_test_only"

    def answer(self, payload: dict[str, Any], *, safety_subject: str) -> TutorProviderResponse:
        del safety_subject
        try:
            request = WrongQuestionTutorInput.model_validate(payload)
        except ValidationError:
            return StructuredProviderResult(None, error="provider_input_invalid")
        output = WrongQuestionReply(
            schema_version="wrong-question-reply-v1",
            verdict="uncertain",
            confidence="0",
            explanation="Test provider cannot decide whether the answer was misjudged.",
            evidence_refs=[],
            knowledge_gaps=[],
            next_steps=["Ask the teacher for an official review."],
            requires_teacher_review=True,
        )
        return StructuredProviderResult(validate_reply(output, request))


class OpenAIWrongQuestionTutorProvider:
    name, endpoint_mode = "openai", "responses"

    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self._client = client

    def answer(self, payload: dict[str, Any], *, safety_subject: str) -> TutorProviderResponse:
        if not self.settings.ai_external_requests_enabled:
            return StructuredProviderResult(None, error="provider_external_requests_disabled")
        if not self.settings.ai_tutor_model or not openai_connection_configured(self.settings):
            return StructuredProviderResult(None, error="provider_configuration_incomplete")
        try:
            request = WrongQuestionTutorInput.model_validate(payload)
        except ValidationError:
            return StructuredProviderResult(None, error="provider_input_invalid")
        if len(request.conversation) > self.settings.ai_tutor_max_conversation_messages:
            return StructuredProviderResult(None, error="conversation_limit_exceeded")
        serialized = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        estimated_tokens = max(1, (len(serialized.encode("utf-8")) + 3) // 4)
        if estimated_tokens > self.settings.ai_tutor_max_input_tokens:
            return StructuredProviderResult(None, error="input_token_limit_exceeded")
        try:
            client = self._client or build_openai_client(
                self.settings,
                timeout_seconds=self.settings.ai_tutor_timeout_seconds,
            )
            safety_id = safety_identifier(safety_subject, ai_safety_secret(self.settings))
        except (OpenAIConfigurationError, ValueError) as exc:
            return StructuredProviderResult(None, error=getattr(exc, "code", str(exc)))
        result = request_structured_output(
            client,
            model=self.settings.ai_tutor_model,
            instructions=SYSTEM_PROMPT,
            input_messages=[
                {
                    "role": "user",
                    "content": "UNTRUSTED_DATA\n"
                    + sanitize_untrusted_text(serialized, limit=max(len(serialized) + 1, 12_000)),
                }
            ],
            output_type=WrongQuestionReply,
            max_output_tokens=self.settings.ai_tutor_max_output_tokens,
            max_retries=self.settings.ai_tutor_max_retries,
            safety_id=safety_id,
            prompt_version=self.settings.ai_tutor_prompt_version,
            schema_version=self.settings.ai_tutor_schema_version,
        )
        if result.output is None:
            return result
        try:
            output = validate_reply(result.output, request)
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


def provider_from_settings(settings: Settings) -> AITutorProvider:
    name = settings.ai_tutor_provider.lower()
    if name == "fake" and settings.app_env.lower() == "test":
        return FakeAITutorProvider()
    if name in {"openai", "openai_compatible"}:
        if not settings.ai_external_requests_enabled:
            return UnavailableAITutorProvider("provider_external_requests_disabled")
        if not settings.ai_tutor_model or not openai_connection_configured(settings):
            return UnavailableAITutorProvider("provider_configuration_incomplete")
        return OpenAIWrongQuestionTutorProvider(settings)
    return UnavailableAITutorProvider()
