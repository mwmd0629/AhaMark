import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import codex_assignment_question_draft as draft_script


def payload() -> dict[str, object]:
    return {
        "source_snapshot_hash": "a" * 64,
        "candidates": [
            {
                "ref": "1",
                "question_number": "1",
                "question_type": "calculation",
                "content_text": "计算矩阵 A 的秩",
                "content_latex": r"\operatorname{rank}(A)",
                "max_score": "5",
                "difficulty": None,
                "knowledge_points": ["线性代数"],
                "field_confidences": {
                    key: "0.9"
                    for key in (
                        "question_number",
                        "parent_relation",
                        "question_type",
                        "content_text",
                        "content_latex",
                        "max_score",
                        "difficulty",
                        "knowledge_points",
                        "regions",
                    )
                },
                "overall_confidence": "0.9",
                "evidence": {},
                "warning_codes": [],
                "manual_required": True,
                "regions": [
                    {
                        "page_id": str(uuid.uuid4()),
                        "display_order": 0,
                        "region_type": "stem",
                        "x": "0",
                        "y": "0",
                        "width": "1",
                        "height": "1",
                        "confidence": "0.9",
                    }
                ],
            }
        ],
    }


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_loader_accepts_valid_utf8_json(tmp_path: Path) -> None:
    path = tmp_path / "draft.json"
    write_json(path, payload())
    snapshot, extraction, raw = draft_script.load_payload(path)
    assert snapshot == "a" * 64
    assert extraction.candidates[0].content_text == "计算矩阵 A 的秩"
    assert "source_snapshot_hash" not in raw


@pytest.mark.parametrize(
    ("raw", "error_code"),
    [
        (b"\xff\xfe", "INVALID_UTF8_PAYLOAD"),
        (b"{not-json", "INVALID_JSON_PAYLOAD"),
    ],
)
def test_loader_rejects_invalid_bytes_or_json_before_database_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes, error_code: str
) -> None:
    path = tmp_path / "draft.json"
    path.write_bytes(raw)
    monkeypatch.setattr(
        draft_script,
        "SessionLocal",
        SimpleNamespace(begin=lambda: pytest.fail("database transaction opened")),
    )
    with pytest.raises(ValueError, match=error_code):
        draft_script.apply_draft(uuid.uuid4(), path)


def test_loader_rejects_corrupted_text_before_database_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = payload()
    value["candidates"][0]["content_text"] = "???? x?+xy+y?=7"  # type: ignore[index]
    path = tmp_path / "draft.json"
    write_json(path, value)
    monkeypatch.setattr(
        draft_script,
        "SessionLocal",
        SimpleNamespace(begin=lambda: pytest.fail("database transaction opened")),
    )
    with pytest.raises(ValueError, match="CHARACTER_ENCODING_CORRUPTION_DETECTED"):
        draft_script.apply_draft(uuid.uuid4(), path)


def test_cli_error_output_is_ascii_safe_and_does_not_echo_question_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source_text = "不会被控制台回显的合成题文"
    monkeypatch.setattr(
        draft_script,
        "apply_draft",
        lambda *_args: (_ for _ in ()).throw(ValueError("INVALID_QUESTION_DRAFT_PAYLOAD")),
    )
    monkeypatch.setattr(
        draft_script.sys,
        "argv",
        ["codex_assignment_question_draft.py", str(uuid.uuid4()), "synthetic-draft.json"],
    )
    with pytest.raises(SystemExit, match="2"):
        draft_script.main()
    captured = capsys.readouterr()
    assert captured.err.isascii()
    assert source_text not in captured.err
