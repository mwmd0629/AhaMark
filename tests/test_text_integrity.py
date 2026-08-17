import pytest
from app.recognition.text_integrity import (
    CHARACTER_ENCODING_CORRUPTION_DETECTED,
    CharacterEncodingCorruptionError,
    ensure_text_fields_integrity,
    inspect_text_integrity,
    text_quality_statistics,
)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("???? x?+xy+y?=7", "ASCII_QUESTION_MARK_RUN"),
        ("求函数???在区间?上的?值", "CONTEXTUAL_REPLACEMENT_QUESTION_MARKS"),
        ("矩阵包含损坏字符\ufffd", "UNICODE_REPLACEMENT_CHARACTER"),
        ("题干\x00后半段", "NUL_CHARACTER"),
        ("题干\x1b后半段", "DISALLOWED_CONTROL_CHARACTER"),
    ],
)
def test_corruption_signals_are_rejected_without_returning_source_text(
    text: str, reason: str
) -> None:
    findings = inspect_text_integrity(text, field_path="candidate.content_text")
    assert any(finding.reason == reason for finding in findings)
    with pytest.raises(CharacterEncodingCorruptionError) as caught:
        ensure_text_fields_integrity([("candidate.content_text", text)])
    assert caught.value.code == CHARACTER_ENCODING_CORRUPTION_DETECTED
    assert text not in str(caught.value)
    assert all("text" not in details for details in caught.value.safe_details)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "这道题的答案是什么？",
        "Does f(x) exist? Explain.",
        "Is x positive? Is y positive?",
        r"\frac{x^2+y^2}{\sqrt{2}} \in \mathbb{R}",
        "求 ∂u/∂x、∫₀¹ f(x)dx 与矩阵 Aᵀ 的值，α≤β。",
        "中英文 mixed content with λ and matrix B².",
    ],
)
def test_normal_questions_latex_and_unicode_math_are_allowed(text: str | None) -> None:
    assert inspect_text_integrity(text, field_path="candidate.content_text") == ()
    ensure_text_fields_integrity([("candidate.content_text", text)])


def test_quality_statistics_are_content_free_and_separate_source_and_region_risks() -> None:
    stats = text_quality_statistics(
        ["???? x?+xy+y?=7", "∫₀¹f(x)dx"],
        sources=["rapidocr:3.0"],
        confidences=[0.62, 0.91],
        block_types=["text", "formula", "table"],
    )
    assert stats == {
        "character_count": 24,
        "text_source": "rapidocr",
        "low_confidence_block_count": 1,
        "suspicious_character_count": 6,
        "ascii_question_mark_count": 6,
        "suspicious_reason_counts": {
            "ASCII_QUESTION_MARK_RUN": 1,
            "CONTEXTUAL_REPLACEMENT_QUESTION_MARKS": 1,
        },
        "has_formula_region": True,
        "has_figure_region": False,
        "has_table_region": True,
    }
    assert "????" not in repr(stats)
