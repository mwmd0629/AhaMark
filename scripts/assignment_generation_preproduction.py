"""Pure guards for a new, isolated Stage 6 preproduction project.

This module prepares/validates configuration only.  It never starts or removes
Docker resources.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEGACY_PROJECT = "ahamarkstage4ai233041"
PROJECT_PATTERN = re.compile(r"^(?:ahamarkassignmentv6c|ahamarkassignmentlandingv1)[0-9]{14}$")
SYNTHETIC_DOMAIN = ".synthetic.invalid"
DANGEROUS_COMMANDS = ("down -v", "volume rm", "system prune")


@dataclass(frozen=True)
class Stage6Config:
    run_id: str
    project_name: str
    https_port: int
    postgres_volume: str
    redis_volume: str
    minio_volume: str
    network: str
    bucket: str
    account_marker: str


def validate_config(config: Stage6Config, old_ports: set[int] | None = None) -> list[str]:
    failures: list[str] = []
    marker = (
        re.sub(r"^assignment-generation-(?:v3|landing-v1)-", "", config.run_id)
        .replace("-", "")
        .lower()
    )
    if not PROJECT_PATTERN.fullmatch(config.project_name):
        failures.append("project_name must be a unique Stage 6 name")
    if config.project_name == LEGACY_PROJECT or "stage4" in config.project_name:
        failures.append("legacy Stage 4 project is forbidden")
    if not marker or marker not in config.project_name:
        failures.append("project_name must contain the run marker")
    if not (1024 <= config.https_port <= 65535) or config.https_port in (old_ports or set()):
        failures.append("HTTPS port is invalid or already reserved")
    for name, value in (
        ("postgres_volume", config.postgres_volume),
        ("redis_volume", config.redis_volume),
        ("minio_volume", config.minio_volume),
        ("network", config.network),
        ("bucket", config.bucket),
    ):
        if marker not in value.lower() or "stage4" in value.lower():
            failures.append(f"{name} must contain only the Stage 6 run marker")
    if not config.account_marker.endswith(SYNTHETIC_DOMAIN) or "hr0196" in config.account_marker:
        failures.append("account marker must use a new synthetic.invalid identity")
    return failures


def validate_command(command: str) -> None:
    lowered = " ".join(command.casefold().split())
    if any(item in lowered for item in DANGEROUS_COMMANDS):
        raise ValueError("destructive Docker cleanup is forbidden")


def build_config(run_id: str, https_port: int) -> Stage6Config:
    if not re.fullmatch(r"assignment-generation-(?:v3|landing-v1)-[0-9]{8}-[0-9]{6}", run_id):
        raise ValueError("run_id must use a Stage 6 isolated timestamp format")
    landing = run_id.startswith("assignment-generation-landing-v1-")
    marker = (
        re.sub(r"^assignment-generation-(?:v3|landing-v1)-", "", run_id).replace("-", "").lower()
    )
    return Stage6Config(
        run_id=run_id,
        project_name=(
            f"ahamarkassignmentlandingv1{marker}" if landing else f"ahamarkassignmentv6c{marker}"
        ),
        https_port=https_port,
        postgres_volume=f"agv6_{marker}_postgres",
        redis_volume=f"agv6_{marker}_redis",
        minio_volume=f"agv6_{marker}_minio",
        network=f"agv6_{marker}_network",
        bucket=f"agv6-{marker}",
        account_marker=(
            f"assignment-landing-{marker}@evaluation.synthetic.invalid"
            if landing
            else f"assignment-v6-{marker}@evaluation.synthetic.invalid"
        ),
    )


def write_guard_result(config: Stage6Config, output: Path) -> dict[str, Any]:
    started = datetime.now(UTC)
    failures = validate_config(config)
    completed = datetime.now(UTC)
    result = {
        "run_id": config.run_id,
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "status": "passed" if not failures else "failed",
        "docker_action": "none",
        "legacy_stage4_touched": False,
        "cleanup_performed": False,
        "config": asdict(config),
        "failures": failures,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--https-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = write_guard_result(build_config(args.run_id, args.https_port), args.output)
    print(
        json.dumps({"status": result["status"], "project_name": result["config"]["project_name"]})
    )
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
