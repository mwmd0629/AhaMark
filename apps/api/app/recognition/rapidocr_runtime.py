"""Lazy bridge from validated local artifacts to the optional RapidOCR runtime."""

from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Protocol, cast

from app.recognition.rapidocr_adapter import (
    RapidOcrAdapterError,
    construct_rapidocr_engine,
)
from app.recognition.rapidocr_artifacts import (
    RapidOcrArtifactError,
    ValidatedRapidOcrBundle,
    ensure_rapidocr_bundle_unchanged,
)

MIN_REC_CHARACTERS = 2
MAX_REC_CHARACTERS = 100_000
MAX_CHARACTER_METADATA_LENGTH = 4 * 1024 * 1024


class RapidOcrRuntimeError(RuntimeError):
    """Stable, non-sensitive failure from the optional local OCR runtime bridge."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _ModelMetadata(Protocol):
    custom_metadata_map: Mapping[str, object]


class _InferenceSession(Protocol):
    def get_modelmeta(self) -> _ModelMetadata: ...


class _EngineTypes(Protocol):
    ONNXRUNTIME: Enum


class _RapidOcrModule(Protocol):
    RapidOCR: Callable[..., Any]
    EngineType: _EngineTypes


class _OnnxRuntimeModule(Protocol):
    InferenceSession: SessionFactory


ModuleLoader = Callable[[str], object]
VersionReader = Callable[[str], str]
SessionFactory = Callable[..., _InferenceSession]


def _runtime_error(code: str, message: str) -> RapidOcrRuntimeError:
    return RapidOcrRuntimeError(code, message)


def _verify_distribution_versions(
    bundle: ValidatedRapidOcrBundle, version_reader: VersionReader
) -> None:
    try:
        rapidocr_version = version_reader("rapidocr")
        onnxruntime_version = version_reader("onnxruntime")
    except Exception as exc:
        raise _runtime_error(
            "OCR_RUNTIME_VERSION_UNAVAILABLE", "local OCR runtime version is unavailable"
        ) from exc
    if (
        rapidocr_version != bundle.rapidocr_version
        or onnxruntime_version != bundle.onnxruntime_version
    ):
        raise _runtime_error(
            "OCR_RUNTIME_VERSION_MISMATCH", "local OCR runtime version is not approved"
        )


def _load_runtime_modules(module_loader: ModuleLoader) -> tuple[object, object]:
    try:
        rapidocr_module = module_loader("rapidocr")
        onnxruntime_module = module_loader("onnxruntime")
    except Exception as exc:
        raise _runtime_error(
            "OCR_RUNTIME_IMPORT_FAILED", "local OCR runtime could not be loaded"
        ) from exc
    return rapidocr_module, onnxruntime_module


def _runtime_components(
    rapidocr_module: object, onnxruntime_module: object
) -> tuple[Callable[..., Any], Enum, SessionFactory]:
    try:
        rapidocr = cast(_RapidOcrModule, rapidocr_module)
        onnxruntime = cast(_OnnxRuntimeModule, onnxruntime_module)
        constructor = rapidocr.RapidOCR
        engine_type = rapidocr.EngineType.ONNXRUNTIME
        session_factory = onnxruntime.InferenceSession
    except (AttributeError, TypeError) as exc:
        raise _runtime_error(
            "OCR_RUNTIME_IMPORT_FAILED", "local OCR runtime has an invalid interface"
        ) from exc
    if not callable(constructor) or not callable(session_factory):
        raise _runtime_error(
            "OCR_RUNTIME_IMPORT_FAILED", "local OCR runtime has an invalid interface"
        )
    return constructor, engine_type, session_factory


def _validate_rec_character_metadata(
    bundle: ValidatedRapidOcrBundle, session_factory: SessionFactory
) -> None:
    ensure_rapidocr_bundle_unchanged(bundle)
    try:
        session = session_factory(str(bundle.rec.path), providers=["CPUExecutionProvider"])
        metadata = session.get_modelmeta().custom_metadata_map
        raw_characters = metadata.get("character")
    except RapidOcrArtifactError:
        raise
    except Exception as exc:
        raise _runtime_error(
            "OCR_REC_MODEL_METADATA_INVALID", "recognition model metadata is invalid"
        ) from exc
    if (
        not isinstance(raw_characters, str)
        or not raw_characters
        or len(raw_characters) > MAX_CHARACTER_METADATA_LENGTH
    ):
        raise _runtime_error(
            "OCR_REC_MODEL_METADATA_INVALID", "recognition model metadata is invalid"
        )
    characters = raw_characters.splitlines()
    if not MIN_REC_CHARACTERS <= len(characters) <= MAX_REC_CHARACTERS or any(
        not character for character in characters
    ):
        raise _runtime_error(
            "OCR_REC_MODEL_METADATA_INVALID", "recognition model metadata is invalid"
        )


def construct_local_rapidocr_engine(
    bundle: ValidatedRapidOcrBundle,
    *,
    module_loader: ModuleLoader = importlib.import_module,
    version_reader: VersionReader = importlib.metadata.version,
    session_factory: SessionFactory | None = None,
) -> Any:
    """Validate installed runtimes and model metadata, then construct RapidOCR."""

    _verify_distribution_versions(bundle, version_reader)
    rapidocr_module, onnxruntime_module = _load_runtime_modules(module_loader)
    constructor, engine_type, default_session_factory = _runtime_components(
        rapidocr_module, onnxruntime_module
    )
    _validate_rec_character_metadata(bundle, session_factory or default_session_factory)
    try:
        return construct_rapidocr_engine(
            bundle,
            constructor=constructor,
            engine_type=engine_type,
        )
    except RapidOcrArtifactError:
        raise
    except RapidOcrAdapterError as exc:
        raise _runtime_error(
            "OCR_ENGINE_INIT_FAILED", "local OCR engine initialization failed"
        ) from exc


def rapidocr_engine_factory(
    bundle: ValidatedRapidOcrBundle,
    *,
    module_loader: ModuleLoader = importlib.import_module,
    version_reader: VersionReader = importlib.metadata.version,
    session_factory: SessionFactory | None = None,
) -> Callable[[], Any]:
    """Return a closure that performs no imports or runtime work until invoked."""

    def factory() -> Any:
        return construct_local_rapidocr_engine(
            bundle,
            module_loader=module_loader,
            version_reader=version_reader,
            session_factory=session_factory,
        )

    return factory
