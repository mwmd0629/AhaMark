from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from app.core.config import Settings
from app.recognition.pipeline import (
    RapidOcrProvider,
    UnavailableProvider,
    _validated_rapidocr_bundle,
    provider_from_settings,
    safe_provider_readiness,
)


def _bundle(root: Path) -> str:
    artifacts: dict[str, object] = {}
    artifact_root = root / "artifacts"
    artifact_root.mkdir(parents=True)
    for role in ("det", "cls", "rec"):
        content = f"approved-{role}".encode()
        (artifact_root / f"{role}.onnx").write_bytes(content)
        artifacts[role] = {
            "path": f"artifacts/{role}.onnx",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    manifest = {
        "schema_version": "ahamark-rapidocr-artifacts-v2",
        "bundle_id": str(uuid.uuid4()),
        "runtime": {"rapidocr_version": "3.9.2", "onnxruntime_version": "1.28.0"},
        "license": {"locally_approved": True, "approval_id": str(uuid.uuid4())},
        "artifacts": artifacts,
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (root / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_complete_fixed_bundle_configuration_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest_sha256 = _bundle(root)
    settings = Settings(
        _env_file=None,
        recognition_provider="rapidocr",
        recognition_rapidocr_runtime_enabled=True,
        recognition_rapidocr_artifact_root=str(root.resolve()),
        recognition_rapidocr_manifest_sha256=manifest_sha256,
    )

    provider = provider_from_settings(settings)

    assert isinstance(provider, RapidOcrProvider)
    assert provider.version == "rapidocr-3.9.2/onnxruntime-1.28.0"
    assert safe_provider_readiness(provider)[0] is True


def test_bundle_tampering_fails_closed_after_cached_validation(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest_sha256 = _bundle(root)
    settings = Settings(
        _env_file=None,
        recognition_provider="rapidocr",
        recognition_rapidocr_runtime_enabled=True,
        recognition_rapidocr_artifact_root=str(root.resolve()),
        recognition_rapidocr_manifest_sha256=manifest_sha256,
    )
    provider = provider_from_settings(settings)
    assert isinstance(provider, RapidOcrProvider)

    (root / "artifacts" / "det.onnx").write_bytes(b"tampered")

    assert safe_provider_readiness(provider) == (
        False,
        "RapidOCR 本地运行材料未通过完整性校验",
    )
    second = provider_from_settings(settings)
    assert isinstance(second, UnavailableProvider)
    assert second.available() == (False, "RapidOCR 本地运行材料未通过完整性校验")
    _validated_rapidocr_bundle.cache_clear()


def test_node2_ocr_image_is_pinned_and_default_image_remains_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    default_dockerfile = (root / "apps/api/Dockerfile").read_text(encoding="utf-8")
    ocr_dockerfile = (root / "apps/api/Dockerfile.rapidocr").read_text(encoding="utf-8")
    compose = (root / "docker-compose.node2.yml").read_text(encoding="utf-8")
    manifest_path = root / "deploy/rapidocr/manifest.json"
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    assert 'pip install --no-cache-dir "."' in default_dockerfile
    assert ".[ocr]" not in default_dockerfile
    assert 'pip install --no-cache-dir ".[ocr]"' in ocr_dockerfile
    assert "RAPIDOCR_MANIFEST_SHA256" in ocr_dockerfile
    assert "RECOGNITION_RAPIDOCR_MODEL_DOWNLOAD_ALLOWED: \"false\"" in compose
    assert "RECOGNITION_RAPIDOCR_RUNTIME_ENABLED:-false" in compose
    assert "RECOGNITION_RAPIDOCR_MANIFEST_SHA256:-" in compose
    assert manifest_hash == "f84336fc78cb51cd0ee223ee3c04158eb2f968af6fa8ffd31051b821f843ff5b"
