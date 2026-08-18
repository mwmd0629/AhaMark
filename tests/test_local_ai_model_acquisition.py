import hashlib
import io
import json
from pathlib import Path

import pytest

from scripts.acquire_local_ai_models import (
    Asset,
    download,
    ensure_empty_directory,
    write_formula_manifest,
)


class DownloadResponse(io.BytesIO):
    def geturl(self) -> str:
        return "https://cdn-lfs.huggingface.co/synthetic"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_download_verifies_identity_before_publishing(monkeypatch, tmp_path: Path) -> None:
    content = b"synthetic-model"
    asset = Asset(
        repository="official/model",
        revision="fixed-revision",
        path="model.bin",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: DownloadResponse(content),
    )

    download(asset, tmp_path)

    assert (tmp_path / "model.bin").read_bytes() == content
    assert not (tmp_path / "model.bin.part").exists()
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        download(asset, tmp_path)


def test_formula_manifest_is_deterministic_and_runtime_compatible(tmp_path: Path) -> None:
    first = write_formula_manifest(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    second = write_formula_manifest(tmp_path)

    assert first == second
    assert manifest["schema_version"] == "ahamark-formula-bundle-v1"
    assert manifest["source"]["revision"] == "712e6e2e4c313b1ea163be5c350127b82662c58d"
    assert {item["path"] for item in manifest["files"]} == {
        "inference.json",
        "inference.pdiparams",
        "inference.yml",
    }


def test_acquisition_refuses_nonempty_or_root_destination(tmp_path: Path) -> None:
    (tmp_path / "existing").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(RuntimeError, match="new or empty"):
        ensure_empty_directory(tmp_path)
    with pytest.raises(RuntimeError, match="filesystem root"):
        ensure_empty_directory(Path(tmp_path.anchor))
