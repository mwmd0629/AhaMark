from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings
from app.recognition import answer_providers
from app.recognition.answer_providers import RapidOcrAnswerProvider, UnavailableAnswerProvider
from app.recognition.pipeline import RapidOcrProvider
from app.recognition.rapidocr_adapter import (
    RapidOcrAdapterError,
    construct_rapidocr_engine,
    rapidocr_v3_params,
)
from app.recognition.rapidocr_artifacts import (
    MAX_TOTAL_ARTIFACT_BYTES,
    RapidOcrArtifactError,
    ValidatedRapidOcrBundle,
    validate_rapidocr_artifact_bundle,
)

ROLES = ("det", "cls", "rec", "keys", "font")


def _manifest(root: Path) -> dict[str, object]:
    artifacts: dict[str, object] = {}
    for role in ROLES:
        suffix = ".txt" if role == "keys" else ".ttf" if role == "font" else ".onnx"
        path = root / "artifacts" / f"{role}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"local-{role}-artifact".encode()
        path.write_bytes(content)
        artifacts[role] = {
            "path": f"artifacts/{role}{suffix}",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return {
        "schema_version": "ahamark-rapidocr-artifacts-v1",
        "bundle_id": str(uuid.uuid4()),
        "runtime": {"rapidocr_version": "3.4.2", "onnxruntime_version": "1.20.1"},
        "license": {"locally_approved": True, "approval_id": str(uuid.uuid4())},
        "artifacts": artifacts,
    }


def _write_manifest(root: Path, manifest: dict[str, object]) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (root / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _bundle(tmp_path: Path) -> tuple[Path, dict[str, object], ValidatedRapidOcrBundle]:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest = _manifest(root)
    expected_hash = _write_manifest(root, manifest)
    return (
        root,
        manifest,
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash),
    )


def _assert_code(exc_info: pytest.ExceptionInfo[RapidOcrArtifactError], code: str) -> None:
    assert exc_info.value.code == code


def test_validates_fixed_local_artifact_roles_and_hashes(tmp_path: Path) -> None:
    root, _, bundle = _bundle(tmp_path)

    assert bundle.root == root.resolve()
    assert {
        bundle.det.role,
        bundle.cls.role,
        bundle.rec.role,
        bundle.keys.role,
        bundle.font.role,
    } == set(ROLES)
    assert all(
        artifact.path.is_absolute()
        for artifact in (bundle.det, bundle.cls, bundle.rec, bundle.keys, bundle.font)
    )
    assert bundle.det.st_size == bundle.det.size_bytes
    assert bundle.det.st_ino == bundle.det.path.lstat().st_ino


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (
            b'{"schema_version":"ahamark-rapidocr-artifacts-v1","schema_version":"x"}',
            "OCR_ARTIFACT_MANIFEST_INVALID",
        ),
        (b'{"value":NaN}', "OCR_ARTIFACT_MANIFEST_INVALID"),
    ],
)
def test_rejects_duplicate_fields_and_non_finite_json(
    tmp_path: Path, raw: bytes, code: str
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "manifest.json").write_bytes(raw)

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(
            root, expected_manifest_sha256=hashlib.sha256(raw).hexdigest()
        )

    _assert_code(exc_info, code)


def test_rejects_manifest_hash_size_and_artifact_hash_mismatches(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest = _manifest(root)
    expected_hash = _write_manifest(root, manifest)

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256="0" * 64)
    _assert_code(exc_info, "OCR_ARTIFACT_MANIFEST_HASH_MISMATCH")

    artifact = root / "artifacts" / "det.onnx"
    artifact.write_bytes(b"changed")
    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash)
    _assert_code(exc_info, "OCR_ARTIFACT_SIZE_MISMATCH")

    det = manifest["artifacts"]
    assert isinstance(det, dict)
    det_item = det["det"]
    assert isinstance(det_item, dict)
    det_item["size_bytes"] = len(b"changed")
    changed_hash = _write_manifest(root, manifest)
    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=changed_hash)
    _assert_code(exc_info, "OCR_ARTIFACT_HASH_MISMATCH")


def test_rejects_declared_total_size_and_duplicate_paths_before_reading_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest = _manifest(root)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict)
    for role in ("det", "cls", "rec"):
        item = artifacts[role]
        assert isinstance(item, dict)
        item["size_bytes"] = MAX_TOTAL_ARTIFACT_BYTES // 2
    expected_hash = _write_manifest(root, manifest)

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash)
    _assert_code(exc_info, "OCR_ARTIFACT_SIZE_INVALID")

    manifest = _manifest(root)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict)
    det = artifacts["det"]
    cls = artifacts["cls"]
    assert isinstance(det, dict) and isinstance(cls, dict)
    cls["path"] = det["path"]
    expected_hash = _write_manifest(root, manifest)

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash)
    _assert_code(exc_info, "OCR_ARTIFACT_PATH_INVALID")


