"""Capture the pre-operation resource and database baseline for a landing run."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROTECTED_PROJECTS = (
    "ahamarkstage4ai233041",
    "ahamarkassignmentv620260726184700",
    "ahamarkassignmentv6c20260726201000",
)


def docker_rows(project: str) -> list[dict[str, str]]:
    completed = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{json .}}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    rows = []
    for line in completed.stdout.splitlines():
        source = json.loads(line)
        rows.append(
            {
                "id": source["ID"],
                "name": source["Names"],
                "state": source["State"],
                "status": source["Status"],
                "health": source.get("HealthStatus", "none"),
                "ports": source["Ports"],
            }
        )
    return sorted(rows, key=lambda row: row["name"])


def fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "length": stat.st_size,
        "mtime_utc_ticks": stat.st_mtime_ns // 100 + 621_355_968_000_000_000,
        "wal": Path(f"{path}-wal").exists(),
        "shm": Path(f"{path}-shm").exists(),
        "journal": Path(f"{path}-journal").exists(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--new-project", required=True)
    args = parser.parse_args()
    root = Path(__file__).parents[1].resolve()
    run_dir = root / ".preproduction-assignment-generation" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    new_rows = docker_rows(args.new_project)
    payload = {
        "run_id": args.run_id,
        "captured_at": datetime.now(UTC).isoformat(),
        "status": "PASS" if not new_rows else "FAIL",
        "snapshot_kind": "contemporaneous_pre_operation_read_only_snapshot",
        "protected_projects": {
            project: {"containers": docker_rows(project)} for project in PROTECTED_PROJECTS
        },
        "new_project": {"project": args.new_project, "containers": new_rows},
        "affected_database": fingerprint(root / "ahamark.db"),
        "docker_operations_performed": [],
    }
    (run_dir / "docker-resources-before.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"run_id": args.run_id, "status": payload["status"]}))
    raise SystemExit(0 if payload["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
