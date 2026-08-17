from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings
from app.recognition.pipeline import (
    DerivedQuestionRegion,
    PageArtifact,
    RecognitionError,
    UnavailableProvider,
    provider_from_settings,
    text_for_question_region,
)
from app.recognition.tesseract_provider import (
    TESSERACT_MAX_BLOCKS,
    TesseractProcessResult,
    TesseractProvider,
    parse_tesseract_tsv,
)
from app.recognition.text_integrity import text_quality_statistics
from pydantic import ValidationError

HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\t"
    "height\tconf\ttext\n"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_files(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "tesseract"
    data_root = root / "tessdata"
    data_root.mkdir(parents=True)
    binary = root / "tesseract.exe"
    binary.write_bytes(b"audited-tesseract-binary")
    chi_sim = data_root / "chi_sim.traineddata"
    chi_sim.write_bytes(b"audited-chi-sim")
    eng = data_root / "eng.traineddata"
    eng.write_bytes(b"audited-eng")
    license_path = root / "LICENSE"
    license_path.write_text("Apache License, Version 2.0", encoding="utf-8")
    return {
        "binary_path": str(binary),
        "data_root": str(data_root),
        "license_path": str(license_path),
        "expected_version": "5.5.2",
        "binary_sha256": _sha(binary),
        "chi_sim_sha256": _sha(chi_sim),
        "eng_sha256": _sha(eng),
        "license_sha256": _sha(license_path),
    }


def _provider(tmp_path: Path, runner: Any) -> TesseractProvider:
    return TesseractProvider(**_runtime_files(tmp_path), runner=runner)


def _valid_tsv() -> bytes:
    return (
        HEADER
        + "5\t1\t1\t1\t1\t2\t150\t18\t30\t24\t80.0\t第二\n"
        + "5\t1\t1\t1\t1\t1\t10\t20\t100\t20\t96.5\tAhaMark\n"
    ).encode()


def test_default_factory_is_closed_without_running_a_process() -> None:
    provider = provider_from_settings(Settings(recognition_provider="tesseract"))
    assert isinstance(provider, UnavailableProvider)
    assert provider.available()[0] is False


def test_enabled_settings_require_all_explicit_runtime_materials() -> None:
    with pytest.raises(ValidationError, match="Tesseract configuration rejected"):
        Settings(
            recognition_provider="tesseract",
            recognition_tesseract_runtime_enabled=True,
        )


def test_explicit_runtime_uses_no_shell_and_parses_deterministic_blocks(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], bytes, float]] = []

    def runner(argv: Any, content: bytes, timeout: float) -> TesseractProcessResult:
        args = tuple(argv)
        calls.append((args, content, timeout))
        if args[-1] == "--version":
            return TesseractProcessResult(0, b"tesseract 5.5.2\n", b"")
        return TesseractProcessResult(0, _valid_tsv(), b"")

    provider = _provider(tmp_path, runner)
    page = PageArtifact(b"synthetic-png", 200, 100)
    blocks = provider.recognize(page)

    assert [block.text for block in blocks] == ["AhaMark", "第二"]
    assert [block.source for block in blocks] == ["tesseract:5.5.2"] * 2
    assert blocks[0].region == pytest.approx((0.05, 0.2, 0.5, 0.2))
    command = calls[-1][0]
    assert command[1:3] == ("stdin", "stdout")
    assert command[command.index("-l") + 1] == "chi_sim+eng"
    assert "--tessdata-dir" in command
    assert calls[-1][1] == b"synthetic-png"


def test_tesseract_source_is_trusted_by_region_and_quality_aggregation() -> None:
    page_id = uuid.uuid4()
    blocks = parse_tesseract_tsv(
        _valid_tsv(), PageArtifact(b"png", 200, 100), source_version="5.5.2"
    )
    region = DerivedQuestionRegion(
        paper_page_id=page_id,
        x=0,
        y=0,
        width=1,
        height=1,
    )
    assert (
        text_for_question_region([(page_id, block) for block in blocks], [region])
        == "AhaMark\n第二"
    )
    block = blocks[0]
    quality = text_quality_statistics(
        [block.text],
        sources=[block.source],
        confidences=[block.confidence],
        block_types=[block.block_type],
    )
    assert quality["text_source"] == "tesseract"


