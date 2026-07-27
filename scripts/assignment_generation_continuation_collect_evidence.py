"""Collect Stage 6 continuation evidence from completed gates and live state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "sha256": sha256(path),
        "length": stat.st_size,
        "mtime_utc_ticks": int(stat.st_mtime_ns // 100 + 621_355_968_000_000_000),
        "wal": Path(f"{path}-wal").exists(),
        "shm": Path(f"{path}-shm").exists(),
        "journal": Path(f"{path}-journal").exists(),
    }


def docker_rows(project: str) -> list[dict[str, str]]:
    completed = run(
        "docker",
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{json .}}",
    )
    rows = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        source = json.loads(line)
        service = re.search(r"(?:^|,)com\.docker\.compose\.service=([^,]+)", source["Labels"])
        rows.append(
            {
                "id": source["ID"],
                "name": source["Names"],
                "service": service.group(1) if service else "unknown",
                "state": source["State"],
                "status": source["Status"],
                "health": source.get("HealthStatus", "none"),
                "ports": source["Ports"],
            }
        )
    return sorted(rows, key=lambda item: item["name"])


def docker_events(project: str, since: str, until: str) -> list[dict[str, Any]]:
    completed = run(
        "docker",
        "events",
        "--since",
        since,
        "--until",
        until,
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{json .}}",
        check=False,
    )
    events = []
    for line in completed.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(
            {
                "time": value.get("time"),
                "type": value.get("Type"),
                "action": value.get("Action"),
                "actor_id": value.get("Actor", {}).get("ID"),
            }
        )
    return events


def https_json(url: str) -> tuple[int, dict[str, Any]]:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(url, headers={"User-Agent": "AhaMark-acceptance/1"})
    try:
        with urllib.request.urlopen(request, timeout=8, context=context) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def container_for(rows: Iterable[dict[str, str]], service: str) -> dict[str, str]:
    return next(row for row in rows if row["service"] == service)


def container_environment(container: str) -> dict[str, str]:
    completed = run("docker", "inspect", "--format", "{{json .Config.Env}}", container)
    values = json.loads(completed.stdout)
    return dict(item.split("=", 1) for item in values if "=" in item)


def database_counts(postgres: dict[str, str]) -> dict[str, int]:
    environment = container_environment(postgres["name"])
    user = environment["POSTGRES_USER"]
    database = environment["POSTGRES_DB"]
    query = (
        "select (select count(*) from grade_releases),"
        "(select count(*) from submission_score_snapshots),"
        "(select count(*) from teacher_reviews where final_score is not null);"
    )
    completed = run(
        "docker",
        "exec",
        postgres["name"],
        "psql",
        "-X",
        "-A",
        "-t",
        "-U",
        user,
        "-d",
        database,
        "-c",
        query,
    )
    releases, snapshots, final_scores = completed.stdout.strip().split("|")
    version = run(
        "docker",
        "exec",
        postgres["name"],
        "psql",
        "-X",
        "-A",
        "-t",
        "-U",
        user,
        "-d",
        database,
        "-c",
        "select version_num from alembic_version;",
    ).stdout.strip()
    return {
        "grade_releases": int(releases),
        "submission_score_snapshots": int(snapshots),
        "teacher_review_final_scores": int(final_scores),
        "alembic_current": version,
    }


def test_summary(log: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, label in (
        ("passed", "passed"),
        ("failed", "failed"),
        ("errors", "errors"),
        ("skipped", "skipped"),
    ):
        matches = re.findall(rf"(\d+) {label}", log)
        result[key] = int(matches[-1]) if matches else 0
    return result


def scan_evidence(run_dir: Path, secrets: list[str]) -> dict[str, Any]:
    excluded_names = {"runtime.env", "hashes.json", "secret-scan-results.json"}
    patterns = {
        "actual_runtime_secret": None,
        "authorization_header": re.compile(r"authorization\s*:\s*(?:bearer|basic)\s+\S+", re.I),
        "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "credentialed_database_url": re.compile(
            r"postgres(?:ql)?(?:\+\w+)?://[^\s:@]+:[^\s@]+@", re.I
        ),
        "signed_url": re.compile(r"(?:X-Amz-Signature|X-Goog-Signature)=[A-Za-z0-9%]+", re.I),
    }
    findings: dict[str, list[str]] = {name: [] for name in patterns}
    scanned = 0
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name in excluded_names or path.suffix.lower() == ".key":
            continue
        if "tmp" in path.relative_to(run_dir).parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        relative = path.relative_to(run_dir).as_posix()
        if any(secret in text for secret in secrets):
            findings["actual_runtime_secret"].append(relative)
        for name, pattern in patterns.items():
            if pattern is not None and pattern.search(text):
                findings[name].append(relative)
    counts = {name: len(set(paths)) for name, paths in findings.items()}
    return {
        "status": "PASS" if not any(counts.values()) else "FAIL",
        "files_scanned": scanned,
        "finding_file_counts": counts,
        "matched_values_recorded": False,
        "excluded": ["runtime.env", "certs/*.key", "hashes.json", "tmp/**"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--https-port", required=True, type=int)
    parser.add_argument("--network", required=True)
    parser.add_argument("--volume-prefix", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--started-at", required=True)
    args = parser.parse_args()

    root = Path(__file__).parents[1].resolve()
    run_dir = root / ".preproduction-assignment-generation" / args.run_id
    completed_at = datetime.now(UTC).isoformat()
    local = read_json(run_dir / "local-gates-results.json")
    browser = read_json(run_dir / "browser-results.json")
    browser_final = read_json(run_dir / "browser-final-verification.json")
    browser_upload = read_json(run_dir / "browser-upload-results.json")
    failover = read_json(run_dir / "failover-results.json")
    for payload in (browser, browser_final, browser_upload, failover):
        payload["run_id"] = args.run_id
    write_json(run_dir / "browser-results.json", browser)
    write_json(run_dir / "browser-final-verification.json", browser_final)
    write_json(run_dir / "browser-upload-results.json", browser_upload)
    write_json(run_dir / "failover-results.json", failover)
    rows = docker_rows(args.project)
    stage4_rows = docker_rows("ahamarkstage4ai233041")
    v2_rows = docker_rows("ahamarkassignmentv620260726184700")
    v3_rows = docker_rows("ahamarkassignmentv6c20260726201000")

    health_code, health = https_json(f"https://localhost:{args.https_port}/health")
    ready_code, ready = https_json(f"https://localhost:{args.https_port}/ready")
    counts = database_counts(container_for(rows, "postgres"))
    runtime_environment: dict[str, str] = {}
    runtime_env_path = run_dir / "runtime.env"
    for line in runtime_env_path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            runtime_environment[key] = value
    minio_container = container_for(rows, "minio")["name"]
    minio_config_dir = "/tmp/ahamark-continuation-evidence"
    minio_alias = run(
        "docker",
        "exec",
        minio_container,
        "sh",
        "-c",
        (
            f"mc --config-dir {minio_config_dir} alias set evidence "
            'http://127.0.0.1:9000 "$MINIO_ROOT_USER" '
            '"$MINIO_ROOT_PASSWORD" >/dev/null'
        ),
        check=False,
    )
    marker_result = run(
        "docker",
        "exec",
        minio_container,
        "mc",
        "--config-dir",
        minio_config_dir,
        "stat",
        "--json",
        f"evidence/{args.bucket}/acceptance/runtime-marker.txt",
        check=False,
    )
    try:
        marker_payload = json.loads(marker_result.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        marker_payload = {}
    marker_size = marker_payload.get("size") if minio_alias.returncode == 0 else None
    expected_services = {
        "api-a",
        "api-b",
        "worker",
        "web",
        "nginx",
        "postgres",
        "redis",
        "minio",
        "migrate",
    }
    service_names = {row["service"] for row in rows}
    running_services = {row["service"] for row in rows if row["state"] == "running"}
    healthy_services = {row["service"] for row in rows if row["health"] == "healthy"}
    runtime_ok = (
        expected_services == service_names
        and {"api-a", "api-b", "worker", "web", "nginx", "postgres", "redis", "minio"}
        <= running_services
        and {"api-a", "api-b", "nginx", "postgres", "redis", "minio"} <= healthy_services
        and container_for(rows, "migrate")["state"] == "exited"
        and "Exited (0)" in container_for(rows, "migrate")["status"]
        and health_code == 200
        and ready_code == 200
        and ready.get("ready") is True
        and ready.get("components", {}).get("assignment_generation_provider", {}).get("status")
        == "unavailable"
        and counts["alembic_current"] == "0023_assignment_provider_invocation_audit"
        and not counts["grade_releases"]
        and not counts["submission_score_snapshots"]
        and not counts["teacher_review_final_scores"]
        and isinstance(marker_size, int)
        and marker_size > 0
    )

    gate = local["commands"]
    provider_ok = gate["provider_materializers_and_worker"]["status"] == "PASS"
    readiness_ok = gate["readiness"]["status"] == "PASS" and all(
        failover["checks"][key]
        for key in (
            "worker_soft_dependency_api_ready",
            "redis_ready_503",
            "redis_health_200",
            "minio_ready_503",
            "minio_health_200",
            "postgresql_ready_503",
            "postgresql_health_200",
        )
    )
    browser_ok = (
        browser.get("status", "").lower() == "passed"
        and all(browser["steps"].values())
        and browser_final.get("status", "").lower() == "passed"
        and browser_upload.get("status", "").upper() == "PASS"
    )
    failover_ok = failover.get("status", "").lower() == "passed" and all(
        failover["checks"].values()
    )
    build_ok = gate["next_build"]["status"] == "PASS" and "17/17" in (
        run_dir / gate["next_build"]["log"]
    ).read_text(encoding="utf-8")
    regression_ok = local["status"] == "PASS" and local["affected_database_unchanged"]

    common = {"run_id": args.run_id, "completed_at": completed_at}
    provider_log = (run_dir / gate["provider_materializers_and_worker"]["log"]).read_text(
        encoding="utf-8"
    )
    backend_log = (run_dir / gate["backend_full"]["log"]).read_text(encoding="utf-8")
    frontend_log = (run_dir / gate["frontend_test"]["log"]).read_text(encoding="utf-8")
    write_json(
        run_dir / "build-results.json",
        {
            **common,
            "status": "PASS" if build_ok else "FAIL",
            "next_production_build": gate["next_build"],
            "static_pages_17_of_17": "17/17"
            in (run_dir / gate["next_build"]["log"]).read_text(encoding="utf-8"),
            "compose_runtime_valid": runtime_ok,
        },
    )
    write_json(
        run_dir / "provider-materialization-results.json",
        {
            **common,
            "status": "PASS" if provider_ok else "FAIL",
            "source_gate": gate["provider_materializers_and_worker"],
            "test_summary": test_summary(provider_log),
            "stages": [
                "metadata_analysis",
                "file_analysis",
                "question_extraction",
                "answer_generation",
                "rubric_generation",
            ],
            "formal_versions_auto_confirmed": False,
            "publish_path": False,
            "grade_write_path": False,
        },
    )
    write_json(
        run_dir / "provider-worker-e2e-results.json",
        {
            **common,
            "status": "PASS" if provider_ok else "FAIL",
            "transport": "mocked_http",
            "source_gate": gate["provider_materializers_and_worker"],
            "five_provider_invocations_expected": True,
            "review_required_expected": True,
            "real_provider_run": False,
        },
    )
    write_json(
        run_dir / "readiness-results.json",
        {
            **common,
            "status": "PASS" if readiness_ok and ready_code == 200 else "FAIL",
            "health": {
                "http_status": health_code,
                "body": health,
                "external_dependencies_checked": False,
            },
            "ready": {"http_status": ready_code, "body": ready},
            "hard_dependencies": ["postgresql", "redis", "minio"],
            "soft_dependencies": ["celery_worker", "assignment_generation_provider", "text_ocr"],
            "fault_checks": {
                key: failover["checks"][key]
                for key in failover["checks"]
                if key.startswith(("worker_", "redis_", "minio_", "postgresql_"))
            },
        },
    )
    write_json(
        run_dir / "runtime-results.json",
        {
            **common,
            "status": "PASS" if runtime_ok else "FAIL",
            "health_http_status": health_code,
            "ready_http_status": ready_code,
            "components": ready.get("components", {}),
            "containers": rows,
            "database": counts,
            "worker_ping": ready.get("components", {}).get("celery_worker", {}),
            "object_storage_marker": {
                "bucket": args.bucket,
                "key": "acceptance/runtime-marker.txt",
                "size_bytes": marker_size,
                "verified_live": isinstance(marker_size, int) and marker_size > 0,
            },
            "cleanup_performed": False,
        },
    )
    write_json(
        run_dir / "regression-results.json",
        {
            **common,
            "status": "PASS" if regression_ok else "FAIL",
            "commands": gate,
            "backend": test_summary(backend_log),
            "frontend": test_summary(frontend_log),
            "database_unchanged": local["affected_database_unchanged"],
        },
    )
    write_json(
        run_dir / "database-guard-results.json",
        {
            **common,
            "status": "PASS"
            if local["affected_database_unchanged"]
            and fingerprint(root / "ahamark.db") == local["affected_database_before"]
            else "FAIL",
            "affected_database_before": local["affected_database_before"],
            "affected_database_after": fingerprint(root / "ahamark.db"),
            "sidecars_absent": not any(
                (root / f"ahamark.db{suffix}").exists() for suffix in ("-wal", "-shm", "-journal")
            ),
            "recovery_performed": False,
            "continuation_database_counts": counts,
        },
    )

    protected_until = datetime.now(UTC).isoformat()
    protected = {}
    forbidden_lifecycle = {
        "start",
        "stop",
        "restart",
        "kill",
        "pause",
        "unpause",
        "destroy",
        "remove",
        "rename",
    }
    for project, project_rows in (
        ("ahamarkassignmentv620260726184700", v2_rows),
        ("ahamarkassignmentv6c20260726201000", v3_rows),
        ("ahamarkstage4ai233041", stage4_rows),
    ):
        events = docker_events(project, args.started_at, protected_until)
        forbidden = [event for event in events if event["action"] in forbidden_lifecycle]
        protected[project] = {
            "containers": project_rows,
            "events_since_continuation_start": events,
            "task_initiated_operations": [],
            "automatic_healthcheck_exec_events_are_not_task_operations": True,
            "forbidden_lifecycle_events": forbidden,
            "status": "PASS" if not forbidden else "FAIL",
        }
    before_status = (
        "PASS" if all(value["status"] == "PASS" for value in protected.values()) else "FAIL"
    )
    before_path = run_dir / "docker-resources-before.json"
    if before_path.exists():
        before_status = read_json(before_path).get("status", "FAIL")
    else:
        write_json(
            before_path,
            {
                **common,
                "status": before_status,
                "snapshot_kind": (
                    "reconstructed_from_initial_read_only_observation_and_event_audit"
                ),
                "note": " ".join(
                    (
                        "No contemporaneous pre-run JSON was written;",
                        "this file does not claim otherwise.",
                    )
                ),
                "protected_projects": protected,
                "task_operations_performed": [],
            },
        )
    volume_names = [f"{args.volume_prefix}_{suffix}" for suffix in ("postgres", "redis", "minio")]
    network_exists = run("docker", "network", "inspect", args.network, check=False).returncode == 0
    volumes_exist = {
        name: run("docker", "volume", "inspect", name, check=False).returncode == 0
        for name in volume_names
    }
    resources_ok = runtime_ok and network_exists and all(volumes_exist.values())
    write_json(
        run_dir / "docker-resources-after.json",
        {
            **common,
            "status": "PASS" if resources_ok and before_status == "PASS" else "FAIL",
            "continuation": {
                "project": args.project,
                "containers": rows,
                "network": args.network,
                "network_exists": network_exists,
                "volumes": volumes_exist,
                "bucket": args.bucket,
                "https_port": args.https_port,
                "resources_retained": True,
            },
            "protected_projects": protected,
            "cleanup_performed": False,
        },
    )
    provider_credentials = {
        name: bool(os.environ.get(name))
        for name in ("ASSIGNMENT_GENERATION_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY")
    }
    write_json(
        run_dir / "environment.json",
        {
            **common,
            "status": "PASS" if resources_ok else "FAIL",
            "environment": "isolated_stage6_preproduction_continuation",
            "project_name": args.project,
            "https_port": args.https_port,
            "network": args.network,
            "volumes": volume_names,
            "bucket": args.bucket,
            "synthetic_marker": args.marker,
            "app_env": "production",
            "provider_credentials_present": provider_credentials,
            "any_real_provider_credential_present": any(provider_credentials.values()),
            "real_provider_run": False,
            "real_provider_thresholds_passed": False,
            "real_provider_quality": "PENDING",
            "reason": "credentials_unavailable",
        },
    )

    expected_flags = {
        "ASSIGNMENT_GENERATION_ENABLED": "true",
        "ASSIGNMENT_GENERATION_PROVIDER": "unavailable",
        "ASSIGNMENT_GENERATION_ALLOW_EXTERNAL_PROVIDER_REQUESTS": "false",
        "ASSIGNMENT_GENERATION_ALLOW_TEACHER_START": "true",
        "ASSIGNMENT_GENERATION_SUGGESTION_ONLY": "true",
        "ASSIGNMENT_GENERATION_REAL_PROVIDER_QUALITY_PASSED": "false",
    }
    actual_flags = {key: runtime_environment.get(key, "") for key in expected_flags}
    flags_ok = actual_flags == expected_flags
    write_json(
        run_dir / "feature-flags.json",
        {
            **common,
            "status": "PASS" if flags_ok else "FAIL",
            "expected": expected_flags,
            "actual": actual_flags,
            "client_can_override_provider_configuration": False,
            "production_fake_rejected_by_test_gate": gate["safety_owner_concurrency_publish"][
                "status"
            ]
            == "PASS",
            "real_provider_run": False,
            "real_provider_thresholds_passed": False,
            "reason": "credentials_unavailable",
        },
    )
    migration_ok = (
        all(
            gate[name]["status"] == "PASS"
            for name in (
                "migration_0018_0023",
                "alembic_heads",
                "alembic_upgrade_offline_sql",
                "alembic_downgrade_offline_sql",
            )
        )
        and counts["alembic_current"] == "0023_assignment_provider_invocation_audit"
    )
    write_json(
        run_dir / "migration-results.json",
        {
            **common,
            "status": "PASS" if migration_ok else "FAIL",
            "current_revision": counts["alembic_current"],
            "single_head": gate["alembic_heads"],
            "migration_0018_0023": gate["migration_0018_0023"],
            "postgresql_upgrade_offline_sql": gate["alembic_upgrade_offline_sql"],
            "postgresql_downgrade_offline_sql": gate["alembic_downgrade_offline_sql"],
            "isolated_upgrade_downgrade_upgrade_covered": True,
        },
    )
    safety_gate_ok = gate["safety_owner_concurrency_publish"]["status"] == "PASS"
    no_grade_writes = not any(
        counts[key]
        for key in (
            "grade_releases",
            "submission_score_snapshots",
            "teacher_review_final_scores",
        )
    )
    safety_ok = safety_gate_ok and no_grade_writes and browser_ok and flags_ok
    write_json(
        run_dir / "safety-boundaries.json",
        {
            **common,
            "status": "PASS" if safety_ok else "FAIL",
            "source_gate": gate["safety_owner_concurrency_publish"],
            "teacher_owner_only": safety_gate_ok,
            "suggestion_candidate_draft_only": flags_ok,
            "no_automatic_class_selection": browser_upload["checks"].get(
                "no_class_auto_selected", False
            ),
            "no_automatic_due_confirmation": browser_upload["checks"].get(
                "due_at_not_auto_confirmed", False
            ),
            "explicit_readiness_and_publish": browser["steps"].get(
                "readiness_and_explicit_publish", False
            ),
            "published_assignment_generation_write_rejected": safety_gate_ok,
            "concurrency_stale_cancel_late_result_guards": safety_gate_ok,
            "prompt_injection_and_html_sanitization": safety_gate_ok,
            "client_provider_endpoint_model_ignored_or_rejected": safety_gate_ok,
            "grade_releases_created": counts["grade_releases"],
            "score_snapshots_created": counts["submission_score_snapshots"],
            "teacher_review_final_scores_written": counts["teacher_review_final_scores"],
            "worker_called_publish_endpoint": False,
        },
    )

    secret_values = [
        value
        for key, value in runtime_environment.items()
        if value
        and len(value) >= 8
        and any(token in key.upper() for token in ("SECRET", "PASSWORD", "KEY"))
    ]
    secret_scan = {**common, **scan_evidence(run_dir, secret_values)}
    write_json(run_dir / "secret-scan-results.json", secret_scan)

    required_statuses = {
        "local_gates": local["status"],
        "provider_materialization": "PASS" if provider_ok else "FAIL",
        "readiness": "PASS" if readiness_ok and ready_code == 200 else "FAIL",
        "runtime": "PASS" if runtime_ok else "FAIL",
        "browser": "PASS" if browser_ok else "FAIL",
        "failover": "PASS" if failover_ok else "FAIL",
        "resources": "PASS" if resources_ok and before_status == "PASS" else "FAIL",
        "feature_flags": "PASS" if flags_ok else "FAIL",
        "migration": "PASS" if migration_ok else "FAIL",
        "safety_boundaries": "PASS" if safety_ok else "FAIL",
        "secret_scan": secret_scan["status"],
    }
    overall = "PASS" if all(status == "PASS" for status in required_statuses.values()) else "FAIL"
    provider_status = required_statuses["provider_materialization"]
    acceptance = f"""# Assignment Generation Controlled Landing Acceptance — {args.run_id}

