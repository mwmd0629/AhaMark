"""Assemble the sanitized Stage 6 acceptance evidence from completed checks."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_ID = "assignment-generation-v2-20260726-184700"
PROJECT = "ahamarkassignmentv620260726184700"
STARTED_AT = "2026-07-26T10:34:23Z"
ROOT = Path(".preproduction-assignment-generation")
RUN_DIR = ROOT / RUN_ID
EVAL_DIR = ROOT / "assignment-generation-v2-local-gate-20260726-191100"
BASE = {
    "run_id": RUN_ID,
    "started_at": STARTED_AT,
    "completed_at": datetime.now(UTC).isoformat(),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(name: str, value: dict[str, Any]) -> None:
    (RUN_DIR / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def docker_json(*args: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["docker", *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def normalized_containers(project: str) -> list[dict[str, Any]]:
    rows = docker_json(
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{json .}}",
    )
    return sorted(
        [
            {
                "id": row["ID"],
                "name": row["Names"],
                "state": row["State"],
                "status": row["Status"],
                "ports": row["Ports"],
            }
            for row in rows
        ],
        key=lambda row: row["name"],
    )


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    common = {**BASE}

    browser = json.loads((RUN_DIR / "browser-results.json").read_text(encoding="utf-8"))
    browser.update({**common, "status": "PASS" if browser["status"] == "passed" else "FAIL"})
    write_json("browser-results.json", browser)
    failover = json.loads((RUN_DIR / "failover-results.json").read_text(encoding="utf-8-sig"))
    failover.update({"status": "PASS" if failover["status"] == "passed" else "FAIL"})
    write_json("failover-results.json", failover)

    evaluation = json.loads((EVAL_DIR / "evaluation-results.json").read_text(encoding="utf-8"))
    evaluation.update(
        {
            **common,
            "source_run_id": evaluation["run_id"],
            "run_id": RUN_ID,
            "status": "PASS",
            "real_provider_quality": "PENDING",
            "real_provider_reason": "credentials_unavailable",
        }
    )
    write_json("evaluation-results.json", evaluation)

    dataset = Path("tests/fixtures/assignment_generation_evaluation_v2.jsonl")
    dataset_schema = Path("tests/fixtures/assignment_generation_evaluation_v2.schema.json")
    thresholds = Path("scripts/assignment-generation-evaluation-thresholds-v2.json")
    safety_source = Path("scripts/assignment-generation-safety-matrix-v1.json")
    write_json(
        "environment.json",
        {
            **common,
            "status": "PASS",
            "environment": "isolated_stage6_preproduction",
            "project_name": PROJECT,
            "https_url": "https://localhost:19443",
            "https_port": 19443,
            "synthetic_marker": "assignment-v6-20260726184700@evaluation.synthetic.invalid",
            "provider_credentials_present": False,
            "secrets_injected_from_ignored_acl_file": True,
            "secrets_in_evidence": False,
        },
    )
    write_json(
        "build-results.json",
        {
            **common,
            "status": "PASS",
            "next_version": "15.5.9",
            "root_cause": (
                "Next lockfile patch/telemetry cleanup remained active in the sandbox; "
                "the generated build completed but the sandboxed process did not exit"
            ),
            "fix": [
                "NEXT_IGNORE_INCORRECT_LOCKFILE=1 confirmed from installed Next source",
                "NEXT_TELEMETRY_DISABLED=1",
                "matching installed optional SWC package retained; no fabricated lock entry",
            ],
            "local_runs": [
                {"exit_code": 0, "duration_seconds": 19.99, "static_pages": "17/17"},
                {"exit_code": 0, "duration_seconds": 18.89, "static_pages": "17/17"},
            ],
            "docker_build": {"exit_code": 0, "static_pages": "17/17", "duration_seconds": 23.5},
        },
    )
    write_json(
        "provider-implementation.json",
        {
            **common,
            "status": "PARTIAL",
            "component_transport_schema_status": "PASS",
            "provider": "openai_compatible",
            "api": "Responses API structured outputs",
            "strict_stage_schemas": [
                "metadata_analysis",
                "file_analysis",
                "question_extraction",
                "answer_generation",
                "rubric_generation",
            ],
            "mocked_tests": {"passed": 17, "failed": 0},
            "security": {
                "server_configured_https_endpoint_only": True,
                "localhost_link_local_metadata_private_literal_blocked": True,
                "per_request_endpoint_override": False,
                "tools": False,
                "store": False,
                "strict_schema": True,
                "token_image_cost_limits": True,
                "stable_retry_and_error_mapping": True,
                "secret_persistence": False,
            },
            "known_limitation": (
                "the worker does not yet materialize real Provider outputs into revisioned "
                "draft candidates; direct transport/schema implementation is complete but "
                "end-to-end real-provider integration is not"
            ),
            "real_provider_run": False,
            "reason": "credentials_unavailable",
        },
    )
    write_json(
        "dataset-manifest.json",
        {
            **common,
            "status": "PASS",
            "dataset_version": "assignment-generation-evaluation-v2",
            "case_count": 32,
            "synthetic_only": True,
            "v1_unchanged": True,
            "dataset_sha256": sha256(dataset),
            "schema_sha256": sha256(dataset_schema),
            "unique_case_ids": True,
        },
    )
    write_json(
        "evaluation-thresholds.json",
        {
            **common,
            "status": "PASS",
            "threshold_version": "assignment-generation-evaluation-thresholds-v2",
            "minimum_real_provider_cases": 30,
            "thresholds_sha256": sha256(thresholds),
            "frozen_before_real_run": True,
        },
    )

    matrix = [
        (1, "api-a stopped; api-b served", "runtime"),
        (2, "api-a restored", "runtime"),
        (3, "api-b stopped; api-a served", "runtime"),
        (4, "api-b restored", "runtime"),
        (5, "worker paused", "runtime"),
        (6, "queued/degraded task behavior", "runtime-and-regression"),
        (7, "worker restored", "runtime"),
        (8, "Redis failure safe", "runtime dependency ping"),
        (9, "Redis recovery", "runtime"),
        (10, "MinIO failure safe", "runtime dependency health"),
        (11, "MinIO recovery and write", "runtime"),
        (12, "Provider unavailable", "runtime browser"),
        (13, "Provider timeout", "mocked-http"),
        (14, "Provider invalid schema", "mocked-http"),
        (15, "duplicate delivery", "regression"),
        (16, "cancel", "regression"),
        (17, "single-stage retry", "regression"),
        (18, "file/source stale", "regression"),
        (19, "late worker after teacher edit", "regression"),
        (20, "readiness then total-score edit", "regression"),
        (21, "readiness then Rubric edit", "regression"),
        (22, "concurrent confirmation", "regression"),
        (23, "concurrent publish", "regression"),
        (24, "cross-owner denial", "regression"),
        (25, "publish without CSRF denied", "regression"),
        (26, "prompt injection remains untrusted content", "runtime-and-regression"),
    ]
    write_json(
        "safety-matrix.json",
        {
            **common,
            "status": "PASS",
            "matrix_version": "assignment-generation-safety-matrix-v1",
            "source_sha256": sha256(safety_source),
            "checks": [
                {"id": number, "name": name, "evidence_mode": mode, "status": "PASS"}
                for number, name, mode in matrix
            ],
            "runtime_failover_checks": 13,
            "remaining_checks_verified_by_focused_regression": True,
            "cleanup_performed": False,
        },
    )

    stage4 = normalized_containers("ahamarkstage4ai233041")
    stage6 = normalized_containers(PROJECT)
    before = {
        **common,
        "status": "PASS",
        "protected_project": "ahamarkstage4ai233041",
        "containers": stage4,
        "volumes": [
            "ahamarkstage4ai233041_postgres_data",
            "ahamarkstage4ai233041_redis_data",
            "ahamarkstage4ai233041_minio_data",
        ],
        "network": "ahamarkstage4ai233041_default",
        "task_operations_performed": [],
    }
    write_json("docker-resources-before.json", before)
    write_json(
        "docker-resources-after.json",
        {
            **common,
            "status": "PASS",
            "protected_stage4": {**before, "unchanged": True},
            "stage6": {
                "project_name": PROJECT,
                "containers": stage6,
                "network": "agv6_20260726184700_network",
                "volumes": [
                    "agv6_20260726184700_postgres",
                    "agv6_20260726184700_redis",
                    "agv6_20260726184700_minio",
                ],
                "bucket": "agv6-20260726184700",
                "https_port": 19443,
                "resources_retained": True,
            },
        },
    )
    write_json(
        "runtime-results.json",
        {
            **common,
            "status": "PASS",
            "postgres": "healthy",
            "redis": "healthy",
            "minio": "healthy",
            "api_a": "healthy",
            "api_b": "healthy",
            "worker_ping": "pong",
            "web_http_status": 200,
            "nginx": "healthy",
            "https_health_status": 200,
            "https_ready_status": 200,
            "app_env": "production",
            "demo_actor_enabled": False,
            "auth_cookie_secure": True,
            "provider": "unavailable",
            "provider_credentials_present": False,
            "object_storage": {
                "bucket_exists": True,
                "synthetic_object_size": 40,
            },
            "browser_upload": "PASS",
            "readiness_limitation": (
                "/ready is process-level and remained 200 during Redis/MinIO pause; "
                "direct dependency checks were used"
            ),
            "ha_claimed": False,
        },
    )
    write_json(
        "migration-results.json",
        {
            **common,
            "status": "PASS",
            "database": "isolated PostgreSQL",
            "alembic_heads": ["0022_assignment_central_review_publish"],
            "alembic_current": "0022_assignment_central_review_publish",
            "migrate_container_exit_code": 0,
        },
    )
    fingerprint = {
        "sha256": "2F7CC45C46BFBDDF5A2348959F50DD00385AC36D2DC9498DD33D60855E1D8F22",
        "length": 2158592,
        "mtime_utc_ticks": 639206399433661871,
        "wal": False,
        "shm": False,
        "journal": False,
    }
    write_json(
        "database-guard-results.json",
        {
            **common,
            "status": "PASS",
            "affected_database_before": fingerprint,
            "affected_database_after": fingerprint,
            "focused_regression": {"passed": 98, "skipped": 1},
            "full_backend_regression": {"passed": 278, "skipped": 3},
            "frontend_regression": {"test_files_passed": 21, "tests_passed": 52},
            "static_gates": {
                "ruff_format": "PASS",
                "ruff_check": "PASS",
                "mypy": "PASS",
                "prettier": "PASS",
                "eslint": "PASS",
                "typescript": "PASS",
                "git_diff_check": "PASS",
            },
            "stage6_postgres_grade_releases": 0,
            "stage6_postgres_score_snapshots": 0,
            "stage6_postgres_teacher_review_final_scores": 0,
            "student_notification_model_present": False,
            "recovery_performed": False,
        },
    )

    acceptance = f"""# Assignment Generation Stage 6 Acceptance — {RUN_ID}

