from __future__ import annotations

import io
import os
import secrets
import threading
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Protocol

import numpy as np
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

PROVIDER_NAME = "paddleocr-ppformulanet-plus-m"
PROVIDER_VERSION = "paddleocr-3.7.0-paddle-3.3.1-PP-FormulaNet-plus-M"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 8_000_000
ALLOWED_REGION_KINDS = {"inline", "display", "unknown"}


class FormulaModel(Protocol):
    def predict(self, image: Any, *, batch_size: int) -> Any: ...


class PaddleFormulaModel:
    def __init__(self, model_dir: Path) -> None:
        from paddleocr import FormulaRecognition

        self._model = FormulaRecognition(
            model_name="PP-FormulaNet_plus-M",
            model_dir=str(model_dir),
            device="cpu",
        )
        self._lock = threading.Lock()

    def predict(self, image: Any, *, batch_size: int) -> Any:
        with self._lock:
            return self._model.predict(image, batch_size=batch_size)


def _required_token() -> str:
    token = os.environ.get("AHAMARK_FORMULA_PROVIDER_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("AHAMARK_FORMULA_PROVIDER_TOKEN must contain at least 32 characters")
    return token


def _model_dir() -> Path:
    raw = os.environ.get("AHAMARK_FORMULA_MODEL_DIR", "")
    if not raw:
        raise RuntimeError("AHAMARK_FORMULA_MODEL_DIR is required")
    path = Path(raw).resolve()
    required = {"inference.json", "inference.pdiparams", "inference.yml"}
    if not path.is_dir() or not required.issubset(item.name for item in path.iterdir()):
        raise RuntimeError("AHAMARK_FORMULA_MODEL_DIR does not contain a complete model")
    return path


@lru_cache(maxsize=1)
def get_model() -> FormulaModel:
    return PaddleFormulaModel(_model_dir())


def _extract_latex(result: object) -> str:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        return ""
    nested = payload.get("res", payload)
    if not isinstance(nested, dict):
        return ""
    latex = nested.get("rec_formula")
    return latex.strip() if isinstance(latex, str) else ""


app = FastAPI(title="AhaMark local formula provider", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "provider": PROVIDER_NAME}


@app.get("/ready")
def ready(authorization: Annotated[str | None, Header()] = None) -> dict[str, str]:
    expected = f"Bearer {_required_token()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid provider credentials")
    get_model()
    return {
        "status": "ready",
        "provider": PROVIDER_NAME,
        "provider_version": PROVIDER_VERSION,
    }


@app.post("/v1/formulas/recognize")
def recognize(
    file: Annotated[UploadFile, File()],
    region_kind: Annotated[str, Form()] = "unknown",
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    expected = f"Bearer {_required_token()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid provider credentials")
    if region_kind not in ALLOWED_REGION_KINDS:
        raise HTTPException(status_code=422, detail="unsupported region kind")
    if file.content_type != "image/png":
        raise HTTPException(status_code=415, detail="only PNG formula crops are accepted")
    content = file.file.read(MAX_IMAGE_BYTES + 1)
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="formula crop exceeds the size limit")
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail="invalid formula crop") from exc
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=413, detail="formula crop exceeds the pixel limit")

    try:
        model_input = np.asarray(image, dtype=np.uint8)
        results = list(get_model().predict(model_input, batch_size=1))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="formula model inference failed") from exc
    latex = _extract_latex(results[0]) if results else ""
    if not latex:
        raise HTTPException(status_code=422, detail="no formula recognized")
    return {
        "provider": PROVIDER_NAME,
        "provider_version": PROVIDER_VERSION,
        "candidates": [
            {
                "latex": latex,
                "confidence": None,
                "warning_codes": ["UNCALIBRATED_CONFIDENCE", "TEACHER_REVIEW_REQUIRED"],
            }
        ],
    }
