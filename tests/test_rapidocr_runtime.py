from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.recognition.rapidocr_artifacts import validate_rapidocr_artifact_bundle
from app.recognition.rapidocr_runtime import (
    MAX_REC_CHARACTERS,
    RapidOcrRuntimeError,
    construct_local_rapidocr_engine,
    rapidocr_engine_factory,
)


class FakeEngineType(Enum):
    ONNXRUNTIME = "onnxruntime"


def _bundle(tmp_path: Path):
    root = tmp_path / "bundle"
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True)
    artifacts: dict[str, object] = {}
    for role in ("det", "cls", "rec"):
        content = f"local-{role}".encode()
        path = artifacts_dir / f"{role}.onnx"
        path.write_bytes(content)
        artifacts[role] = {
            "path": f"artifacts/{role}.onnx",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    manifest = {
        "schema_version": "ahamark-rapidocr-artifacts-v2",
        "bundle_id": str(uuid.uuid4()),
        "runtime": {"rapidocr_version": "3.9.2", "onnxruntime_version": "1.27.0"},
        "license": {"locally_approved": True, "approval_id": str(uuid.uuid4())},
        "artifacts": artifacts,
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (root / "manifest.json").write_bytes(raw)
    return validate_rapidocr_artifact_bundle(
        root, expected_manifest_sha256=hashlib.sha256(raw).hexdigest()
    )


def _versions(name: str) -> str:
    return {"rapidocr": "3.9.2", "onnxruntime": "1.27.0"}[name]


def _assert_code(exc_info: pytest.ExceptionInfo[RapidOcrRuntimeError], code: str) -> None:
    assert exc_info.value.code == code


def test_factory_is_lazy_and_constructs_with_enum_and_cpu_metadata_session(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    events: list[object] = []
    engine = object()

    class FakeRapidOCR:
        def __new__(cls, **kwargs: Any) -> object:
            events.append(("construct", kwargs))
            return engine

    def module_loader(name: str) -> object:
        events.append(("import", name))
        if name == "rapidocr":
            return SimpleNamespace(RapidOCR=FakeRapidOCR, EngineType=FakeEngineType)
        return SimpleNamespace(InferenceSession=object)

    def session_factory(path: str, *, providers: list[str]):
        events.append(("session", path, providers))
        metadata = SimpleNamespace(custom_metadata_map={"character": "a\nb\n中"})
        return SimpleNamespace(get_modelmeta=lambda: metadata)

    factory = rapidocr_engine_factory(
        bundle,
        module_loader=module_loader,
        version_reader=_versions,
        session_factory=session_factory,
    )
    assert events == []

    assert factory() is engine
    assert events[:2] == [("import", "rapidocr"), ("import", "onnxruntime")]
    assert events[2] == (
        "session",
        str(bundle.rec.path),
        ["CPUExecutionProvider"],
    )
    params = events[3][1]
    assert params["params"]["Det.engine_type"] is FakeEngineType.ONNXRUNTIME
    assert params["params"]["Global.font_path"] is None
    assert params["params"]["Rec.rec_keys_path"] is None


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"character": ""},
        {"character": "only-one"},
        {"character": "a\n\nb"},
        {"character": "x\n" * (MAX_REC_CHARACTERS + 1)},
    ],
)
def test_rejects_invalid_character_metadata_without_constructing(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    bundle = _bundle(tmp_path)
    constructed = False

    def constructor(**_: Any) -> object:
        nonlocal constructed
        constructed = True
        return object()

    modules = {
        "rapidocr": SimpleNamespace(RapidOCR=constructor, EngineType=FakeEngineType),
        "onnxruntime": SimpleNamespace(InferenceSession=object),
    }
    session = SimpleNamespace(get_modelmeta=lambda: SimpleNamespace(custom_metadata_map=metadata))

    with pytest.raises(RapidOcrRuntimeError) as exc_info:
        construct_local_rapidocr_engine(
            bundle,
            module_loader=modules.__getitem__,
            version_reader=_versions,
            session_factory=lambda *_args, **_kwargs: session,
        )

    _assert_code(exc_info, "OCR_REC_MODEL_METADATA_INVALID")
    assert constructed is False


def test_rejects_version_mismatch_before_import(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    imported = False

    def module_loader(_: str) -> object:
        nonlocal imported
        imported = True
        return object()

    with pytest.raises(RapidOcrRuntimeError) as exc_info:
        construct_local_rapidocr_engine(
            bundle,
            module_loader=module_loader,
            version_reader=lambda _: "0.0.0",
        )

    _assert_code(exc_info, "OCR_RUNTIME_VERSION_MISMATCH")
    assert imported is False


def test_hides_version_import_session_and_constructor_failures(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    def secret_failure(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("secret path hash and backend detail")

    cases = [
        ({"version_reader": secret_failure}, "OCR_RUNTIME_VERSION_UNAVAILABLE"),
        (
            {"version_reader": _versions, "module_loader": secret_failure},
            "OCR_RUNTIME_IMPORT_FAILED",
        ),
    ]
    for kwargs, code in cases:
        with pytest.raises(RapidOcrRuntimeError) as exc_info:
            construct_local_rapidocr_engine(bundle, **kwargs)
        _assert_code(exc_info, code)
        assert "secret" not in str(exc_info.value)

    modules = {
        "rapidocr": SimpleNamespace(RapidOCR=object, EngineType=FakeEngineType),
        "onnxruntime": SimpleNamespace(InferenceSession=object),
    }
    with pytest.raises(RapidOcrRuntimeError) as exc_info:
        construct_local_rapidocr_engine(
            bundle,
            module_loader=modules.__getitem__,
            version_reader=_versions,
            session_factory=secret_failure,
        )
    _assert_code(exc_info, "OCR_REC_MODEL_METADATA_INVALID")
    assert "secret" not in str(exc_info.value)

    modules["rapidocr"] = SimpleNamespace(RapidOCR=secret_failure, EngineType=FakeEngineType)
    metadata = SimpleNamespace(custom_metadata_map={"character": "a\nb"})
    with pytest.raises(RapidOcrRuntimeError) as exc_info:
        construct_local_rapidocr_engine(
            bundle,
            module_loader=modules.__getitem__,
            version_reader=_versions,
            session_factory=lambda *_args, **_kwargs: SimpleNamespace(
                get_modelmeta=lambda: metadata
            ),
        )
    _assert_code(exc_info, "OCR_ENGINE_INIT_FAILED")
    assert "secret" not in str(exc_info.value)
