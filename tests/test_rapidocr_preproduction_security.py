from __future__ import annotations

import ast
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from app.core.config import Settings
from app.recognition import answer_providers
from app.recognition.answer_providers import UnavailableAnswerProvider
from app.recognition.pipeline import RapidOcrProvider
from app.recognition.rapidocr_artifacts import (
    RapidOcrArtifactError,
    validate_rapidocr_artifact_bundle,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROLES = ("det", "cls", "rec", "keys", "font")


def _write_bundle(root: Path) -> str:
    artifacts: dict[str, object] = {}
    for role in ARTIFACT_ROLES:
        suffix = ".txt" if role == "keys" else ".ttf" if role == "font" else ".onnx"
        relative_path = f"models/{role}{suffix}"
        content = f"offline-{role}".encode()
        artifact_path = root / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(content)
        artifacts[role] = {
            "path": relative_path,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    manifest = {
        "schema_version": "ahamark-rapidocr-artifacts-v1",
        "bundle_id": str(uuid.uuid4()),
        "runtime": {"rapidocr_version": "3.4.2", "onnxruntime_version": "1.20.1"},
        "license": {"locally_approved": True, "approval_id": str(uuid.uuid4())},
        "artifacts": artifacts,
    }
    raw_manifest = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (root / "manifest.json").write_bytes(raw_manifest)
    return hashlib.sha256(raw_manifest).hexdigest()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "https://models.example/det.onnx",
        "file:///private/det.onnx",
        "C:/private/det.onnx",
        r"models\det.onnx",
    ],
)
def test_artifact_manifest_rejects_urls_and_platform_paths(
    tmp_path: Path, unsafe_path: str
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    _write_bundle(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["det"]["path"] = unsafe_path
    raw_manifest = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_path.write_bytes(raw_manifest)
    expected_hash = hashlib.sha256(raw_manifest).hexdigest()

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root.resolve(), expected_manifest_sha256=expected_hash)

    assert exc_info.value.code == "OCR_ARTIFACT_PATH_INVALID"
    assert "models.example" not in str(exc_info.value)


def test_unreadable_manifest_is_a_stable_non_sensitive_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    expected_hash = _write_bundle(root)
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path.name == "manifest.json":
            raise PermissionError(r"C:\private-school\manifest.json")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root.resolve(), expected_manifest_sha256=expected_hash)

    assert exc_info.value.code == "OCR_ARTIFACT_MANIFEST_UNREADABLE"
    assert "private-school" not in str(exc_info.value)


def test_unreadable_artifact_is_a_stable_non_sensitive_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    expected_hash = _write_bundle(root)
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path.name == "det.onnx":
            raise PermissionError(r"C:\private-school\det.onnx")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root.resolve(), expected_manifest_sha256=expected_hash)

    assert exc_info.value.code == "OCR_ARTIFACT_UNREADABLE"
    assert "private-school" not in str(exc_info.value)


def test_default_api_image_does_not_install_optional_ocr_runtime() -> None:
    dockerfile = (REPOSITORY_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")

    assert 'pip install --no-cache-dir "."' in dockerfile
    assert ".[ocr]" not in dockerfile
    assert "rapidocr" not in dockerfile.lower()
    assert "onnxruntime" not in dockerfile.lower()


def test_artifact_validation_and_adapter_have_no_runtime_or_download_imports() -> None:
    forbidden_roots = {
        "httpx",
        "importlib",
        "onnxruntime",
        "rapidocr",
        "requests",
        "subprocess",
        "urllib",
    }
    modules = (
        REPOSITORY_ROOT / "apps" / "api" / "app" / "recognition" / "rapidocr_artifacts.py",
        REPOSITORY_ROOT / "apps" / "api" / "app" / "recognition" / "rapidocr_adapter.py",
    )

    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        dynamic_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
        ]

        assert imported_roots.isdisjoint(forbidden_roots), module.name
        assert dynamic_imports == [], module.name


def test_answer_rapidocr_route_cannot_construct_or_import_behind_closed_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_init(self: RapidOcrProvider, *args: object, **kwargs: object) -> None:
        del self, args, kwargs
        raise AssertionError("closed answer route attempted to construct RapidOCR")

    monkeypatch.setattr(RapidOcrProvider, "__init__", forbidden_init)
    settings = Settings(
        app_env="test",
        recognition_provider="unavailable",
        answer_recognition_provider="rapidocr",
    )

    provider = answer_providers.provider_from_settings(settings)

    assert isinstance(provider, UnavailableAnswerProvider)


def test_answer_provider_source_has_no_zero_argument_rapidocr_construction() -> None:
    source_path = REPOSITORY_ROOT / "apps" / "api" / "app" / "recognition" / "answer_providers.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    zero_argument_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RapidOcrProvider"
        and not node.args
        and not node.keywords
    ]

    assert zero_argument_calls == []
