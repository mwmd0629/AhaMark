"""Pure adapter for an injected RapidOCR v3 constructor and validated artifacts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.recognition.rapidocr_artifacts import (
    ValidatedRapidOcrBundle,
    ensure_rapidocr_bundle_unchanged,
)

RAPIDOCR_ENGINE_TYPE = "onnxruntime"


class RapidOcrAdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def rapidocr_v3_params(bundle: ValidatedRapidOcrBundle) -> dict[str, object]:
    """Build only allowlisted local paths; no manifest-controlled runtime options pass through."""

    params: dict[str, object] = {
        "Global.use_det": True,
        "Global.use_cls": True,
        "Global.use_rec": True,
        "Global.model_root_dir": str(bundle.root),
        "Global.font_path": str(bundle.font.path),
        "Det.engine_type": RAPIDOCR_ENGINE_TYPE,
        "Det.model_path": str(bundle.det.path),
        "Cls.engine_type": RAPIDOCR_ENGINE_TYPE,
        "Cls.model_path": str(bundle.cls.path),
        "Rec.engine_type": RAPIDOCR_ENGINE_TYPE,
        "Rec.model_path": str(bundle.rec.path),
        "Rec.rec_keys_path": str(bundle.keys.path),
    }
    return params


def construct_rapidocr_engine(
    bundle: ValidatedRapidOcrBundle,
    *,
    constructor: Callable[..., Any],
) -> Any:
    """Construct through explicit v3 params; a constructor must always be injected."""

    ensure_rapidocr_bundle_unchanged(bundle)
    try:
        return constructor(params=rapidocr_v3_params(bundle))
    except Exception as exc:
        raise RapidOcrAdapterError(
            "OCR_ENGINE_INIT_FAILED", "local OCR engine initialization failed"
        ) from exc
