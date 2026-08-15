from dataclasses import asdict

import pytest
from app.recognition.math_structure import (
    VERSION,
    apply_math_risk_status,
    detect_math_structure_risks,
)
from app.recognition.pipeline import ProviderBlock


def _block(
    text: str,
    region: tuple[float, float, float, float] = (0.1, 0.1, 0.2, 0.04),
    *,
    status: str = "adopted",
    latex: str | None = None,
) -> ProviderBlock:
    return ProviderBlock("text", text, latex, 0.9, region, status=status, source="test")


@pytest.mark.parametrize(
    "text",
    ["x² + y₁", "α + β", "A ∈ B", r"\frac{1}{2}", r"x^{2}", r"\sqrt{x}"],
)
def test_formula_signals_require_review(text: str) -> None:
    assessment = detect_math_structure_risks([_block(text)])

    assert assessment.version == VERSION == "math-structure-risk-v1"
    assert assessment.risk_codes == ("FORMULA_REVIEW_REQUIRED",)
    assert assessment.evidence[0].block_indexes == (0,)


@pytest.mark.parametrize(
    "text",
    [r"C:\temp\paper.txt", r"literal \n marker", "普通中文题目", "ordinary English", "+", "1 + 2"],
)
def test_common_text_and_path_are_not_formula_signals(text: str) -> None:
    assert not detect_math_structure_risks([_block(text)]).risk_codes


def test_matrix_command_and_two_by_two_math_geometry_are_layout_risks() -> None:
    explicit = detect_math_structure_risks([_block(r"\begin{matrix}1&2\\3&4\end{matrix}")])
    grid = [
        _block("1+0", (0.10, 0.10, 0.05, 0.03)),
        _block("2+0", (0.24, 0.10, 0.05, 0.03)),
        _block("3+0", (0.10, 0.18, 0.05, 0.03)),
        _block("4+0", (0.24, 0.18, 0.05, 0.03)),
    ]

    assert "MATH_LAYOUT_REVIEW_REQUIRED" in explicit.risk_codes
    assert detect_math_structure_risks(grid).risk_codes == ("MATH_LAYOUT_REVIEW_REQUIRED",)


def test_incomplete_or_distant_math_cells_are_not_a_two_by_two_layout() -> None:
    incomplete = [
        _block("1+0", (0.10, 0.10, 0.05, 0.03)),
        _block("2+0", (0.24, 0.10, 0.05, 0.03)),
        _block("3+0", (0.10, 0.18, 0.05, 0.03)),
        _block("4+0", (0.70, 0.70, 0.05, 0.03)),
    ]

    assert not detect_math_structure_risks(incomplete).risk_codes


def test_distant_formula_signals_have_repeated_code_and_local_evidence() -> None:
    blocks = [
        _block("x²", (0.05, 0.05, 0.08, 0.04)),
        _block("middle ordinary question text", (0.30, 0.45, 0.30, 0.05)),
        _block("α", (0.85, 0.90, 0.05, 0.04)),
    ]

    assessment = detect_math_structure_risks(blocks)

    assert assessment.risk_codes == (
        "FORMULA_REVIEW_REQUIRED",
        "FORMULA_REVIEW_REQUIRED",
    )
    assert [item.block_indexes for item in assessment.evidence] == [(0,), (2,)]
    assert all(item.region[2] < 0.1 for item in assessment.evidence)


def test_table_cells_and_geometry_labels_are_not_layout_risks() -> None:
    table = [
        _block("Name", (0.1, 0.1, 0.1, 0.03)),
        _block("Score", (0.3, 0.1, 0.1, 0.03)),
        _block("Alice", (0.1, 0.2, 0.1, 0.03)),
        _block("Ten", (0.3, 0.2, 0.1, 0.03)),
    ]
    labels = [
        _block("A", (0.1, 0.1, 0.03, 0.03)),
        _block("B", (0.3, 0.1, 0.03, 0.03)),
        _block("C", (0.2, 0.3, 0.03, 0.03)),
        _block("D", (0.4, 0.3, 0.03, 0.03)),
    ]

    assert not detect_math_structure_risks(table).risk_codes
    assert not detect_math_structure_risks(labels).risk_codes


def test_two_prose_columns_report_reading_order_conflict() -> None:
    blocks = [
        _block("Left column first sentence", (0.05, 0.10, 0.30, 0.05)),
        _block("Left column second sentence", (0.05, 0.30, 0.30, 0.05)),
        _block("Right column first sentence", (0.55, 0.12, 0.30, 0.05)),
        _block("Right column second sentence", (0.55, 0.32, 0.30, 0.05)),
        _block("A cross-column page heading", (0.05, 0.02, 0.90, 0.05)),
    ]

    assessment = detect_math_structure_risks(blocks)

    assert assessment.risk_codes == ("READING_ORDER_CONFLICT",)
    assert assessment.evidence[0].block_indexes == (0, 1, 2, 3)
    assert assessment.evidence[0].region == pytest.approx((0.05, 0.1, 0.8, 0.27))


def test_reading_order_requires_two_prose_blocks_per_column_and_y_overlap() -> None:
    blocks = [
        _block("Left column first sentence", (0.05, 0.05, 0.30, 0.05)),
        _block("Left column second sentence", (0.05, 0.15, 0.30, 0.05)),
        _block("Right column first sentence", (0.55, 0.65, 0.30, 0.05)),
        _block("Right column second sentence", (0.55, 0.75, 0.30, 0.05)),
    ]

    assert not detect_math_structure_risks(blocks).risk_codes


def test_application_changes_only_adopted_risky_blocks_and_preserves_payload() -> None:
    blocks = [
        _block("x²", status="adopted", latex=r"x^2"),
        _block("α", (0.4, 0.1, 0.1, 0.04), status="source_conflict"),
        _block("plain text", (0.1, 0.3, 0.2, 0.04), status="adopted"),
    ]
    assessment = detect_math_structure_risks(blocks)

    applied = apply_math_risk_status(blocks, assessment)

    assert applied[0].status == "manual_required"
    assert applied[1].status == "source_conflict"
    assert applied[2].status == "adopted"
    assert [(item.text, item.latex, item.region) for item in applied] == [
        (item.text, item.latex, item.region) for item in blocks
    ]
    public_evidence = asdict(assessment)["evidence"]
    assert set(public_evidence[0]) == {"block_indexes", "region"}
    assert "x²" not in repr(public_evidence)


def test_detection_is_deterministic_and_bounds_work() -> None:
    blocks = [_block("plain text") for _ in range(512)] + [_block("x²")]

    first = detect_math_structure_risks(blocks)

    assert first == detect_math_structure_risks(blocks)
    assert not first.risk_codes
