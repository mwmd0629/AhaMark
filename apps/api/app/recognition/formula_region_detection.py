import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

MAX_PROPOSALS = 100
_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")


class FormulaRegionDetectionUnavailable(RuntimeError):
    code = "FORMULA_REGION_DETECTION_UNAVAILABLE"


@dataclass(frozen=True)
class FormulaRegionDetectionArtifact:
    content: bytes
    width: int
    height: int
    content_type: str


@dataclass(frozen=True)
class FormulaRegionProposal:
    bbox: tuple[float, float, float, float]
    score: float
    detection_source: str


class FormulaRegionDetector(Protocol):
    name: str
    version: str

    def available(self) -> tuple[bool, str | None]: ...

    def detect(
        self, artifact: FormulaRegionDetectionArtifact
    ) -> Sequence[FormulaRegionProposal]: ...


class UnavailableFormulaRegionDetector:
    name = "unavailable"
    version = "none"

    def available(self) -> tuple[bool, str | None]:
        return False, "公式区域检测尚未通过许可证、数据与盲测 readiness gate"

    def detect(self, artifact: FormulaRegionDetectionArtifact) -> Sequence[FormulaRegionProposal]:
        del artifact
        raise FormulaRegionDetectionUnavailable(self.available()[1])


def validate_detection_artifact(
    artifact: FormulaRegionDetectionArtifact,
) -> FormulaRegionDetectionArtifact:
    if type(artifact.content) is not bytes or not artifact.content:
        raise ValueError("formula-region artifact content must be non-empty bytes")
    if (
        isinstance(artifact.width, bool)
        or isinstance(artifact.height, bool)
        or not isinstance(artifact.width, int)
        or not isinstance(artifact.height, int)
        or artifact.width <= 0
        or artifact.height <= 0
    ):
        raise ValueError("formula-region artifact dimensions must be positive integers")
    if artifact.content_type not in {"image/png", "image/jpeg"}:
        raise ValueError("formula-region artifact content type is unsupported")
    return artifact


def validate_and_sort_proposals(
    proposals: Sequence[FormulaRegionProposal],
    *,
    max_proposals: int = MAX_PROPOSALS,
) -> tuple[FormulaRegionProposal, ...]:
    if isinstance(proposals, (str, bytes)) or not isinstance(proposals, Sequence):
        raise ValueError("formula-region proposals must be a sequence")
    if isinstance(max_proposals, bool) or not isinstance(max_proposals, int) or max_proposals < 1:
        raise ValueError("max_proposals must be a positive integer")
    if len(proposals) > max_proposals:
        raise ValueError("formula-region proposal count exceeds the configured limit")

    validated: list[FormulaRegionProposal] = []
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, FormulaRegionProposal):
            raise ValueError(f"formula-region proposal {index} has an invalid type")
        if not isinstance(proposal.bbox, tuple) or len(proposal.bbox) != 4:
            raise ValueError(f"formula-region proposal {index} bbox must be a four-item tuple")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in proposal.bbox
        ):
            raise ValueError(f"formula-region proposal {index} bbox must contain finite numbers")
        x, y, width, height = (float(value) for value in proposal.bbox)
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
            raise ValueError(f"formula-region proposal {index} bbox must be within 0..1")
        if (
            isinstance(proposal.score, bool)
            or not isinstance(proposal.score, (int, float))
            or not math.isfinite(proposal.score)
            or not 0 <= proposal.score <= 1
        ):
            raise ValueError(f"formula-region proposal {index} score must be within 0..1")
        if not isinstance(proposal.detection_source, str) or not _SOURCE.fullmatch(
            proposal.detection_source
        ):
            raise ValueError(f"formula-region proposal {index} detection_source is invalid")
        validated.append(
            FormulaRegionProposal(
                (x, y, width, height), float(proposal.score), proposal.detection_source
            )
        )

    return tuple(
        sorted(
            validated,
            key=lambda item: (
                -item.score,
                item.bbox[1],
                item.bbox[0],
                item.bbox[2],
                item.bbox[3],
                item.detection_source,
            ),
        )
    )
