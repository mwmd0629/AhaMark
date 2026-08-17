from types import SimpleNamespace

from app.assignment_generation.file_analysis import _content_evidence, _file_content_evidence


def block(
    source: str, text: str, confidence: float = 0.9, status: str = "recognized"
) -> SimpleNamespace:
    return SimpleNamespace(source=source, text=text, confidence=confidence, status=status)


def job(provider: str) -> SimpleNamespace:
    return SimpleNamespace(provider=provider)


def test_page_content_sources_are_conservative() -> None:
    pdf = _content_evidence(
        [block("pdf_text:pypdfium2", "Synthetic embedded text", 1.0)], job("unavailable")
    )
    assert pdf == ("text", "pdf_text", 1.0, 21)

    ocr = _content_evidence([block("rapidocr:3.9.2", "printed text", 0.82)], job("rapidocr"))
    assert ocr == ("scanned", "ocr", 0.82, 11)
    legacy_low_confidence = _content_evidence(
        [block("rapidocr:3.9.2", "legacy text", 0.65, "low_confidence")], job("rapidocr")
    )
    assert legacy_low_confidence == ("scanned", "ocr", 0.65, 10)

    mixed = _content_evidence(
        [
            block("pdf_text:pypdfium2", "short layer", 1.0),
            block("rapidocr:3.9.2", "image text", 0.74),
        ],
        job("rapidocr"),
    )
    assert mixed == ("mixed", "mixed", 0.74, 19)

    fake = _content_evidence([block("fake:1", "1. 测试题", 0.95)], job("fake"))
    assert fake == ("unknown", "unavailable", 0.0, 0)


def test_file_content_mode_requires_all_pages_to_have_verified_sources() -> None:
    assert _file_content_evidence([("text", "pdf_text", 1.0, 30), ("scanned", "ocr", 0.8, 20)]) == (
        "mixed",
        "mixed",
        0.8,
    )
    assert _file_content_evidence(
        [("text", "pdf_text", 1.0, 30), ("unknown", "unavailable", 0.0, 0)]
    ) == ("unknown", "unavailable", 0.0)
