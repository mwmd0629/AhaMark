import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from app.recognition.pipeline import ProviderBlock

RiskCode = Literal[
    "FORMULA_REVIEW_REQUIRED",
    "MATH_LAYOUT_REVIEW_REQUIRED",
    "READING_ORDER_CONFLICT",
]

VERSION = "math-structure-risk-v1"

_SCRIPT = re.compile(r"[\u00b2\u00b3\u00b9\u2070-\u209f]")
_GREEK = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
_LATEX_COMMAND = re.compile(
    r"\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|iint|iiint|lim|infty|"
    r"alpha|beta|gamma|delta|theta|lambda|mu|pi|rho|sigma|phi|omega|"
    r"left|right|overline|underline|vec|begin\s*\{(?:matrix|pmatrix|bmatrix|"
    r"vmatrix|Vmatrix|cases)\})(?![A-Za-z])"
)
_STRUCTURAL_TOKEN = re.compile(r"(?:\^|_)\s*\{[^{}]+\}")
_MATRIX_COMMAND = re.compile(
    r"\\begin\s*\{(?:matrix|pmatrix|bmatrix|vmatrix|Vmatrix|cases)\}(?![A-Za-z])"
)
_CJK_OR_LATIN = re.compile(r"[A-Za-z\u3400-\u9fff]")


@dataclass(frozen=True)
class RiskEvidence:
    block_indexes: tuple[int, ...]
    region: tuple[float, float, float, float]


@dataclass(frozen=True)
class MathStructureAssessment:
    version: str
    risk_codes: tuple[RiskCode, ...]
    evidence: tuple[RiskEvidence, ...]

    @property
    def review_required(self) -> bool:
        return bool(self.risk_codes)


def _formula_signal(text: str | None) -> bool:
    value = text or ""
    if _SCRIPT.search(value) or _GREEK.search(value):
        return True
    if _LATEX_COMMAND.search(value) or _STRUCTURAL_TOKEN.search(value):
        return True
    return any(
        unicodedata.category(character) == "Sm" and character not in "+-=<>" for character in value
    )


def _short_math_block(block: ProviderBlock) -> bool:
    value = "".join((block.text or "").split())
    if not value or len(value) > 12:
        return False
    if len(value) == 1 and value.isalpha():
        return False
    if _formula_signal(value):
        return True
    has_number = any(character.isdigit() for character in value)
    has_operator = any(character in "+-=*/^()[]{}" for character in value)
    return has_number and has_operator


def _union_region(
    blocks: Sequence[ProviderBlock], indexes: Sequence[int]
) -> tuple[float, float, float, float]:
    left = min(blocks[index].region[0] for index in indexes)
    top = min(blocks[index].region[1] for index in indexes)
    right = max(blocks[index].region[0] + blocks[index].region[2] for index in indexes)
    bottom = max(blocks[index].region[1] + blocks[index].region[3] for index in indexes)
    left = max(0.0, min(1.0, left))
    top = max(0.0, min(1.0, top))
    right = max(left, min(1.0, right))
    bottom = max(top, min(1.0, bottom))
    return (left, top, right - left, bottom - top)


def _axis_clusters(
    indexes: Sequence[int], values: dict[int, float], tolerance: float
) -> list[tuple[int, ...]]:
    clusters: list[list[int]] = []
    anchors: list[float] = []
    for index in sorted(indexes, key=lambda item: (values[item], item)):
        if not anchors or values[index] - anchors[-1] > tolerance:
            clusters.append([index])
            anchors.append(values[index])
        else:
            clusters[-1].append(index)
    return [tuple(cluster) for cluster in clusters]