def test_rejects_escape_links_extra_files_and_unapproved_license(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest = _manifest(root)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict)
    det = artifacts["det"]
    assert isinstance(det, dict)
    outside = tmp_path / "outside.onnx"
    outside.write_bytes(b"outside")
    det.update(
        path="../outside.onnx",
        size_bytes=len(b"outside"),
        sha256=hashlib.sha256(b"outside").hexdigest(),
    )
    expected_hash = _write_manifest(root, manifest)
    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash)
    _assert_code(exc_info, "OCR_ARTIFACT_PATH_INVALID")

    det.update(
        path="artifacts/det.onnx",
        size_bytes=len(b"local-det-artifact"),
        sha256=hashlib.sha256(b"local-det-artifact").hexdigest(),
    )
    (root / "undeclared.bin").write_bytes(b"extra")
    expected_hash = _write_manifest(root, manifest)
    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash)
    _assert_code(exc_info, "OCR_ARTIFACT_EXTRA_FILE")
    (root / "undeclared.bin").unlink()

    license_data = manifest["license"]
    assert isinstance(license_data, dict)
    license_data["locally_approved"] = False
    expected_hash = _write_manifest(root, manifest)
    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash)
    _assert_code(exc_info, "OCR_ARTIFACT_LICENSE_UNAPPROVED")


def test_rejects_symlinked_artifact_when_platform_allows_it(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest = _manifest(root)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict)
    det = artifacts["det"]
    assert isinstance(det, dict)
    target = root / "artifacts" / "det-target.onnx"
    target.write_bytes(b"linked")
    link = root / "artifacts" / "det-link.onnx"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")
    det.update(
        path="artifacts/det-link.onnx",
        size_bytes=len(b"linked"),
        sha256=hashlib.sha256(b"linked").hexdigest(),
    )
    expected_hash = _write_manifest(root, manifest)

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash)

    _assert_code(exc_info, "OCR_ARTIFACT_SYMLINK_FORBIDDEN")


@pytest.mark.parametrize("target_name", ["manifest.json", "artifacts/det.onnx"])
def test_rejects_hardlinked_manifest_and_artifact(tmp_path: Path, target_name: str) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest = _manifest(root)
    expected_hash = _write_manifest(root, manifest)
    target = root / target_name
    alias = tmp_path / f"alias-{target.name}"
    try:
        os.link(target, alias)
    except OSError:
        pytest.skip("hardlink creation is not supported on this platform")

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        validate_rapidocr_artifact_bundle(root, expected_manifest_sha256=expected_hash)

    _assert_code(exc_info, "OCR_ARTIFACT_HARDLINK_FORBIDDEN")


def test_adapter_uses_only_explicit_v3_paths_and_injected_constructor(tmp_path: Path) -> None:
    _, _, bundle = _bundle(tmp_path)
    calls: list[dict[str, Any]] = []
    engine = object()

    def constructor(**kwargs: Any) -> object:
        calls.append(kwargs)
        return engine

    assert construct_rapidocr_engine(bundle, constructor=constructor) is engine
    assert calls == [{"params": rapidocr_v3_params(bundle)}]
    params = calls[0]["params"]
    assert params == {
        "Global.use_det": True,
        "Global.use_cls": True,
        "Global.use_rec": True,
        "Global.model_root_dir": str(bundle.root),
        "Global.font_path": str(bundle.font.path),
        "Det.engine_type": "onnxruntime",
        "Det.model_path": str(bundle.det.path),
        "Cls.engine_type": "onnxruntime",
        "Cls.model_path": str(bundle.cls.path),
        "Rec.engine_type": "onnxruntime",
        "Rec.model_path": str(bundle.rec.path),
        "Rec.rec_keys_path": str(bundle.keys.path),
    }


def test_adapter_hides_constructor_failure(tmp_path: Path) -> None:
    _, _, bundle = _bundle(tmp_path)

    def constructor(**_: Any) -> object:
        raise RuntimeError("secret local path and runtime detail")

    with pytest.raises(RapidOcrAdapterError) as exc_info:
        construct_rapidocr_engine(bundle, constructor=constructor)

    assert exc_info.value.code == "OCR_ENGINE_INIT_FAILED"
    assert "secret" not in str(exc_info.value)


def test_adapter_rejects_artifact_changed_after_validation(tmp_path: Path) -> None:
    _, _, bundle = _bundle(tmp_path)
    bundle.det.path.write_bytes(b"changed-after-validation")
    called = False

    def constructor(**_: Any) -> object:
        nonlocal called
        called = True
        return object()

    with pytest.raises(RapidOcrArtifactError) as exc_info:
        construct_rapidocr_engine(bundle, constructor=constructor)

    _assert_code(exc_info, "OCR_ARTIFACT_CHANGED")
    assert called is False


def test_answer_rapidocr_reuses_available_main_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RapidOcrProvider(engine_factory=lambda: object(), version="injected-test")
    monkeypatch.setattr(answer_providers, "recognition_provider_from_settings", lambda _: provider)
    settings = Settings(app_env="test", answer_recognition_provider="rapidocr")

    answer_provider = answer_providers.provider_from_settings(settings)

    assert isinstance(answer_provider, RapidOcrAnswerProvider)
    assert answer_provider.provider is provider


def test_answer_rapidocr_cannot_bypass_main_runtime_gate() -> None:
    settings = Settings(app_env="test", answer_recognition_provider="rapidocr")

    assert isinstance(answer_providers.provider_from_settings(settings), UnavailableAnswerProvider)
