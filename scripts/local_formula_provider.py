from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
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
FORMULA_BUNDLE_SCHEMA_VERSION = "ahamark-formula-bundle-v1"
REQUIRED_MODEL_FILES = {"inference.json", "inference.pdiparams", "inference.yml"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FormulaBundle:
    root: Path
    identities: dict[str, tuple[int, int, int]]


class FormulaModel(Protocol):
    def predict(self, image: Any, *, batch_size: int) -> Any: ...


class PaddleFormulaModel:
    def __init__(self, model_dir: Path) -> None:
        from paddleocr import FormulaRecognition  # type: ignore[import-not-found]

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
    if not path.is_dir() or not REQUIRED_MODEL_FILES.issubset(
        item.name for item in path.iterdir()
    ):
        raise RuntimeError("AHAMARK_FORMULA_MODEL_DIR does not contain a complete model")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def validate_formula_bundle() -> FormulaBundle:
    root = _model_dir()
    manifest_path = root / "manifest.json"
    expected_manifest_sha = os.environ.get("AHAMARK_FORMULA_MANIFEST_SHA256", "").lower()
    if not _SHA256.fullmatch(expected_manifest_sha):
        raise RuntimeError("AHAMARK_FORMULA_MANIFEST_SHA256 is required")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("formula model manifest is missing or unsafe")
    if _sha256(manifest_path) != expected_manifest_sha:
        raise RuntimeError("formula model manifest hash mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("formula model manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != FORMULA_BUNDLE_SCHEMA_VERSION
    ):
        raise RuntimeError("formula model manifest schema is invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(REQUIRED_MODEL_FILES):
        raise RuntimeError("formula model manifest file list is invalid")
    identities: dict[str, tuple[int, int, int]] = {}
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError("formula model manifest entry is invalid")
        name, size, expected_sha = item.get("path"), item.get("size"), item.get("sha256")
        if name not in REQUIRED_MODEL_FILES or name in seen:
            raise RuntimeError("formula model manifest path is invalid")
        if not isinstance(size, int) or size <= 0 or not isinstance(expected_sha, str):
            raise RuntimeError("formula model manifest identity is invalid")
        if not _SHA256.fullmatch(expected_sha):
            raise RuntimeError("formula model manifest hash is invalid")
        seen.add(name)
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("formula model file is missing or unsafe")
        stat = path.stat()
        if stat.st_size != size or _sha256(path) != expected_sha:
            raise RuntimeError("formula model file identity mismatch")
        identities[name] = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    if seen != REQUIRED_MODEL_FILES:
        raise RuntimeError("formula model manifest is incomplete")
    return FormulaBundle(root, identities)


def verify_formula_bundle_identity(bundle: FormulaBundle) -> None:
    for name, expected in bundle.identities.items():
        path = bundle.root / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("formula model file identity changed")
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != expected:
            raise RuntimeError("formula model file identity changed")


@lru_cache(maxsize=1)
def get_model() -> FormulaModel:
    bundle = validate_formula_bundle()
    return PaddleFormulaModel(bundle.root)


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
    verify_formula_bundle_identity(validate_formula_bundle())
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
        with Image.open(io.BytesIO(content)) as source:
            source.load()
            image = source.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail="invalid formula crop") from exc
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=413, detail="formula crop exceeds the pixel limit")

    try:
        verify_formula_bundle_identity(validate_formula_bundle())
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
