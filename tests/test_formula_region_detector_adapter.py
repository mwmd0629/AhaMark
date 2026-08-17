import math

import pytest
from app.core.config import Settings
from app.recognition.formula_region_detection import (
    FormulaRegionDetectionArtifact,
    FormulaRegionDetectionUnavailable,
    FormulaRegionProposal,
    UnavailableFormulaRegionDetector,
    validate_and_sort_proposals,
    validate_detection_artifact,
)
from pydantic import ValidationError


def test_formula_region_runtime_switches_default_closed() -> None:
    settings = Settings(_env_file=None)

    assert settings.formula_region_detection_enabled is False
    assert settings.formula_region_detection_model_download_allowed is False


@pytest.mark.parametrize("app_env", ["development", "test", "production"])
@pytest.mark.parametrize(
    ("field", "error"),
    [
        (
            "formula_region_detection_enabled",
            "FORMULA_REGION_DETECTION_ENABLED must remain false",
        ),
        (
            "formula_region_detection_model_download_allowed",
            "runtime model downloads are prohibited",
        ),
    ],
)
def test_formula_region_runtime_switches_fail_closed_in_every_environment(
    app_env: str, field: str, error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        Settings(_env_file=None, app_env=app_env, **{field: True})


def test_unavailable_detector_never_returns_proposals() -> None:
    detector = UnavailableFormulaRegionDetector()
    artifact = FormulaRegionDetectionArtifact(b"image", 10, 10, "image/png")

    assert detector.available()[0] is False
    with pytest.raises(FormulaRegionDetectionUnavailable) as error:
        detector.detect(artifact)
    assert error.value.code == "FORMULA_REGION_DETECTION_UNAVAILABLE"


def test_artifact_validation_is_strict_and_has_no_io_side_effects() -> None:
    artifact = FormulaRegionDetectionArtifact(b"image", 20, 10, "image/jpeg")

    assert validate_detection_artifact(artifact) is artifact
    with pytest.raises(ValueError, match="non-empty bytes"):
        validate_detection_artifact(FormulaRegionDetectionArtifact(b"", 20, 10, "image/png"))
    with pytest.raises(ValueError, match="positive integers"):
        validate_detection_artifact(FormulaRegionDetectionArtifact(b"x", True, 10, "image/png"))
    with pytest.raises(ValueError, match="unsupported"):
        validate_detection_artifact(FormulaRegionDetectionArtifact(b"x", 10, 10, "text/plain"))


def test_proposals_are_normalized_and_sorted_deterministically() -> None:
    proposals = [
        FormulaRegionProposal((0.5, 0.4, 0.2, 0.1), 0.7, "offline:v1"),
        FormulaRegionProposal((0.2, 0.1, 0.2, 0.1), 0.9, "offline:v1"),
        FormulaRegionProposal((0.1, 0.1, 0.2, 0.1), 0.9, "offline:v1"),
    ]

    first = validate_and_sort_proposals(proposals)

    assert first == validate_and_sort_proposals(tuple(reversed(proposals)))
    assert [item.bbox for item in first] == [
        (0.1, 0.1, 0.2, 0.1),
        (0.2, 0.1, 0.2, 0.1),
        (0.5, 0.4, 0.2, 0.1),
    ]
    assert proposals[0].bbox == (0.5, 0.4, 0.2, 0.1)


@pytest.mark.parametrize(
    ("proposal", "error"),
    [
        (FormulaRegionProposal((0.0, 0.0, 0.0, 0.1), 0.5, "offline:v1"), "bbox"),
        (FormulaRegionProposal((0.9, 0.0, 0.2, 0.1), 0.5, "offline:v1"), "bbox"),
        (FormulaRegionProposal((0.0, 0.0, 0.1, 0.1), math.nan, "offline:v1"), "score"),
        (FormulaRegionProposal((0.0, 0.0, 0.1, 0.1), True, "offline:v1"), "score"),
        (FormulaRegionProposal((0.0, 0.0, 0.1, 0.1), 0.5, "../../model"), "source"),
    ],
)
def test_invalid_proposals_fail_closed(proposal: FormulaRegionProposal, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        validate_and_sort_proposals([proposal])


def test_proposal_count_and_input_type_are_bounded() -> None:
    proposal = FormulaRegionProposal((0.0, 0.0, 0.1, 0.1), 0.5, "offline:v1")

    with pytest.raises(ValueError, match="count"):
        validate_and_sort_proposals([proposal, proposal], max_proposals=1)
    with pytest.raises(ValueError, match="sequence"):
        validate_and_sort_proposals("not proposals")  # type: ignore[arg-type]
