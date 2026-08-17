"""Generate private, review-required recognition drafts with the Codex CLI.

The command deliberately stays outside the product recognition provider. It reads an
anonymous annotation seed, sends one local image at a time to Codex, and checkpoints
strict draft JSON outside the repository. Nothing produced here is Gold or accuracy
evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

SEED_VERSION: Final = "recognition-private-annotation-v1"
DRAFT_VERSION: Final = "recognition-private-codex-drafts-v1"
REQUIRED_RESULT_KEYS: Final = {
    "draft_text",
    "question_numbers",
    "formula_drafts",
    "uncertainties",
    "manual_review_required",
}
REQUIRED_FORMULA_KEYS: Final = {
    "linear_text",
    "latex",
    "location_hint",
    "uncertain",
}

OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": sorted(REQUIRED_RESULT_KEYS),
    "properties": {
        "draft_text": {"type": "string"},
        "question_numbers": {"type": "array", "items": {"type": "string"}},
        "formula_drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(REQUIRED_FORMULA_KEYS),
                "properties": {
                    "linear_text": {"type": "string"},
                    "latex": {"type": "string"},
                    "location_hint": {"type": "string"},
                    "uncertain": {"type": "boolean"},
                },
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "manual_review_required": {"type": "boolean", "const": True},
    },
}

PROMPT: Final = """你是私有数学页面识别草稿助手。
只分析随本次请求附加的一张图片，不调用 shell、文件工具、网络搜索、OCR 或外部知识。

严格要求：
1. draft_text 只逐字转录图片中实际可见的笔画和文字，按页面自然阅读顺序保留换行。
   不得根据题号、公式逻辑、教材常识或上下文补写图片中没有的题干、条件、连接词、
   推导步骤或结论，也不得纠正原图。
2. draft_text 只能使用线性 Unicode 数学文本，不得包含 Markdown、美元定界符、反斜杠
   或任何 LaTeX 命令。LaTeX 只允许出现在 formula_drafts.latex。正文公式使用 ∇、×、
   ·、∂、√、Σ、∫、上下标线性记法和 [分子]/[分母] 等可读 Unicode 形式。
3. 圈号、小题号、省略号、删除痕迹和原图已有连接词只在确实可见时转录；不得把推测的
   小题编号或说明性文字补进 draft_text。
4. question_numbers 只写真正题号，不写章节号、页码或页眉序号。
5. formula_drafts 按完整数学表达式给出 linear_text、LaTeX、页面内相对位置说明；
   linear_text 同样不得含 LaTeX，不拆成无意义单字符。
6. 看不清、被遮挡或结构不确定时不得猜测，在正文对应位置使用“⟦不清⟧”，并写入
   uncertainties；公式 uncertain=true。宁可留不清标记，也不要补全合理文本。
