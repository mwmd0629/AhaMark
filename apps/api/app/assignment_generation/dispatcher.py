"""Server-owned routing from generation stages to draft-only providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.assignment_generation.providers import (
    AssignmentProviderResponse,
    OpenAICompatibleAssignmentGenerationProvider,
    ProviderSelection,
    StageName,
    select_provider,
)
from app.core.config import Settings


@dataclass(frozen=True)
class DispatchedProviderResult:
    selection: ProviderSelection
    response: AssignmentProviderResponse


def dispatch_stage(
    settings: Settings,
    requested_mode: str,
    stage: StageName,
    payload: dict[str, Any],
) -> DispatchedProviderResult:
    """Dispatch using server configuration; payloads cannot override model or endpoint."""
    selection = select_provider(settings, requested_mode)
    if selection.name != "openai_compatible" or not selection.available:
        return DispatchedProviderResult(
            selection,
            AssignmentProviderResponse(None, error=selection.error_code or "provider_unavailable"),
        )
    provider = OpenAICompatibleAssignmentGenerationProvider(settings)
    return DispatchedProviderResult(selection, provider.generate(stage, payload))