def test_tesseract_tsv_treats_double_quote_as_literal_text() -> None:
    output = (
        HEADER
        + '5\t1\t1\t1\t1\t1\t10\t20\t30\t10\t95\tquoted"text\n'
        + "5\t1\t1\t1\t1\t2\t50\t20\t30\t10\t90\tsecond\n"
    ).encode()

    blocks = parse_tesseract_tsv(
        output,
        PageArtifact(content=b"png", width=100, height=100),
        source_version="5.3.0",
    )

    assert [block.text for block in blocks] == ['quoted"text', "second"]


@pytest.mark.parametrize(
    "body",
    [
        b"not-tsv",
        (HEADER + "5\t1\t1\t1\t1\t1\t0\t0\t0\t10\t90\tx\n").encode(),
        (HEADER + "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\tnan\tx\n").encode(),
        (HEADER + "5\t1\t1\t1\t1\t1\t195\t0\t10\t10\t90\tx\n").encode(),
        (HEADER + "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tx\u202ey\n").encode(),
        (HEADER + "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tx\x00y\n").encode(),
    ],
)
def test_tsv_parser_fails_closed_on_malformed_output(body: bytes) -> None:
    with pytest.raises(RecognitionError) as exc_info:
        parse_tesseract_tsv(body, PageArtifact(b"png", 200, 100), source_version="5.5.2")
    assert exc_info.value.code == "OCR_PROVIDER_OUTPUT_INVALID"


def test_tsv_parser_rejects_too_many_blocks() -> None:
    row = "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t90\tx\n"
    body = (HEADER + row * (TESSERACT_MAX_BLOCKS + 1)).encode()
    with pytest.raises(RecognitionError) as exc_info:
        parse_tesseract_tsv(body, PageArtifact(b"png", 200, 100), source_version="5.5.2")
    assert exc_info.value.code == "OCR_PROVIDER_OUTPUT_INVALID"


def test_hash_version_timeout_and_process_failures_are_non_sensitive(tmp_path: Path) -> None:
    files = _runtime_files(tmp_path)
    files["binary_sha256"] = "0" * 64
    invalid = TesseractProvider(**files, runner=lambda *_: TesseractProcessResult(0, b"", b""))
    assert invalid.available() == (False, "Tesseract 本地运行材料未通过校验")

    def wrong_version(*_: Any) -> TesseractProcessResult:
        return TesseractProcessResult(0, b"tesseract 0.0.0 C:\\secret", b"")

    mismatch = _provider(tmp_path / "version", wrong_version)
    assert mismatch.available() == (False, "Tesseract 本地运行时版本不匹配")

    def timeout(argv: Any, content: bytes, seconds: float) -> TesseractProcessResult:
        if tuple(argv)[-1] == "--version":
            return TesseractProcessResult(0, b"tesseract 5.5.2", b"")
        raise subprocess.TimeoutExpired("C:\\secret\\tesseract.exe", seconds)

    provider = _provider(tmp_path / "timeout", timeout)
    with pytest.raises(RecognitionError) as exc_info:
        provider.recognize(PageArtifact(b"png", 10, 10))
    assert exc_info.value.code == "OCR_INFERENCE_FAILED"
    assert "secret" not in str(exc_info.value).lower()


def test_license_and_post_validation_file_changes_fail_closed(tmp_path: Path) -> None:
    files = _runtime_files(tmp_path / "license")
    license_path = Path(files["license_path"])
    license_path.write_text("unknown terms", encoding="utf-8")
    files["license_sha256"] = _sha(license_path)
    invalid_license = TesseractProvider(
        **files, runner=lambda *_: TesseractProcessResult(0, b"tesseract 5.5.2", b"")
    )
    assert invalid_license.available() == (
        False,
        "Tesseract 本地运行材料未通过校验",
    )

    provider = _provider(
        tmp_path / "changed",
        lambda *_: TesseractProcessResult(0, b"tesseract 5.5.2", b""),
    )
    Path(provider._data_root / "eng.traineddata").write_bytes(b"changed")
    assert provider.available() == (False, "Tesseract 本地运行时不可用")