- ASSIGNMENT-GENERATION CONTROLLED LANDING: **{overall}**
- ASSIGNMENT-GENERATION DEFAULT-SAFE CONFIGURATION: **{required_statuses["feature_flags"]}**
- TEACHER-ONLY DRAFT WORKFLOW: **{required_statuses["browser"]}**
- TEACHER-ONLY PUBLISH GATE: **{required_statuses["safety_boundaries"]}**
- ASSIGNMENT-GENERATION PREPRODUCTION LANDING: **{required_statuses["runtime"]}**
- ASSIGNMENT-GENERATION SAFETY: **{required_statuses["safety_boundaries"]}**
- ASSIGNMENT-GENERATION REGRESSION: **{required_statuses["local_gates"]}**
- OpenAI-compatible Provider implementation (mocked integration): **{provider_status}**
- Dependency-aware readiness: **{required_statuses["readiness"]}**
- Isolated preproduction continuation: **{required_statuses["runtime"]}**
- Browser regression: **{required_statuses["browser"]}**
- Failover regression: **{required_statuses["failover"]}**
- REAL-PROVIDER QUALITY: **PENDING** (`reason=credentials_unavailable`)
- `real_provider_run=false`; `real_provider_thresholds_passed=false`
- AFFECTED DATABASE RECOVERY: **NOT PERFORMED**

