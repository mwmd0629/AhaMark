from __future__ import annotations

import os
from pathlib import Path

from app.core.config import Settings
from app.recognition.formula import FormulaRegionArtifact, HttpFormulaProvider
from app.recognition.pipeline import PageArtifact


def main() -> None:
    token = os.environ["AHAMARK_FORMULA_PROVIDER_TOKEN"]
    image_path = Path(os.environ["AHAMARK_FORMULA_SMOKE_IMAGE"])
    base_url = os.environ.get("AHAMARK_FORMULA_PROVIDER_BASE_URL", "http://127.0.0.1:8765")
    if len(token) < 32:
        raise RuntimeError("AHAMARK_FORMULA_PROVIDER_TOKEN must contain at least 32 characters")
    content = image_path.read_bytes()
    artifact = FormulaRegionArtifact(
        PageArtifact(content, 0, 0),
        (0.0, 0.0, 1.0, 1.0),
        "display",
    )
    provider = HttpFormulaProvider(
        Settings(
            app_env="development",
            formula_recognition_provider="http",
            formula_recognition_base_url=base_url,
            formula_recognition_api_key=token,
            formula_recognition_allowed_hosts=["127.0.0.1"],
            formula_recognition_timeout_seconds=120,
        )
    )
    candidates = provider.recognize(artifact)
    if len(candidates) != 1 or not candidates[0].latex:
        raise RuntimeError("local formula provider did not return exactly one candidate")
    candidate = candidates[0]
    print(f"provider={candidate.provider}")
    print(f"provider_version={candidate.provider_version}")
    print(f"confidence={candidate.confidence}")
    print(f"warnings={','.join(candidate.warning_codes)}")
    print(f"latex={candidate.latex}")


if __name__ == "__main__":
    main()
