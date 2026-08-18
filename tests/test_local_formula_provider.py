from __future__ import annotations

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scripts import local_formula_provider


class _SyntheticModel:
    def predict(self, image: object, *, batch_size: int) -> list[dict[str, object]]:
        assert batch_size == 1
        assert isinstance(image, np.ndarray)
        assert image.shape == (32, 80, 3)
        return [{"res": {"rec_formula": r"\frac{1}{x^2}"}}]


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 32), "white").save(output, "PNG")
    return output.getvalue()


def _allow_synthetic_bundle(monkeypatch) -> None:
    synthetic_bundle = object()
    monkeypatch.setattr(
        local_formula_provider, "validate_formula_bundle", lambda: synthetic_bundle
    )
    monkeypatch.setattr(
        local_formula_provider, "verify_formula_bundle_identity", lambda _bundle: None
    )


def test_local_formula_provider_returns_one_uncalibrated_candidate(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AHAMARK_FORMULA_PROVIDER_TOKEN", "synthetic-local-token-with-32-characters")
    monkeypatch.setattr(local_formula_provider, "get_model", lambda: _SyntheticModel())
    _allow_synthetic_bundle(monkeypatch)

    response = TestClient(local_formula_provider.app).post(
        "/v1/formulas/recognize",
        headers={"Authorization": "Bearer synthetic-local-token-with-32-characters"},
        data={"region_kind": "display"},
        files={"file": ("formula.png", _png(), "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["candidates"] == [
        {
            "latex": r"\frac{1}{x^2}",
            "confidence": None,
            "warning_codes": ["UNCALIBRATED_CONFIDENCE", "TEACHER_REVIEW_REQUIRED"],
        }
    ]


def test_local_formula_provider_ready_loads_model(monkeypatch) -> None:
    monkeypatch.setenv("AHAMARK_FORMULA_PROVIDER_TOKEN", "synthetic-local-token-with-32-characters")
    monkeypatch.setattr(local_formula_provider, "get_model", lambda: _SyntheticModel())
    _allow_synthetic_bundle(monkeypatch)

    response = TestClient(local_formula_provider.app).get(
        "/ready",
        headers={"Authorization": "Bearer synthetic-local-token-with-32-characters"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "provider": local_formula_provider.PROVIDER_NAME,
        "provider_version": local_formula_provider.PROVIDER_VERSION,
    }


def test_local_formula_provider_ready_rejects_weak_runtime_token(monkeypatch) -> None:
    monkeypatch.setenv("AHAMARK_FORMULA_PROVIDER_TOKEN", "too-short")

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        TestClient(local_formula_provider.app).get(
            "/ready",
            headers={"Authorization": "Bearer too-short"},
        )


def test_local_formula_provider_ready_rejects_bad_token(monkeypatch) -> None:
    monkeypatch.setenv("AHAMARK_FORMULA_PROVIDER_TOKEN", "synthetic-local-token-with-32-characters")

    response = TestClient(local_formula_provider.app).get(
        "/ready",
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 401


def test_local_formula_provider_rejects_bad_token(monkeypatch) -> None:
    monkeypatch.setenv("AHAMARK_FORMULA_PROVIDER_TOKEN", "synthetic-local-token-with-32-characters")

    response = TestClient(local_formula_provider.app).post(
        "/v1/formulas/recognize",
        headers={"Authorization": "Bearer wrong"},
        data={"region_kind": "display"},
        files={"file": ("formula.png", _png(), "image/png")},
    )

    assert response.status_code == 401


def test_local_formula_provider_rejects_non_png(monkeypatch) -> None:
    monkeypatch.setenv("AHAMARK_FORMULA_PROVIDER_TOKEN", "synthetic-local-token-with-32-characters")

    response = TestClient(local_formula_provider.app).post(
        "/v1/formulas/recognize",
        headers={"Authorization": "Bearer synthetic-local-token-with-32-characters"},
        data={"region_kind": "display"},
        files={"file": ("formula.jpg", b"not-an-image", "image/jpeg")},
    )

    assert response.status_code == 415
