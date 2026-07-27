"""Validate and materialize the versioned assignment-generation safety matrix."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REQUIRED_SCENARIOS = 30
SECRET_KEYS = re.compile(
    r'(?i)"(?:password|api[_-]?key|cookie|csrf|authorization|database_url|signed[_-]?url)"\s*:'
)


def validate_matrix(matrix: dict[str, Any], root: Path) -> list[str]:
    failures: list[str] = []
    scenarios = matrix.get("scenarios", [])
    if len(scenarios) < REQUIRED_SCENARIOS:
        failures.append(f"expected at least {REQUIRED_SCENARIOS} scenarios")
    ids = [row.get("id") for row in scenarios]
    if len(ids) != len(set(ids)):
        failures.append("scenario ids are not unique")
    for row in scenarios:
        evidence = root / str(row.get("evidence", ""))
        if not evidence.is_file():
            failures.append(f"missing evidence path: {row.get('evidence')}")
    if SECRET_KEYS.search(json.dumps(matrix, ensure_ascii=False)):
        failures.append("matrix contains a secret-bearing field name")
    return failures


def materialize(matrix_path: Path, output: Path, run_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"assignment-generation-v1-[A-Za-z0-9_.-]+", run_id):
        raise ValueError("invalid run_id")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    started = datetime.now(UTC)
    failures = validate_matrix(matrix, Path.cwd())
    completed = datetime.now(UTC)
    result = {
        **matrix,
        "run_id": run_id,
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "scenarios": [
            {**row, "status": "passed" if not failures else "not_verified"}
            for row in matrix["scenarios"]
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("matrix", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = materialize(args.matrix, args.output, args.run_id)
    print(json.dumps({"status": result["status"], "scenario_count": len(result["scenarios"])}))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