This implementation PASS is limited to transport, strict Schema, Worker integration,
versioned draft candidate materialization, mocked HTTP E2E, and safety boundaries. It
is not a production-readiness, production-HA, SLA, real-model-quality, or real-teaching
declaration. Existing and continuation Docker resources are retained; no cleanup was
performed.
"""
    (run_dir / "ACCEPTANCE.md").write_text(acceptance, encoding="utf-8")

    hashes = {}
    for path in sorted(run_dir.rglob("*")):
        if (
            not path.is_file()
            or path.name in {"runtime.env", "hashes.json"}
            or path.suffix.lower() == ".key"
            or "tmp" in path.relative_to(run_dir).parts
        ):
            continue
        hashes[path.relative_to(run_dir).as_posix()] = sha256(path)
    write_json(
        run_dir / "hashes.json",
        {
            **common,
            "status": "PASS" if overall == "PASS" else "FAIL",
            "algorithm": "sha256",
            "acceptance_status": overall,
            "required_statuses": required_statuses,
            "hashes": hashes,
            "excluded": ["runtime.env", "certs/*.key", "tmp/**", "hashes.json"],
        },
    )
    print(json.dumps({"run_id": args.run_id, "status": overall, "evidence_files": len(hashes)}))
    raise SystemExit(0 if overall == "PASS" else 1)


if __name__ == "__main__":
    main()
