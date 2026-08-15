"""Conservative, content-free checks for corrupted recognized text."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

CHARACTER_ENCODING_CORRUPTION_DETECTED: Final = "CHARACTER_ENCODING_CORRUPTION_DETECTED"

_ASCII_QUESTION_RUN = re.compile(r"\?{3,}")
_ALLOWED_CONTROLS = {"\t", "\n", "\r"}
_MATH_SYMBOLS = set("∂∫∑∏√∞≈≠≤≥±×÷∈∉⊂⊆∪∩→⇒⇔∀∃∇∆αβγδεζηθικλμνξοπρστυφχψω")


@dataclass(frozen=True)
class TextIntegrityFinding:
    """A stable reason and minimal non-content statistics for one field."""

    reason: str
    field_path: str
    character_count: int
    ascii_question_mark_count: int = 0
    longest_ascii_question_mark_run: int = 0
    replacement_character_count: int = 0
    disallowed_control_count: int = 0

    @property
    def code(self) -> str:
        return CHARACTER_ENCODING_CORRUPTION_DETECTED

    def safe_details(self) -> dict[str, int | str]:
        return {
            "code": self.code,
            "reason": self.reason,
            "field_path": self.field_path,
            "character_count": self.character_count,
            "ascii_question_mark_count": self.ascii_question_mark_count,
            "longest_ascii_question_mark_run": self.longest_ascii_question_mark_run,
            "replacement_character_count": self.replacement_character_count,
            "disallowed_control_count": self.disallowed_control_count,
        }


class CharacterEncodingCorruptionError(ValueError):
    """Raised without including the source text in the exception message."""

    def __init__(self, findings: tuple[TextIntegrityFinding, ...]):
        self.code = CHARACTER_ENCODING_CORRUPTION_DETECTED
        self.findings = findings
        reasons = ",".join(sorted({finding.reason for finding in findings}))
        super().__init__(f"{self.code}:{reasons}")

    @property
    def safe_details(self) -> list[dict[str, int | str]]:
        return [finding.safe_details() for finding in self.findings]


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def inspect_text_integrity(
    text: str | None, *, field_path: str
) -> tuple[TextIntegrityFinding, ...]:
    """Return conservative corruption findings without retaining or returning source text."""

    if not text:
        return ()
    character_count = len(text)
    question_count = text.count("?")
    runs = [len(match.group(0)) for match in _ASCII_QUESTION_RUN.finditer(text)]
    longest_run = max(runs, default=0)
    replacement_count = text.count("\ufffd")
    control_count = sum(
        1
        for character in text
        if character not in _ALLOWED_CONTROLS and unicodedata.category(character) == "Cc"
    )

    def finding(reason: str) -> TextIntegrityFinding:
        return TextIntegrityFinding(
            reason=reason,
            field_path=field_path,
            character_count=character_count,
            ascii_question_mark_count=question_count,
            longest_ascii_question_mark_run=longest_run,
            replacement_character_count=replacement_count,
            disallowed_control_count=control_count,
        )

    findings: list[TextIntegrityFinding] = []
    if replacement_count:
        findings.append(finding("UNICODE_REPLACEMENT_CHARACTER"))
    if "\x00" in text:
        findings.append(finding("NUL_CHARACTER"))
    if control_count:
        findings.append(finding("DISALLOWED_CONTROL_CHARACTER"))
    if longest_run >= 3:
        findings.append(finding("ASCII_QUESTION_MARK_RUN"))

    visible_count = sum(not character.isspace() for character in text)
    question_ratio = question_count / max(visible_count, 1)
    if character_count >= 16 and question_count >= 4 and question_ratio >= 0.15:
        findings.append(finding("ASCII_QUESTION_MARK_RATIO"))

    has_cjk = any(_is_cjk(character) for character in text)
    has_math = any(
        character in _MATH_SYMBOLS or unicodedata.category(character) == "Sm" for character in text
    )
    if (
        character_count >= 12
        and question_count >= 3
        and question_ratio >= 0.10
        and (has_cjk or has_math)
    ):
        findings.append(finding("CONTEXTUAL_REPLACEMENT_QUESTION_MARKS"))
    return tuple(findings)


def ensure_text_fields_integrity(fields: list[tuple[str, str | None]]) -> None:
    findings = tuple(
        finding
        for field_path, text in fields
        for finding in inspect_text_integrity(text, field_path=field_path)
    )
    if findings:
        raise CharacterEncodingCorruptionError(findings)


def text_quality_statistics(
    texts: Iterable[str | None],
    *,
    sources: Iterable[str] = (),
    confidences: Iterable[float | None] = (),
    block_types: Iterable[str] = (),
) -> dict[str, object]:
    """Build content-free metrics suitable for persistence and teacher-facing summaries."""

    text_values = list(texts)
    source_values = sorted(set(sources))
    type_values = set(block_types)
    reason_counts: Counter[str] = Counter()
    suspicious_character_count = 0
    ascii_question_mark_count = 0
    for index, text in enumerate(text_values):
        if not text:
            continue
        findings = inspect_text_integrity(text, field_path=f"texts[{index}]")
        reasons = {finding.reason for finding in findings}
        reason_counts.update(reasons)
        ascii_question_mark_count += text.count("?")
        suspicious_character_count += sum(
            character == "\ufffd"
            or (character not in _ALLOWED_CONTROLS and unicodedata.category(character) == "Cc")
            or (character == "?" and any("QUESTION_MARK" in reason for reason in reasons))
            for character in text
        )
    has_ocr = any(
        source.startswith("rapidocr:") or source == "printed_text_ocr_anchor"
        for source in source_values
    )
    has_pdf_text = any(
        source.startswith("pdf_text:") or source == "pdf_text_anchor" for source in source_values
    )
    if has_ocr and has_pdf_text:
        text_source = "mixed"
    elif has_ocr:
        text_source = "rapidocr"
    elif has_pdf_text and all(
        source.startswith("pdf_text:") or source == "pdf_text_anchor" for source in source_values
    ):
        text_source = "pdf_text"
    elif source_values:
        text_source = source_values[0].split(":", 1)[0]
    else:
        text_source = "unknown"
    return {
        "character_count": sum(len(text or "") for text in text_values),
        "text_source": text_source,
        "low_confidence_block_count": sum(
            confidence is not None and confidence < 0.70 for confidence in confidences
        ),
        "suspicious_character_count": suspicious_character_count,
        "ascii_question_mark_count": ascii_question_mark_count,
        "suspicious_reason_counts": dict(sorted(reason_counts.items())),
        "has_formula_region": "formula" in type_values,
        "has_figure_region": "figure" in type_values,
        "has_table_region": "table" in type_values,
    }
