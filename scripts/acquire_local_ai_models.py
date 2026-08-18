from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

FORMULA_REPOSITORY = "PaddlePaddle/PP-FormulaNet_plus-M"
FORMULA_REVISION = "712e6e2e4c313b1ea163be5c350127b82662c58d"
LLM_REPOSITORY = "Qwen/Qwen3-4B-GGUF"
LLM_REVISION = "main"
FORMULA_BUNDLE_SCHEMA_VERSION = "ahamark-formula-bundle-v1"
CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class Asset:
    repository: str
    revision: str
    path: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.path}"


FORMULA_ASSETS = (
    Asset(
        FORMULA_REPOSITORY,
        FORMULA_REVISION,
        "inference.json",
        1_130_158,
        "8333a7f650766a748e273c550d278601dd19dfeee1c4b01038ff632f134d9884",
    ),
    Asset(
        FORMULA_REPOSITORY,
        FORMULA_REVISION,
        "inference.pdiparams",
        617_064_962,
        "f16ef9b5c8227da70d3ec969a5195f4d62c1154427b883f4d6cff07633654041",
    ),
    Asset(
        FORMULA_REPOSITORY,
        FORMULA_REVISION,
        "inference.yml",
        2_244_564,
        "87b5f3d7f2b2fe553627d77b37f496608ca150ebd0ef62d362591edca47b5538",
    ),
)
LLM_ASSET = Asset(
    LLM_REPOSITORY,
    LLM_REVISION,
    "Qwen3-4B-Q4_K_M.gguf",
    2_497_280_256,
    "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_empty_directory(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise RuntimeError("destination cannot be a filesystem root")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise RuntimeError("destination must be a new or empty directory")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def download(asset: Asset, destination: Path) -> None:
    output = destination / asset.path
    partial = destination / f"{asset.path}.part"
    if output.exists() or partial.exists():
        raise RuntimeError(f"refusing to overwrite staged file: {asset.path}")
    request = urllib.request.Request(
        asset.url,
        headers={"User-Agent": "AhaMark-local-model-acquisition/1"},
    )
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as handle:
        final_url = response.geturl()
        if not final_url.startswith("https://"):
            raise RuntimeError(f"insecure model redirect rejected: {asset.path}")
        while chunk := response.read(CHUNK_SIZE):
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    if size != asset.size or digest.hexdigest() != asset.sha256:
        raise RuntimeError(f"model identity mismatch: {asset.path}")
    partial.replace(output)
    print(f"VERIFIED {asset.path} bytes={size} sha256={asset.sha256}")


def write_formula_manifest(destination: Path) -> str:
    manifest = {
        "schema_version": FORMULA_BUNDLE_SCHEMA_VERSION,
        "source": {
            "repository": FORMULA_REPOSITORY,
            "revision": FORMULA_REVISION,
            "license": "Apache-2.0",
        },
        "files": [
            {"path": asset.path, "size": asset.size, "sha256": asset.sha256}
            for asset in FORMULA_ASSETS
        ],
    }
    path = destination / "manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )
    manifest_sha = sha256(path)
    print(f"FORMULA_MANIFEST_SHA256={manifest_sha}")
    return manifest_sha


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire fixed local-AI model assets with fail-closed identity checks."
    )
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--asset", choices=("formula", "llm", "all"), default="all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    destination = ensure_empty_directory(args.destination)
    if args.asset in {"formula", "all"}:
        formula_dir = destination / "PP-FormulaNet_plus-M"
        formula_dir.mkdir()
        for asset in FORMULA_ASSETS:
            download(asset, formula_dir)
        write_formula_manifest(formula_dir)
    if args.asset in {"llm", "all"}:
        llm_dir = destination / "qwen3-4b"
        llm_dir.mkdir()
        download(LLM_ASSET, llm_dir)
    print("LOCAL_AI_MODEL_ACQUISITION=passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        print(f"LOCAL_AI_MODEL_ACQUISITION=failed reason={exc}", file=sys.stderr)
        raise SystemExit(1) from exc