7. 所有内容只是待人工核对草稿，manual_review_required 必须为 true；不得声称准确率、置信度或已核对。
8. 严格按给定 JSON schema 输出，不添加字段。
"""

Runner = Callable[[Path, Path, Path, Path], dict[str, Any]]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _require_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a UUID string") from error
    if str(parsed) != value.lower():
        raise ValueError(f"{label} must use canonical UUID form")
    return value


def load_seed(seed_path: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = _load_json(seed_path)
    if not isinstance(payload, dict) or payload.get("schema_version") != SEED_VERSION:
        raise ValueError("unsupported annotation seed")
    dataset_id = _require_uuid(payload.get("dataset_id"), "dataset_id")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("annotation seed must contain cases")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for row in cases:
        if not isinstance(row, dict):
            raise ValueError("annotation case must be an object")
        case_id = _require_uuid(row.get("case_id"), "case_id")
        image_file = row.get("image_file")
        if case_id in seen or not isinstance(image_file, str) or not image_file:
            raise ValueError("annotation cases must have unique IDs and image files")
        image_name = Path(image_file).name
        if Path(image_name).stem.lower() != case_id or Path(image_name).suffix.lower() != ".png":
            raise ValueError("annotation image names must be anonymous case UUID PNGs")
        seen.add(case_id)
        validated.append(row)
    return dataset_id, validated


def locate_image(image_root: Path, case: dict[str, Any]) -> Path:
    expected_name = Path(str(case["image_file"])).name
    matches = [path for path in image_root.rglob(expected_name) if path.is_file()]
    if len(matches) != 1:
        raise ValueError(f"case {case['case_id']} must resolve to exactly one image")
    resolved_root = image_root.resolve(strict=True)
    resolved = matches[0].resolve(strict=True)
    if not _is_relative_to(resolved, resolved_root):
        raise ValueError("image escaped the private image root")
    return resolved


def validate_result(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != REQUIRED_RESULT_KEYS:
        raise ValueError("Codex result has unexpected fields")
    if not isinstance(payload["draft_text"], str) or not payload["draft_text"].strip():
        raise ValueError("Codex result draft_text must be non-empty")
    if any(marker in payload["draft_text"] for marker in ("\\", "$", "```")):
        raise ValueError("Codex result draft_text must be plain linear Unicode, not LaTeX")
    for key in ("question_numbers", "uncertainties"):
        value = payload[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Codex result {key} must be a string array")
    formulas = payload["formula_drafts"]
    if not isinstance(formulas, list):
        raise ValueError("Codex result formula_drafts must be an array")
    for formula in formulas:
        if not isinstance(formula, dict) or set(formula) != REQUIRED_FORMULA_KEYS:
            raise ValueError("Codex formula draft has unexpected fields")
        if not all(
            isinstance(formula[key], str) for key in ("linear_text", "latex", "location_hint")
        ) or not isinstance(formula["uncertain"], bool):
            raise ValueError("Codex formula draft has invalid field types")
        if any(marker in formula["linear_text"] for marker in ("\\", "$", "```")):
            raise ValueError("Codex formula linear_text must be plain linear Unicode")
    if payload["manual_review_required"] is not True:
        raise ValueError("Codex result must require manual review")
    return payload


def codex_runner(codex_command: Path, timeout_seconds: int) -> Runner:
    def run(image: Path, schema: Path, result: Path, workdir: Path) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [
                    str(codex_command),
                    "exec",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "-s",
                    "read-only",
                    "--skip-git-repo-check",
                    "--output-schema",
                    str(schema),
                    "-o",
                    str(result),
                    "-i",
                    str(image),
                    "-C",
                    str(workdir),
                    PROMPT,
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"Codex CLI exceeded the {timeout_seconds}-second page limit"
            ) from error
        if completed.returncode != 0:
            raise RuntimeError(f"Codex CLI failed with exit code {completed.returncode}")
        return validate_result(_load_json(result))

    return run


def _load_checkpoint(output: Path, dataset_id: str) -> list[dict[str, Any]]:
    if not output.exists():
        return []
    payload = _load_json(output)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "private", "dataset_id", "cases"}
        or payload.get("schema_version") != DRAFT_VERSION
        or payload.get("private") is not True
        or payload.get("dataset_id") != dataset_id
        or not isinstance(payload.get("cases"), list)
    ):
        raise ValueError("existing Codex checkpoint is incompatible")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in payload["cases"]:
        if not isinstance(row, dict) or set(row) != {"case_id", *REQUIRED_RESULT_KEYS}:
            raise ValueError("existing Codex checkpoint row is invalid")
        case_id = _require_uuid(row.get("case_id"), "checkpoint case_id")
        if case_id in seen:
            raise ValueError("existing Codex checkpoint has duplicate cases")
        validate_result({key: row[key] for key in REQUIRED_RESULT_KEYS})
        seen.add(case_id)
        rows.append(row)
    return rows


def generate_drafts(
    *,
    seed_path: Path,
    image_root: Path,
    output: Path,
    repository_root: Path,
    runner: Runner,
    limit: int | None = None,
) -> dict[str, int]:
    repository = repository_root.resolve(strict=True)
    for label, path in (("seed", seed_path), ("image root", image_root), ("output", output)):
        resolved = path.resolve(strict=label != "output")
        if _is_relative_to(resolved, repository):
            raise ValueError(f"private {label} must stay outside the repository")
    dataset_id, cases = load_seed(seed_path)
    existing = _load_checkpoint(output, dataset_id)
    by_id = {row["case_id"]: row for row in existing}
    seed_ids = {row["case_id"] for row in cases}
    if not set(by_id).issubset(seed_ids):
        raise ValueError("checkpoint contains a case outside the seed")
    remaining = [row for row in cases if row["case_id"] not in by_id]
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        remaining = remaining[:limit]

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-drafts-", dir=output.parent) as raw_temp:
        temporary_root = Path(raw_temp)
        schema = temporary_root / "schema.json"
        _write_json_atomic(schema, OUTPUT_SCHEMA)
        for index, case in enumerate(remaining):
            page_root = temporary_root / f"page-{index:04d}"
            workdir = page_root / "work"
            workdir.mkdir(parents=True)
            result_path = page_root / "result.json"
            result = runner(locate_image(image_root, case), schema, result_path, workdir)
            validated = validate_result(result)
            by_id[case["case_id"]] = {"case_id": case["case_id"], **validated}
            ordered = [by_id[row["case_id"]] for row in cases if row["case_id"] in by_id]
            _write_json_atomic(
                output,
                {
                    "schema_version": DRAFT_VERSION,
                    "private": True,
                    "dataset_id": dataset_id,
                    "cases": ordered,
                },
            )

    completed = len(by_id)
    return {
        "completed": completed,
        "remaining": len(cases) - completed,
        "formula_drafts": sum(len(row["formula_drafts"]) for row in by_id.values()),
        "uncertainties": sum(len(row["uncertainties"]) for row in by_id.values()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--image-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex-command", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--page-timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--consent-to-codex-upload",
        action="store_true",
        help="Required explicit acknowledgement that selected private images may be sent to Codex.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.consent_to_codex_upload:
        raise SystemExit("refusing to upload: pass --consent-to-codex-upload explicitly")
    if not args.codex_command.is_file():
        raise SystemExit("Codex command does not exist")
    if args.page_timeout_seconds < 30:
        raise SystemExit("page timeout must be at least 30 seconds")
    repository_root = Path(__file__).resolve().parent.parent
    summary = generate_drafts(
        seed_path=args.seed,
        image_root=args.image_root,
        output=args.output,
        repository_root=repository_root,
        runner=codex_runner(args.codex_command, args.page_timeout_seconds),
        limit=args.limit,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