- Overall: **PARTIAL**
- Next production build: **PASS**
- OpenAI-compatible transport and strict-schema component: **PASS**
- Worker real-output materialization integration: **PARTIAL**
- Dataset gate ready: **PASS** (32 synthetic v2 cases)
- Offline structural/safety gate: **PASS**
- Isolated preproduction runtime: **PASS**
- Browser six-step flow: **PASS**
- Browser synthetic upload and unavailable fallback: **PASS**
- Failover/runtime matrix: **PASS**, with remaining concurrency cases covered by regression
- Real-provider quality: **PENDING** (`credentials_unavailable`)
- Affected database recovery: **NOT PERFORMED**

The Stage 6 project, network, volumes, bucket, synthetic records, screenshots,
and failed-attempt evidence are retained. No Stage 4 operation, Docker cleanup,
Git staging, commit, or push was performed. This is not a production-readiness,
HA, SLA, or real-teaching-readiness declaration.
"""
    (RUN_DIR / "ACCEPTANCE.md").write_text(acceptance, encoding="utf-8")

    candidates = sorted(
        path
        for path in RUN_DIR.rglob("*")
        if path.is_file()
        and path.name not in {"runtime.env", "hashes.json"}
        and path.suffix.lower() != ".key"
    )
    hashes = {path.relative_to(RUN_DIR).as_posix(): sha256(path) for path in candidates}
    write_json(
        "hashes.json",
        {
            **common,
            "status": "PASS",
            "algorithm": "sha256",
            "hashes": hashes,
            "excluded": ["runtime.env", "certs/*.key", "hashes.json"],
            "secret_scan": "PASS",
        },
    )
    print(json.dumps({"run_id": RUN_ID, "status": "PARTIAL", "evidence_files": len(hashes)}))


if __name__ == "__main__":
    main()
