import io
import json
from pathlib import Path

import httpx
import pytest
from app.api.formula_recognition import UNREADABLE_REASONS
from app.core.config import Settings, get_settings
from app.main import app
from app.recognition.formula import (
    FormulaCandidate,
    FormulaRegionArtifact,
    HttpFormulaProvider,
    UnavailableFormulaProvider,
    assess_formula_image_quality,
    crop_formula_region,
    normalize_latex,
    recognize_formula_safely,
    select_top_candidate,
    token_edit_distance,
    token_similarity,
    validate_eval_dataset,
)
from app.recognition.pipeline import PageArtifact, RecognitionError
from app.storage.dependencies import get_storage
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from test_assignments import FakeStorage, active_class, actor_and_db, create

from scripts.formula_ocr_offline_evaluate import evaluate

ROOT = Path(__file__).parents[1]
SYNTHETIC_DATASET = ROOT / "scripts/formula-ocr-evaluation-synthetic-v1.json"
client = TestClient(app)


def image_bytes(width: int = 400, height: int = 240) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, "PNG")
    return output.getvalue()


def ruled_formula_bytes() -> bytes:
    image = Image.new("RGB", (180, 52), "white")
    pixels = image.load()
    for x in range(image.width):
        pixels[x, 25] = (120, 175, 230)
    for x in range(30, 150):
        pixels[x, 18] = (20, 20, 20)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def synthetic_formula_bytes(width: int = 400, height: int = 240) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    pixels = image.load()
    baseline = height // 2
    for x in range(width // 5, width * 4 // 5):
        for y in range(max(0, baseline - 2), min(height, baseline + 2)):
            pixels[x, y] = (20, 20, 20)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def overwritten_formula_bytes() -> bytes:
    image = Image.new("RGB", (190, 53), "white")
    pixels = image.load()
    for x in range(190):
        pixels[x, 42] = (120, 175, 230)
    for x in range(20, 90):
        pixels[x, 18] = (20, 20, 20)
    for x in range(105, 141):
        for y in range(17, 39):
            pixels[x, y] = (10, 10, 10)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_latex_normalization_is_presentation_only() -> None:
    assert normalize_latex(r" \left( x + 1 \right) ") == "(x+1)"
    assert normalize_latex(r"\int_0^1 x\, dx") == normalize_latex(r"\int_0^1 x dx")
    assert normalize_latex(r"\frac{1}{x^2}") != normalize_latex(r"\frac{1}{x^3}")


def test_token_metrics_and_top_candidate_are_deterministic() -> None:
    assert token_edit_distance(r"\frac{1}{x^2}", r"\frac{1}{x^3}") == 1
    assert 0 < token_similarity(r"\frac{1}{x^2}", r"\frac{1}{x^3}") < 1
    candidates = [
        FormulaCandidate("x", 0.51, "synthetic", "1"),
        FormulaCandidate("y", 0.92, "synthetic", "1"),
    ]
    assert select_top_candidate(candidates) == candidates[1]
    assert select_top_candidate([]) is None


def test_unavailable_formula_provider_never_claims_recognition() -> None:
    provider = UnavailableFormulaProvider()
    available, reason = provider.available()
    assert not available and reason
    artifact = FormulaRegionArtifact(PageArtifact(b"png", 1, 1), (0.0, 0.0, 1.0, 1.0))
    with pytest.raises(RecognitionError) as raised:
        provider.recognize(artifact)
    assert raised.value.code == "FORMULA_PROVIDER_UNAVAILABLE"


def test_formula_crop_uses_normalized_coordinates_and_limits() -> None:
    artifact = crop_formula_region(
        PageArtifact(image_bytes(), 400, 240),
        (0.25, 0.25, 0.5, 0.5),
        max_pixels=100_000,
        max_bytes=100_000,
    )
    assert (artifact.page.width, artifact.page.height) == (200, 120)
    with pytest.raises(RecognitionError, match="像素数量"):
        crop_formula_region(
            PageArtifact(image_bytes(), 400, 240),
            (0, 0, 1, 1),
            max_pixels=10,
            max_bytes=100_000,
        )


def test_formula_quality_blocks_blank_crop_before_provider_call() -> None:
    class Provider:
        name = "must-not-run"
        version = "1"

        def available(self) -> tuple[bool, str | None]:
            return True, None

        def recognize(self, artifact: FormulaRegionArtifact) -> list[FormulaCandidate]:
            del artifact
            raise AssertionError("provider must not run for a blocked image")

    artifact = FormulaRegionArtifact(PageArtifact(image_bytes(80, 30), 80, 30), (0, 0, 1, 1))
    quality = assess_formula_image_quality(artifact)
    assert "FORMULA_CROP_BLANK_OR_TOO_FAINT" in quality.blocking_codes
    with pytest.raises(RecognitionError) as raised:
        recognize_formula_safely(Provider(), artifact)
    assert raised.value.code == "FORMULA_IMAGE_QUALITY_BLOCKED"


def test_formula_quality_blocks_severe_overwriting_without_trying_to_erase_it() -> None:
    class Provider:
        name = "must-not-run"
        version = "1"

        def available(self) -> tuple[bool, str | None]:
            return True, None

        def recognize(self, artifact: FormulaRegionArtifact) -> list[FormulaCandidate]:
            del artifact
            raise AssertionError("provider must not run for an occluded formula")

    artifact = FormulaRegionArtifact(
        PageArtifact(overwritten_formula_bytes(), 190, 53), (0, 0, 1, 1)
    )
    quality = assess_formula_image_quality(artifact)
    assert "FORMULA_SEVERE_OVERWRITING_OR_OCCLUSION" in quality.blocking_codes
    with pytest.raises(RecognitionError, match="涂改或遮挡"):
        recognize_formula_safely(Provider(), artifact)


def test_ruled_paper_warns_but_does_not_mutate_or_rerun_recognition() -> None:
    class Provider:
        name = "synthetic"
        version = "1"

        def __init__(self) -> None:
            self.calls = 0

        def available(self) -> tuple[bool, str | None]:
            return True, None

        def recognize(self, artifact: FormulaRegionArtifact) -> list[FormulaCandidate]:
            del artifact
            self.calls += 1
            return [FormulaCandidate("z_x", 0.8, self.name, self.version)]

    provider = Provider()
    artifact = FormulaRegionArtifact(PageArtifact(ruled_formula_bytes(), 180, 52), (0, 0, 1, 1))
    outcome = recognize_formula_safely(provider, artifact)
    assert provider.calls == 1
    assert outcome.used_preprocessed_variant is False
    assert outcome.preprocessing_agreed is None
    assert [candidate.latex for candidate in outcome.candidates] == ["z_x"]
    assert outcome.candidates[0].warning_codes == ("RULED_PAPER_LINE_AMBIGUOUS",)


def test_http_formula_provider_requires_allowlist_and_validates_response() -> None:
    settings = Settings(
        app_env="test",
        formula_recognition_provider="http",
        formula_recognition_base_url="http://formula.internal:8010",
        formula_recognition_api_key=SecretStr("synthetic-token"),
        formula_recognition_allowed_hosts=["formula.internal"],
        formula_recognition_allow_local_http=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/formulas/recognize"
        assert request.headers["authorization"] == "Bearer synthetic-token"
        return httpx.Response(
            200,
            json={
                "provider": "synthetic-service",
                "provider_version": "model-v1",
                "candidates": [{"latex": "x^2", "confidence": 0.91, "warning_codes": []}],
            },
        )

    provider = HttpFormulaProvider(settings, transport=httpx.MockTransport(handler))
    candidates = provider.recognize(
        FormulaRegionArtifact(PageArtifact(image_bytes(20, 20), 20, 20), (0, 0, 1, 1))
    )
    assert candidates[0].latex == "x^2"
    assert candidates[0].provider == "synthetic-service"
    assert candidates[0].provider_version == "model-v1"

    settings.formula_recognition_allowed_hosts = []
    with pytest.raises(RecognitionError) as rejected:
        provider.recognize(
            FormulaRegionArtifact(PageArtifact(image_bytes(20, 20), 20, 20), (0, 0, 1, 1))
        )
    assert rejected.value.code == "FORMULA_PROVIDER_UNAVAILABLE"


def test_eval_manifest_rejects_private_source_fields() -> None:
    with pytest.raises(ValueError, match="private source fields"):
        validate_eval_dataset(
            {
                "schema_version": "formula-ocr-eval-v1",
                "cases": [
                    {
                        "id": "synthetic-001",
                        "modality": "synthetic",
                        "expected_latex": "x",
                        "predictions": [{"latex": "x", "source_path": "private.pdf"}],
                    }
                ],
            }
        )


def test_synthetic_offline_evaluation_reports_accuracy_and_review_gate() -> None:
    report = evaluate(json.loads(SYNTHETIC_DATASET.read_text(encoding="utf-8")))
    assert report["schema_version"] == "formula-ocr-eval-report-v1"
    assert report["production_ready"] is False
    assert report["human_confirmation_required"] is True
    assert report["metrics"]["total_cases"] == 3
    assert report["metrics"]["normalized_exact_match_rate"] == pytest.approx(1 / 3)
    assert report["metrics"]["manual_review_rate"] == pytest.approx(2 / 3)
    assert report["cases"][2]["warning_codes"] == ["NO_CANDIDATE"]


def test_formula_region_fake_recognition_and_explicit_teacher_disposition() -> None:
    actor, db = actor_and_db()
    storage = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    settings = get_settings()
    previous_text_provider = settings.recognition_provider
    previous_formula_provider = settings.formula_recognition_provider
    settings.recognition_provider = "fake"
    settings.formula_recognition_provider = "fake"
    try:
        assignment = create(client, active_class(db, actor.id).id)
        assignment_id = assignment["id"]
        upload = client.post(
            f"/api/assignments/{assignment_id}/files",
            files={"file": ("synthetic.png", synthetic_formula_bytes(), "image/png")},
        )
        assert upload.status_code == 201
        version_id = client.get(f"/api/assignments/{assignment_id}").json()["paper_version"]["id"]
        job = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs?run_now=true",
            json={"paper_version_id": version_id, "idempotency_key": "formula-local-loop"},
        ).json()
        page_id = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/pages"
        ).json()[0]["paper_page_id"]
        base = f"/api/assignments/{assignment_id}/recognition/jobs/{job['id']}/formulas"
        created = client.post(
            f"{base}/regions",
            json={
                "paper_page_id": page_id,
                "region_kind": "display",
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.5,
            },
        )
        assert created.status_code == 201, created.text
        region_id = created.json()["id"]
        duplicate = client.post(
            f"{base}/regions",
            json={
                "paper_page_id": page_id,
                "region_kind": "display",
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.5,
            },
        )
        assert duplicate.status_code == 409
        recognized = client.post(f"{base}/regions/{region_id}/recognize")
        assert recognized.status_code == 200, recognized.text
        assert len(recognized.json()["candidates"]) == 1
        assert recognized.json()["has_alternatives"] is True
        candidate_id = recognized.json()["candidates"][0]["id"]
        expanded = client.get(f"{base}/regions?include_alternatives=true").json()
        assert len(expanded[0]["candidates"]) == 2
        denied = client.post(
            f"{base}/regions/{region_id}/candidates/{candidate_id}/disposition",
            json={"action": "accept", "explicit_confirmation": False},
        )
        assert denied.status_code == 422
        accepted = client.post(
            f"{base}/regions/{region_id}/candidates/{candidate_id}/disposition",
            json={
                "action": "accept",
                "explicit_confirmation": True,
                "edited_latex": r"\frac{1}{x^2}+1",
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "confirmed"
        assert accepted.json()["candidates"][0]["latex"] == r"\frac{1}{x^2}+1"

        unreadable_region = client.post(
            f"{base}/regions",
            json={
                "paper_page_id": page_id,
                "region_kind": "display",
                "x": 0.02,
                "y": 0.72,
                "width": 0.3,
                "height": 0.2,
            },
        )
        assert unreadable_region.status_code == 201
        unreadable_id = unreadable_region.json()["id"]
        missing_confirmation = client.post(
            f"{base}/regions/{unreadable_id}/unreadable",
            json={
                "reason": "subscript_ambiguous",
                "explicit_confirmation": False,
            },
        )
        assert missing_confirmation.status_code == 422
        marked = client.post(
            f"{base}/regions/{unreadable_id}/unreadable",
            json={
                "reason": "subscript_ambiguous",
                "explicit_confirmation": True,
            },
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["status"] == "rejected"
        assert marked.json()["unreadable_reason"] == "subscript_ambiguous"

        blocked_region = client.post(
            f"{base}/regions",
            json={
                "paper_page_id": page_id,
                "region_kind": "display",
                "x": 0.68,
                "y": 0.72,
                "width": 0.3,
                "height": 0.2,
            },
        )
        assert blocked_region.status_code == 201
        blocked = client.post(f"{base}/regions/{blocked_region.json()['id']}/recognize")
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["code"] == "FORMULA_IMAGE_QUALITY_BLOCKED"
        assert blocked.json()["details"]["allowed_unreadable_reasons"] == list(UNREADABLE_REASONS)

        redrawn = client.patch(
            f"{base}/regions/{unreadable_id}",
            json={
                "region_kind": "unknown",
                "x": 0.35,
                "y": 0.72,
                "width": 0.3,
                "height": 0.2,
            },
        )
        assert redrawn.status_code == 200, redrawn.text
        assert redrawn.json()["status"] == "manual_required"
        assert redrawn.json()["candidates"] == []
        assert redrawn.json()["region"] == {
            "x": "0.35",
            "y": "0.72",
            "width": "0.3",
            "height": "0.2",
        }
    finally:
        settings.recognition_provider = previous_text_provider
        settings.formula_recognition_provider = previous_formula_provider
        app.dependency_overrides.pop(get_storage, None)