def _spatial_components(
    blocks: Sequence[ProviderBlock],
    indexes: Sequence[int],
    *,
    max_x_gap: float,
    max_y_gap: float,
) -> list[tuple[int, ...]]:
    remaining = set(indexes)
    components: list[tuple[int, ...]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        pending = [seed]
        component = {seed}
        while pending:
            current = pending.pop()
            cx, cy, cw, ch = blocks[current].region
            for other in sorted(remaining):
                ox, oy, ow, oh = blocks[other].region
                x_gap = max(0.0, max(cx, ox) - min(cx + cw, ox + ow))
                y_gap = max(0.0, max(cy, oy) - min(cy + ch, oy + oh))
                if x_gap <= max_x_gap and y_gap <= max_y_gap:
                    remaining.remove(other)
                    component.add(other)
                    pending.append(other)
        components.append(tuple(sorted(component)))
    return components


def _has_complete_two_by_two(blocks: Sequence[ProviderBlock], indexes: Sequence[int]) -> bool:
    x_values = {index: blocks[index].region[0] + blocks[index].region[2] / 2 for index in indexes}
    y_values = {index: blocks[index].region[1] + blocks[index].region[3] / 2 for index in indexes}
    x_clusters = _axis_clusters(indexes, x_values, 0.08)
    y_clusters = _axis_clusters(indexes, y_values, 0.045)
    for left_position, left in enumerate(x_clusters):
        for right in x_clusters[left_position + 1 :]:
            for top_position, top in enumerate(y_clusters):
                for bottom in y_clusters[top_position + 1 :]:
                    if all(
                        set(x_cluster) & set(y_cluster)
                        for x_cluster in (left, right)
                        for y_cluster in (top, bottom)
                    ):
                        return True
    return False


def _layout_groups(blocks: Sequence[ProviderBlock]) -> list[tuple[int, ...]]:
    groups: list[tuple[int, ...]] = [
        (index,) for index, block in enumerate(blocks) if _MATRIX_COMMAND.search(block.text or "")
    ]
    candidates = [index for index, block in enumerate(blocks) if _short_math_block(block)]
    for component in _spatial_components(blocks, candidates, max_x_gap=0.18, max_y_gap=0.10):
        if len(component) >= 4 and _has_complete_two_by_two(blocks, component):
            groups.append(component)
    return groups


def _is_prose_block(block: ProviderBlock) -> bool:
    value = " ".join((block.text or "").split())
    if len(value) < 8 or not _CJK_OR_LATIN.search(value) or _formula_signal(value):
        return False
    _, _, width, height = block.region
    return 0.0 < width < 0.55 and 0.0 < height < 0.25


def _reading_order_indexes(blocks: Sequence[ProviderBlock]) -> tuple[int, ...]:
    candidates = [index for index, block in enumerate(blocks) if _is_prose_block(block)]
    if len(candidates) < 4:
        return ()
    ordered = sorted(candidates, key=lambda index: blocks[index].region[0])
    best: tuple[float, tuple[int, ...], tuple[int, ...]] | None = None
    for split in range(2, len(ordered) - 1):
        left = tuple(ordered[:split])
        right = tuple(ordered[split:])
        left_edge = max(blocks[index].region[0] + blocks[index].region[2] for index in left)
        right_edge = min(blocks[index].region[0] for index in right)
        gutter = right_edge - left_edge
        if gutter < 0.12:
            continue
        left_top = min(blocks[index].region[1] for index in left)
        left_bottom = max(blocks[index].region[1] + blocks[index].region[3] for index in left)
        right_top = min(blocks[index].region[1] for index in right)
        right_bottom = max(blocks[index].region[1] + blocks[index].region[3] for index in right)
        overlap = max(0.0, min(left_bottom, right_bottom) - max(left_top, right_top))
        smaller_span = min(left_bottom - left_top, right_bottom - right_top)
        if smaller_span <= 0.0 or overlap / smaller_span < 0.5:
            continue
        if best is None or gutter > best[0]:
            best = (gutter, left, right)
    if best is None:
        return ()
    return tuple(sorted((*best[1], *best[2])))


def detect_math_structure_risks(
    blocks: Sequence[ProviderBlock],
) -> MathStructureAssessment:
    bounded_blocks = blocks[:512]
    codes: list[RiskCode] = []
    evidence: list[RiskEvidence] = []

    formula_indexes = tuple(
        index
        for index, block in enumerate(bounded_blocks)
        if _formula_signal(block.text) or _formula_signal(block.latex)
    )
    for formula_group in _spatial_components(
        bounded_blocks, formula_indexes, max_x_gap=0.08, max_y_gap=0.08
    ):
        codes.append("FORMULA_REVIEW_REQUIRED")
        evidence.append(RiskEvidence(formula_group, _union_region(bounded_blocks, formula_group)))

    for layout_group in _layout_groups(bounded_blocks):
        codes.append("MATH_LAYOUT_REVIEW_REQUIRED")
        evidence.append(RiskEvidence(layout_group, _union_region(bounded_blocks, layout_group)))

    reading_indexes = _reading_order_indexes(bounded_blocks)
    if reading_indexes:
        codes.append("READING_ORDER_CONFLICT")
        evidence.append(
            RiskEvidence(reading_indexes, _union_region(bounded_blocks, reading_indexes))
        )

    return MathStructureAssessment(VERSION, tuple(codes), tuple(evidence))


def apply_math_risk_status(
    blocks: Sequence[ProviderBlock], assessment: MathStructureAssessment
) -> list[ProviderBlock]:
    risky_indexes = {index for item in assessment.evidence for index in item.block_indexes}
    return [
        replace(block, status="manual_required")
        if index in risky_indexes and block.status == "adopted"
        else block
        for index, block in enumerate(blocks)
    ]
