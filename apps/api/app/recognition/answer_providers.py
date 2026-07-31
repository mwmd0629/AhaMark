import base64
import json
import unicodedata
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import Settings
from app.recognition.pipeline import PageArtifact, ProviderBlock, RapidOcrProvider

ProviderKind = Literal["printed_text", "handwriting_text", "math_formula", "multimodal_document"]
BlockType = Literal["text", "formula", "matrix", "table", "diagram", "unknown"]


class AnswerProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AnswerProvider(Protocol):
    name: str
    version: str

    def recognize(self, image: PageArtifact, kind: ProviderKind) -> list[ProviderBlock]: ...


class UnavailableAnswerProvider:
    name = "unavailable"
    version = "none"

    def recognize(self, image: PageArtifact, kind: ProviderKind) -> list[ProviderBlock]:
        raise AnswerProviderError("PROVIDER_UNAVAILABLE", f"{kind} provider is not configured")


class RapidOcrAnswerProvider:
    name = "rapidocr"

    def __init__(self) -> None:
        self.provider = RapidOcrProvider()
        self.version = self.provider.version

    def recognize(self, image: PageArtifact, kind: ProviderKind) -> list[ProviderBlock]:
        if kind != "printed_text":
            raise AnswerProviderError(
                "PROVIDER_CAPABILITY_UNAVAILABLE",
                "RapidOCR only supports printed_text",
            )
        available, reason = self.provider.available()
        if not available:
            raise AnswerProviderError("PROVIDER_UNAVAILABLE", reason or "RapidOCR unavailable")
        return self.provider.recognize(image)


class FakeAnswerProvider:
    name = "fake"
    version = "answer-evidence-1"

    def recognize(self, image: PageArtifact, kind: ProviderKind) -> list[ProviderBlock]:
        if kind == "math_formula":
            return [ProviderBlock("formula", "x²+1", "x^{2}+1", 0.99, (0, 0, 1, 1))]
        return [ProviderBlock("text", "Student answer", None, 0.99, (0, 0, 1, 1))]


class OpenAICompatibleAnswerProvider:
    name = "openai-compatible"

    def __init__(self, settings: Settings):
        self.base_url = settings.answer_recognition_base_url
        self.api_key = settings.answer_recognition_api_key
        self.model = settings.answer_recognition_model
        self.timeout = settings.answer_recognition_timeout_seconds
        self.version = self.model or "unconfigured"

    def recognize(self, image: PageArtifact, kind: ProviderKind) -> list[ProviderBlock]:
        if not self.base_url or not self.api_key or not self.model:
            raise AnswerProviderError(
                "PROVIDER_UNAVAILABLE", "multimodal provider is not configured"
            )
        prompt = (
            "Transcribe only visible student work. Return JSON object {blocks:[{block_type,"
            "raw_text,latex,confidence,bbox:[x,y,width,height]}],abstain:boolean}. "
            "Never repair mathematics or infer missing content."
        )
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Capability: {kind}. {prompt}"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(image.content).decode("ascii")
                            },
                        },
                    ],
                }
            ],
        }
        try:
            response = httpx.post(
                self.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise AnswerProviderError(
                "PROVIDER_TIMEOUT", "provider timed out", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise AnswerProviderError(
                "PROVIDER_UNAVAILABLE", "provider request failed", retryable=True
            ) from exc
        if response.status_code == 429:
            raise AnswerProviderError(
                "PROVIDER_RATE_LIMITED", "provider rate limited", retryable=True
            )
        if response.status_code >= 400:
            raise AnswerProviderError(
                "PROVIDER_REJECTED", f"provider returned {response.status_code}"
            )
        try:
            content = response.json()["choices"][0]["message"]["content"]
            document = json.loads(content) if isinstance(content, str) else content
            if document.get("abstain"):
                raise AnswerProviderError("PROVIDER_ABSTAINED", "provider abstained")
            raw_blocks = document["blocks"]
        except AnswerProviderError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AnswerProviderError(
                "PROVIDER_INVALID_JSON", "provider returned invalid JSON"
            ) from exc
        if not raw_blocks:
            raise AnswerProviderError("PROVIDER_EMPTY_RESULT", "provider returned no blocks")
        blocks: list[ProviderBlock] = []
        for item in raw_blocks:
            try:
                block_type = str(item.get("block_type", "unknown"))
                if block_type not in {"text", "formula", "matrix", "table", "diagram", "unknown"}:
                    block_type = "unknown"
                bbox = tuple(float(value) for value in item["bbox"])
                if len(bbox) != 4 or any(value < 0 or value > 1 for value in bbox):
                    raise ValueError
                blocks.append(
                    ProviderBlock(
                        block_type,
                        item.get("raw_text"),
                        item.get("latex"),
                        float(item["confidence"]) if item.get("confidence") is not None else None,
                        bbox,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise AnswerProviderError(
                    "PROVIDER_INVALID_JSON", "provider block schema is invalid"
                ) from exc
        return blocks


def provider_from_settings(settings: Settings) -> AnswerProvider:
    name = settings.answer_recognition_provider
    if name == "unavailable" and settings.recognition_provider in {"fake", "rapidocr"}:
        name = settings.recognition_provider
    if name == "fake" and settings.app_env.lower() == "test":
        return FakeAnswerProvider()
    if name == "rapidocr":
        return RapidOcrAnswerProvider()
    if name == "openai-compatible":
        return OpenAICompatibleAnswerProvider(settings)
    return UnavailableAnswerProvider()


@dataclass(frozen=True)
class NormalizedMath:
    text: str | None
    latex: str | None
    warnings: list[str]


def normalize_math(raw_text: str | None, latex: str | None, block_type: str) -> NormalizedMath:
    text = unicodedata.normalize("NFKC", raw_text) if raw_text is not None else None
    warnings: list[str] = []
    normalized_latex = latex.strip() if latex else None
    if normalized_latex:
        pairs = {"{": "}", "[": "]", "(": ")"}
        stack: list[str] = []
        for char in normalized_latex:
            if char in pairs:
                stack.append(pairs[char])
            elif char in pairs.values() and (not stack or stack.pop() != char):
                warnings.append("LATEX_UNBALANCED")
                break
        if stack:
            warnings.append("LATEX_UNBALANCED")
    if block_type == "matrix" and (not normalized_latex or "\\begin" not in normalized_latex):
        warnings.append("MATRIX_STRUCTURE_UNCERTAIN")
    if any(char in (text or "") for char in "□�"):
        warnings.append("AMBIGUOUS_CHARACTER")
    return NormalizedMath(text, normalized_latex, sorted(set(warnings)))
