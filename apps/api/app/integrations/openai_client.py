from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import Settings

if TYPE_CHECKING:
    from openai import OpenAI

OutputT = TypeVar("OutputT", bound=BaseModel)


class OpenAIConfigurationError(RuntimeError):
    """Stable configuration error that is safe to persist or return to a worker."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StructuredProviderResult(Generic[OutputT]):
    output: OutputT | None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_hash: str | None = None
    response_hash: str | None = None
    error: str | None = None
    retryable: bool = False
    attempts: int = 1


def canonical_json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sanitize_untrusted_text(value: str, limit: int = 12000) -> str:
    value = re.sub(
        r"<script\b[^>]*>.*?</script\s*>",
        "[removed script]",
        value,
        flags=re.I | re.S,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[redacted email]",
        value,
    )
    value = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[redacted phone]", value)
    value = re.sub(r"(?<!\d)\d{17}[0-9Xx](?!\d)", "[redacted id]", value)
    return value[:limit]


def safety_identifier(subject: str, secret: str) -> str:
    """Return a stable pseudonym; never send a student identifier to the provider."""

    normalized = subject.strip()
    if not normalized:
        raise ValueError("safety_subject_required")
    digest = hmac.new(secret.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256)
    return "ahamark_" + digest.hexdigest()[:48]


def ai_safety_secret(settings: Settings) -> str:
    if settings.ai_safety_hmac_secret is not None:
        value = settings.ai_safety_hmac_secret.get_secret_value().strip()
        if value:
            return value
    if settings.app_env.lower() == "production":
        raise OpenAIConfigurationError("provider_safety_secret_missing")
    return settings.session_hmac_secret


def safe_openai_base_url(value: str, *, allow_private_for_tests: bool = False) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise OpenAIConfigurationError("provider_endpoint_not_allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        raise OpenAIConfigurationError("provider_endpoint_not_allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global and not allow_private_for_tests:
        raise OpenAIConfigurationError("provider_endpoint_not_allowed")
    if host.endswith((".local", ".internal", ".localhost")) and not allow_private_for_tests:
        raise OpenAIConfigurationError("provider_endpoint_not_allowed")
    return value.rstrip("/")


def _api_key(settings: Settings, override: str | None) -> str:
    if override and override.strip():
        return override.strip()
    if settings.openai_api_key is not None:
        value = settings.openai_api_key.get_secret_value().strip()
        if value:
            return value
    raise OpenAIConfigurationError("provider_configuration_incomplete")


def openai_connection_configured(
    settings: Settings,
    *,
    api_key_override: str | None = None,
) -> bool:
    try:
        _api_key(settings, api_key_override)
    except OpenAIConfigurationError:
        return False
    return True


def build_openai_client(
    settings: Settings,
    *,
    timeout_seconds: float,
    base_url_override: str | None = None,
    api_key_override: str | None = None,
) -> OpenAI:
    """Build a server-only client with SDK retries disabled.

    Retry policy is implemented by ``request_structured_output`` so audit rows can
    record the actual number of attempts consistently across every AI feature.
    """

    if not settings.ai_external_requests_enabled:
        raise OpenAIConfigurationError("provider_external_requests_disabled")
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency installation failure
        raise OpenAIConfigurationError("openai_sdk_unavailable") from exc

    base_url = safe_openai_base_url(
        base_url_override or settings.openai_base_url,
        allow_private_for_tests=(
            settings.app_env.lower() == "test" and settings.openai_allow_private_base_url_for_tests
        ),
    )
    kwargs: dict[str, Any] = {
        "api_key": _api_key(settings, api_key_override),
        "base_url": base_url,
        "timeout": timeout_seconds,
        "max_retries": 0,
    }
    if settings.openai_organization:
        kwargs["organization"] = settings.openai_organization
    if settings.openai_project:
        kwargs["project"] = settings.openai_project
    return OpenAI(**kwargs)


def _provider_error(exc: Exception) -> tuple[str, bool, str | None]:
    request_id = getattr(exc, "request_id", None)
    class_name = type(exc).__name__
    if class_name == "LengthFinishReasonError":
        return "provider_output_limit_exceeded", False, request_id
    if class_name == "ContentFilterFinishReasonError":
        return "provider_content_filtered", False, request_id

    try:
        import openai
    except ImportError:  # pragma: no cover - handled before a request can be made
        return "openai_sdk_unavailable", False, request_id

    if isinstance(exc, openai.APITimeoutError):
        return "provider_timeout", True, request_id
    if isinstance(exc, openai.RateLimitError):
        return "provider_rate_limited", True, request_id
    if isinstance(exc, openai.APIConnectionError):
        return "provider_network_error", True, request_id
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        retryable = status in {408, 409, 429, 500, 502, 503, 504}
        stable = {
            400: "provider_request_invalid",
            401: "provider_authentication_failed",
            403: "provider_permission_denied",
            404: "provider_model_not_found",
            408: "provider_timeout",
            409: "provider_conflict",
            422: "provider_request_invalid",
            429: "provider_rate_limited",
        }.get(status, "provider_server_error" if status >= 500 else "provider_request_failed")
        return stable, retryable, request_id
    if isinstance(exc, openai.OpenAIError):
        return "provider_request_failed", False, request_id
    return "provider_internal_error", False, request_id


def _is_refusal(response: Any) -> bool:
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "refusal":
                return True
    return False


def request_structured_output(
    client: Any,
    *,
    model: str,
    instructions: str,
    input_messages: list[dict[str, Any]],
    output_type: type[OutputT],
    max_output_tokens: int,
    max_retries: int,
    safety_id: str,
    prompt_version: str,
    schema_version: str,
) -> StructuredProviderResult[OutputT]:
    """Call Responses API with strict structured output and stable failure codes."""

    request_description = {
        "model": model,
        "instructions": instructions,
        "input": input_messages,
        "output_schema": output_type.model_json_schema(),
        "max_output_tokens": max_output_tokens,
        "safety_identifier": safety_id,
        "store": False,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
    }
    request_hash = canonical_json_hash(request_description)
    attempts = max(1, max_retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            response = client.responses.parse(
                model=model,
                instructions=instructions,
                input=input_messages,
                text_format=output_type,
                max_output_tokens=max_output_tokens,
                safety_identifier=safety_id,
                store=False,
                metadata={
                    "prompt_version": prompt_version,
                    "schema_version": schema_version,
                },
            )
            parsed = getattr(response, "output_parsed", None)
            request_id = getattr(response, "_request_id", None) or getattr(response, "id", None)
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None)
            if parsed is None:
                return StructuredProviderResult(
                    None,
                    request_id=request_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    request_hash=request_hash,
                    error=(
                        "provider_refusal" if _is_refusal(response) else "provider_empty_response"
                    ),
                    attempts=attempt,
                )
            output = output_type.model_validate(parsed)
            return StructuredProviderResult(
                output,
                request_id=request_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_hash=request_hash,
                response_hash=canonical_json_hash(output.model_dump(mode="json")),
                attempts=attempt,
            )
        except ValidationError:
            return StructuredProviderResult(
                None,
                request_hash=request_hash,
                error="provider_schema_invalid",
                attempts=attempt,
            )
        except Exception as exc:  # OpenAI SDK has a versioned exception hierarchy.
            error, retryable, request_id = _provider_error(exc)
            if retryable and attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
                continue
            return StructuredProviderResult(
                None,
                request_id=request_id,
                request_hash=request_hash,
                error=error,
                retryable=retryable,
                attempts=attempt,
            )
    raise AssertionError("provider attempt loop exhausted")
