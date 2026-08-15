"""Strict validation for explicitly provisioned local RapidOCR artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

MANIFEST_SCHEMA_VERSION = "ahamark-rapidocr-artifacts-v1"
MANIFEST_FILENAME = "manifest.json"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_DICTIONARY_BYTES = 20 * 1024 * 1024
MAX_TOTAL_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?")
_ARTIFACT_ROLES = {"det", "cls", "rec", "keys", "font"}


class RapidOcrArtifactError(ValueError):
    """Stable, non-sensitive failure raised before any OCR runtime is imported."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedArtifact:
    role: str
    path: Path
    size_bytes: int
    sha256: str
    st_dev: int
    st_ino: int
    st_size: int
    st_mtime_ns: int


@dataclass(frozen=True)
class _ArtifactDeclaration:
    role: str
    path_parts: tuple[str, ...]
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ValidatedRapidOcrBundle:
    root: Path
    manifest_path: Path
    manifest_sha256: str
    bundle_id: uuid.UUID
    rapidocr_version: str
    onnxruntime_version: str
    license_approval_id: uuid.UUID
    det: ValidatedArtifact
    cls: ValidatedArtifact
    rec: ValidatedArtifact
    keys: ValidatedArtifact
    font: ValidatedArtifact


def _fail(code: str, message: str) -> RapidOcrArtifactError:
    return RapidOcrArtifactError(code, message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "manifest contains duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "manifest contains a non-finite number")


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", f"{label} fields are invalid")
    return cast(dict[str, Any], value)


def _canonical_uuid(value: object, label: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", f"{label} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", f"{label} must be a UUID") from exc
    if value != str(parsed):
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", f"{label} must be a canonical UUID")
    return parsed


def _version(value: object, label: str) -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", f"{label} is invalid")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", f"{label} must be lowercase SHA-256")
    return value


def _metadata_is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _reject_link_or_reparse(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _fail("OCR_ARTIFACT_UNREADABLE", "artifact path cannot be inspected") from exc
    if stat.S_ISLNK(metadata.st_mode) or _metadata_is_reparse_point(metadata):
        raise _fail("OCR_ARTIFACT_SYMLINK_FORBIDDEN", "artifact paths cannot use links")


def _safe_relative_path(value: object, role: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or value != unicodedata.normalize("NFC", value):
        raise _fail("OCR_ARTIFACT_PATH_INVALID", f"{role} path is invalid")
    if "\x00" in value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise _fail("OCR_ARTIFACT_PATH_INVALID", f"{role} path is invalid")
    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise _fail("OCR_ARTIFACT_PATH_INVALID", f"{role} path is invalid")
    return parts


def _resolve_artifact(root: Path, parts: tuple[str, ...], role: str) -> Path:
    candidate = root.joinpath(*parts)
    current = root
    _reject_link_or_reparse(current)
    for part in parts:
        current = current / part
        if not current.exists():
            raise _fail("OCR_ARTIFACT_PATH_INVALID", f"{role} artifact is missing")
        _reject_link_or_reparse(current)
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise _fail("OCR_ARTIFACT_PATH_INVALID", f"{role} path escapes artifact root") from exc
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise _fail("OCR_ARTIFACT_UNREADABLE", f"{role} artifact cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise _fail("OCR_ARTIFACT_PATH_INVALID", f"{role} artifact must be a regular file")
    if metadata.st_nlink != 1:
        raise _fail("OCR_ARTIFACT_HARDLINK_FORBIDDEN", f"{role} artifact cannot be hard-linked")
    return candidate.resolve(strict=True)


def _file_sha256(path: Path, role: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise _fail("OCR_ARTIFACT_UNREADABLE", f"{role} artifact cannot be read") from exc
    return digest.hexdigest()


def _parse_artifact_declaration(role: str, value: object) -> _ArtifactDeclaration:
    data = _exact_keys(value, {"path", "size_bytes", "sha256"}, f"artifacts.{role}")
    size_bytes = data["size_bytes"]
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", f"{role} size_bytes is invalid")
    limit = MAX_DICTIONARY_BYTES if role in {"keys", "font"} else MAX_ARTIFACT_BYTES
    if size_bytes > limit:
        raise _fail("OCR_ARTIFACT_SIZE_INVALID", f"{role} declared size exceeds limit")
    expected_hash = _sha256(data["sha256"], f"artifacts.{role}.sha256")
    return _ArtifactDeclaration(
        role=role,
        path_parts=_safe_relative_path(data["path"], role),
        size_bytes=size_bytes,
        sha256=expected_hash,
    )


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _validate_artifact(root: Path, declaration: _ArtifactDeclaration) -> ValidatedArtifact:
    role = declaration.role
    path = _resolve_artifact(root, declaration.path_parts, role)
    try:
        before = path.lstat()
    except OSError as exc:
        raise _fail("OCR_ARTIFACT_UNREADABLE", f"{role} artifact cannot be read") from exc
    if before.st_nlink != 1:
        raise _fail("OCR_ARTIFACT_HARDLINK_FORBIDDEN", f"{role} artifact cannot be hard-linked")
    if before.st_size != declaration.size_bytes:
        raise _fail("OCR_ARTIFACT_SIZE_MISMATCH", f"{role} size does not match manifest")
    if _file_sha256(path, role) != declaration.sha256:
        raise _fail("OCR_ARTIFACT_HASH_MISMATCH", f"{role} hash does not match manifest")
    try:
        after = path.lstat()
    except OSError as exc:
        raise _fail("OCR_ARTIFACT_CHANGED", f"{role} artifact changed during validation") from exc
    if (
        _identity(after) != _identity(before)
        or stat.S_ISLNK(after.st_mode)
        or _metadata_is_reparse_point(after)
        or after.st_nlink != 1
    ):
        raise _fail("OCR_ARTIFACT_CHANGED", f"{role} artifact changed during validation")
    return ValidatedArtifact(
        role=role,
        path=path,
        size_bytes=declaration.size_bytes,
        sha256=declaration.sha256,
        st_dev=after.st_dev,
        st_ino=after.st_ino,
        st_size=after.st_size,
        st_mtime_ns=after.st_mtime_ns,
    )


def ensure_rapidocr_bundle_unchanged(bundle: ValidatedRapidOcrBundle) -> None:
    """Reject artifact replacement before engine construction without rehashing large files."""

    for artifact in (bundle.det, bundle.cls, bundle.rec, bundle.keys, bundle.font):
        try:
            metadata = artifact.path.lstat()
        except OSError as exc:
            raise _fail(
                "OCR_ARTIFACT_CHANGED", "validated artifact is no longer available"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or _metadata_is_reparse_point(metadata)
            or metadata.st_nlink != 1
            or _identity(metadata)
            != (artifact.st_dev, artifact.st_ino, artifact.st_size, artifact.st_mtime_ns)
        ):
            raise _fail("OCR_ARTIFACT_CHANGED", "validated artifact identity changed")


def validate_rapidocr_artifact_bundle(
    root: str | os.PathLike[str], *, expected_manifest_sha256: str
) -> ValidatedRapidOcrBundle:
    """Validate a local, immutable artifact bundle without importing an OCR runtime."""

    expected_manifest_sha256 = _sha256(expected_manifest_sha256, "expected_manifest_sha256")
    root_path = Path(root)
    if not root_path.is_absolute() or not root_path.exists() or not root_path.is_dir():
        raise _fail(
            "OCR_ARTIFACT_CONFIG_INVALID", "artifact root must be an existing absolute directory"
        )
    _reject_link_or_reparse(root_path)
    root_path = root_path.resolve(strict=True)
    manifest_path = root_path / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "artifact manifest is missing")
    _reject_link_or_reparse(manifest_path)
    try:
        manifest_metadata = manifest_path.lstat()
    except OSError as exc:
        raise _fail(
            "OCR_ARTIFACT_MANIFEST_UNREADABLE", "artifact manifest cannot be inspected"
        ) from exc
    if not stat.S_ISREG(manifest_metadata.st_mode):
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "artifact manifest must be a regular file")
    if manifest_metadata.st_nlink != 1:
        raise _fail("OCR_ARTIFACT_HARDLINK_FORBIDDEN", "artifact manifest cannot be hard-linked")
    if manifest_metadata.st_size <= 0 or manifest_metadata.st_size > MAX_MANIFEST_BYTES:
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "artifact manifest size is invalid")
    try:
        raw_manifest = manifest_path.read_bytes()
    except OSError as exc:
        raise _fail("OCR_ARTIFACT_MANIFEST_UNREADABLE", "artifact manifest cannot be read") from exc
    manifest_hash = hashlib.sha256(raw_manifest).hexdigest()
    if manifest_hash != expected_manifest_sha256:
        raise _fail("OCR_ARTIFACT_MANIFEST_HASH_MISMATCH", "artifact manifest hash is not approved")
    try:
        manifest = json.loads(
            raw_manifest.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except RapidOcrArtifactError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "artifact manifest is invalid JSON") from exc
    data = _exact_keys(
        manifest,
        {"schema_version", "bundle_id", "runtime", "license", "artifacts"},
        "manifest",
    )
    if data["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "artifact manifest schema is unsupported")
    bundle_id = _canonical_uuid(data["bundle_id"], "bundle_id")
    runtime = _exact_keys(data["runtime"], {"rapidocr_version", "onnxruntime_version"}, "runtime")
    rapidocr_version = _version(runtime["rapidocr_version"], "runtime.rapidocr_version")
    onnxruntime_version = _version(runtime["onnxruntime_version"], "runtime.onnxruntime_version")
    license_data = _exact_keys(data["license"], {"locally_approved", "approval_id"}, "license")
    if license_data["locally_approved"] is not True:
        raise _fail("OCR_ARTIFACT_LICENSE_UNAPPROVED", "artifact license is not locally approved")
    approval_id = _canonical_uuid(license_data["approval_id"], "license.approval_id")
    raw_artifacts = data["artifacts"]
    if not isinstance(raw_artifacts, dict):
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "artifacts must be an object")
    artifacts = cast(dict[str, Any], raw_artifacts)
    if set(artifacts) != _ARTIFACT_ROLES:
        raise _fail("OCR_ARTIFACT_MANIFEST_INVALID", "artifact roles are invalid")
    declarations = {
        role: _parse_artifact_declaration(role, value) for role, value in artifacts.items()
    }
    if sum(item.size_bytes for item in declarations.values()) > MAX_TOTAL_ARTIFACT_BYTES:
        raise _fail("OCR_ARTIFACT_SIZE_INVALID", "total artifact size exceeds limit")
    if len({item.path_parts for item in declarations.values()}) != len(declarations):
        raise _fail("OCR_ARTIFACT_PATH_INVALID", "artifact paths must be unique")
    validated = {
        role: _validate_artifact(root_path, declaration)
        for role, declaration in declarations.items()
    }
    declared_paths = {manifest_path.resolve(strict=True)} | {
        artifact.path for artifact in validated.values()
    }
    try:
        for candidate in root_path.rglob("*"):
            _reject_link_or_reparse(candidate)
            if candidate.is_file() and candidate.resolve(strict=True) not in declared_paths:
                raise _fail("OCR_ARTIFACT_EXTRA_FILE", "artifact root contains an undeclared file")
    except RapidOcrArtifactError:
        raise
    except OSError as exc:
        raise _fail("OCR_ARTIFACT_UNREADABLE", "artifact root cannot be inspected") from exc
    return ValidatedRapidOcrBundle(
        root=root_path,
        manifest_path=manifest_path.resolve(strict=True),
        manifest_sha256=manifest_hash,
        bundle_id=bundle_id,
        rapidocr_version=rapidocr_version,
        onnxruntime_version=onnxruntime_version,
        license_approval_id=approval_id,
        det=validated["det"],
        cls=validated["cls"],
        rec=validated["rec"],
        keys=validated["keys"],
        font=validated["font"],
    )
