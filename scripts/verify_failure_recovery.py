"""Run guarded 7C fault recovery scenarios in a fresh isolated Compose project."""

import argparse
import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

if __package__:
    from scripts.verify_backup_restore import (
        ROOT,
        RecoveryRunner,
        prepare_runner,
        raw_command,
        write_runtime_env,
    )
else:
    from verify_backup_restore import (
        ROOT,
        RecoveryRunner,
        prepare_runner,
        raw_command,
        write_runtime_env,
    )

PROBE = ("python", "-m", "app.cli.failure_recovery_probe")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def docker_engine_check() -> dict[str, Any]:
    version = raw_command(["docker", "version", "--format", "{{.Server.Version}}"]).stdout.strip()
    info = json.loads(raw_command(["docker", "info", "--format", "{{json .}}"]).stdout)
    return {
        "checked_at": utc_now(),
        "server_version": version,
        "engine_id": info["ID"],
        "containers": len(raw_command(["docker", "ps", "-a", "-q"]).stdout.splitlines()),
        "volumes": len(raw_command(["docker", "volume", "ls", "-q"]).stdout.splitlines()),
        "networks": len(raw_command(["docker", "network", "ls", "-q"]).stdout.splitlines()),
    }


def probe(runner: RecoveryRunner, *args: str) -> dict[str, Any]:
    return runner.json_exec("api", *PROBE, *args)


def wait_for(
    read: Callable[[], dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 150,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = read()
        if predicate(last):
            return last
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for state; last={last}")


def service_container(runner: RecoveryRunner, service: str) -> str:
    container_id = runner.run("ps", "--all", "-q", service)
    if not container_id:
        raise RuntimeError(f"{service} container does not exist")
    inspected = json.loads(
        raw_command(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                container_id,
            ]
        ).stdout
    )
    labels = inspected["Config"]["Labels"]
    expected = {
        "com.docker.compose.project": runner.identity.project,
        "com.docker.compose.service": service,
        "com.ahamark.recovery-v7.run-id": runner.identity.run_id,
        "com.ahamark.recovery-v7.project": runner.identity.project,
    }
    if any(labels.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"{service} labels do not match the exact recovery run")
    name = str(inspected["Name"]).lstrip("/")
    if not name.startswith(f"{runner.identity.project}-{service}-"):
        raise RuntimeError(f"{service} container name does not match the exact project")
    return container_id


def service_action(runner: RecoveryRunner, service: str, action: str) -> None:
    if service not in {"worker", "redis", "minio", "api"}:
        raise RuntimeError("service is outside the authorized 7C fault set")
    service_container(runner, service)
    if action == "kill":
        runner.run("kill", "-s", "KILL", service)
    elif action in {"stop", "start", "restart"}:
        runner.run(action, service)
    else:
        raise RuntimeError("unsupported service action")


def wait_healthy(runner: RecoveryRunner, service: str, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container_id = service_container(runner, service)
        state = json.loads(
            raw_command(["docker", "inspect", "--format", "{{json .State}}", container_id]).stdout
        )
        if state.get("Running") and state.get("Health", {}).get("Status") == "healthy":
            return
        time.sleep(2)
    raise RuntimeError(f"{service} did not become healthy")


def service_snapshot(runner: RecoveryRunner, service: str) -> dict[str, Any]:
    container_id = service_container(runner, service)
    inspected = json.loads(
        raw_command(["docker", "inspect", "--format", "{{json .}}", container_id]).stdout
    )
    state = inspected["State"]
    return {
        "service": service,
        "container_id": container_id,
        "name": str(inspected["Name"]).lstrip("/"),
        "status": state["Status"],
        "health": state.get("Health", {}).get("Status"),
        "created": inspected["Created"],
    }


def response_payload(result: dict[str, Any]) -> dict[str, Any]:
    responses = result.get("responses")
    if isinstance(responses, list):
        successful = [item for item in responses if item.get("status_code") in {200, 201}]
        if not successful:
            raise RuntimeError(f"no successful business response: {result}")
        return dict(successful[0]["payload"])
    if result.get("status_code") not in {200, 201}:
        raise RuntimeError(f"business request failed: {result}")
    return dict(result["payload"])


def redelivery_pass(
    completed: dict[str, Any], *, elapsed_seconds: float, configured_seconds: int
) -> bool:
    return (
        completed.get("status") == "completed"
        and int(completed.get("attempt", 0)) >= 2
        and elapsed_seconds >= configured_seconds
    )


def analytics_idempotency_pass(snapshot_ids: set[str]) -> bool:
    return len(snapshot_ids) == 1


def recognition_by_key(runner: RecoveryRunner, key: str) -> dict[str, Any]:
    return probe(runner, "find-recognition", "--key", key)


def report_by_key(runner: RecoveryRunner, key: str) -> dict[str, Any]:
    return probe(runner, "find-report", "--key", key)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--docker-restart-started-at", required=True)
    parser.add_argument("--docker-engine-recovered-at", required=True)
    args = parser.parse_args()
    runner, work = prepare_runner(args.run_id, skip_start=False)
    runner.values["RECOVERY_V7_FAULT_CHECKPOINT"] = "recognition-running,report-before-storage"
    runner.values["RECOVERY_V7_FAULT_DELAY_SECONDS"] = "20"
    write_runtime_env(runner.runtime_env, runner.values)
    raw_path = work / "failure-recovery-raw.json"
    started = time.perf_counter()
    evidence: dict[str, Any] = {
        "schema_version": "1.0",
        "scope": "isolated synthetic single-worker development fault recovery",
        "run_id": runner.identity.run_id,
        "compose_project": runner.identity.project,
        "started_at": utc_now(),
        "docker_restart_started_at": args.docker_restart_started_at,
        "docker_engine_recovered_at": args.docker_engine_recovered_at,
        "scenarios": {},
    }

    runner.run("up", "--build", "-d")
    for service in ("postgres", "redis", "minio", "api", "worker"):
        wait_healthy(runner, service)
    runner.run("exec", "-T", "api", "python", "-m", "app.cli.seed_capacity_demo")
    runner.run("exec", "-T", "api", "python", "-m", "app.cli.seed_capacity_results")
    runner.run("exec", "-T", "api", "python", "-m", "app.cli.seed_recovery_fixture")
    evidence["context"] = probe(runner, "context")

    offline_key = f"{args.run_id}-worker-offline"
    service_action(runner, "worker", "stop")
    created = probe(runner, "create-recognition", "--key", offline_key)
    offline_id = str(response_payload(created)["id"])
    queued = recognition_by_key(runner, offline_key)
    if queued["status"] != "queued":
        raise RuntimeError(f"offline task was not durably queued: {queued}")
    service_action(runner, "worker", "start")
    wait_healthy(runner, "worker")
    offline_completed = wait_for(
        lambda: recognition_by_key(runner, offline_key),
        lambda value: value.get("status") == "completed",
    )
    evidence["scenarios"]["worker_offline_queue"] = {
        "before": "worker healthy",
        "during": queued,
        "after": offline_completed,
        "job_id": offline_id,
        "pass": offline_completed["attempt"] == 1,
    }

    crash_key = f"{args.run_id}-worker-crash"
    crash_created = probe(runner, "create-recognition", "--key", crash_key)
    crash_id = str(response_payload(crash_created)["id"])
    crash_running = wait_for(
        lambda: recognition_by_key(runner, crash_key),
        lambda value: value.get("status") == "running",
    )
    crash_killed_at = utc_now()
    crash_wait_started = time.perf_counter()
    service_action(runner, "worker", "kill")
    crash_during = recognition_by_key(runner, crash_key)
    service_action(runner, "worker", "start")
    wait_healthy(runner, "worker")
    crash_completed = wait_for(
        lambda: recognition_by_key(runner, crash_key),
        lambda value: value.get("status") in {"completed", "failed"},
        timeout=180,
    )
    redelivery_seconds = round(time.perf_counter() - crash_wait_started, 3)
    evidence["scenarios"]["worker_crash_redelivery"] = {
        "before": crash_running,
        "during": crash_during,
        "after": crash_completed,
        "job_id": crash_id,
        "killed_at": crash_killed_at,
        "visibility_timeout_seconds": 15,
        "redelivery_seconds": redelivery_seconds,
        "pass": redelivery_pass(
            crash_completed,
            elapsed_seconds=redelivery_seconds,
            configured_seconds=15,
        ),
    }

    redis_key = f"{args.run_id}-redis-down"
    service_action(runner, "redis", "stop")
    redis_create = probe(runner, "create-recognition", "--key", redis_key)
    redis_failed = recognition_by_key(runner, redis_key)
    service_action(runner, "redis", "start")
    wait_healthy(runner, "redis")
    redis_id = str(redis_failed["id"])
    redis_retry = probe(runner, "retry-recognition", "--job-id", redis_id)
    response_payload(redis_retry)
    redis_completed = wait_for(
        lambda: recognition_by_key(runner, redis_key),
        lambda value: value.get("status") == "completed",
    )
    evidence["scenarios"]["redis_unavailable"] = {
        "create_response": redis_create,
        "during": redis_failed,
        "after": redis_completed,
        "job_id": redis_id,
        "pass": redis_create.get("status_code") == 503
        and redis_failed.get("error_code") == "WORKER_UNAVAILABLE",
    }

    minio_key = f"{args.run_id}-minio-failure"
    service_action(runner, "worker", "stop")
    report_created = probe(
        runner,
        "create-report",
        "--key",
        minio_key,
        "--count",
        "1",
        "--concurrency",
        "1",
    )
    old_report = response_payload(report_created)
    old_id = str(old_report["id"])
    old_before = report_by_key(runner, minio_key)
    service_action(runner, "worker", "start")
    wait_healthy(runner, "worker")
    old_running = wait_for(
        lambda: report_by_key(runner, minio_key),
        lambda value: value.get("status") == "running",
    )
    service_action(runner, "minio", "stop")
    old_failed = wait_for(
        lambda: report_by_key(runner, minio_key),
        lambda value: value.get("status") == "failed",
    )
    service_action(runner, "minio", "start")
    wait_healthy(runner, "minio")
    replacement_response = probe(runner, "retry-report", "--job-id", old_id)
    replacement = response_payload(replacement_response)
    replacement_id = str(replacement["id"])
    replacement_key = str(replacement.get("idempotency_key", ""))
    replacement_completed = wait_for(
        lambda: probe(runner, "get-report", "--job-id", replacement_id),
        lambda value: value.get("payload", {}).get("status") == "completed",
    )
    old_after = report_by_key(runner, minio_key)
    immutable_report_fields = (
        "id",
        "created_at",
        "error_code",
        "grade_release_id",
        "assignment_id",
        "class_id",
        "student_id",
        "report_type",
    )
    old_job_unchanged = all(
        old_failed[field] == old_after[field] for field in immutable_report_fields
    )
    evidence["scenarios"]["minio_report_failure"] = {
        "before": old_before,
        "running_before_minio_stop": old_running,
        "failed": old_failed,
        "old_after_retry": old_after,
        "replacement": replacement_completed,
        "old_job_id": old_id,
        "new_job_id": replacement_id,
        "replacement_key_recorded": bool(replacement_key),
        "old_job_unchanged": old_job_unchanged,
        "pass": old_after["status"] == "failed"
        and old_after["stored_file_id"] is None
        and old_job_unchanged
        and replacement_id != old_id
        and replacement_completed["payload"]["stored_file_id"] is not None,
    }

    recognition_before = recognition_by_key(runner, offline_key)
    probe(
        runner,
        "dispatch-recognition",
        "--job-id",
        offline_id,
        "--count",
        "5",
        "--concurrency",
        "1",
    )
    probe(
        runner,
        "dispatch-recognition",
        "--job-id",
        offline_id,
        "--count",
        "20",
        "--concurrency",
        "20",
    )
    recognition_after = wait_for(
        lambda: recognition_by_key(runner, offline_key),
        lambda value: value.get("status") == "completed",
    )
    evidence["scenarios"]["recognition_duplicate_dispatch"] = {
        "before": recognition_before,
        "after": recognition_after,
        "pass": all(
            recognition_before[key] == recognition_after[key]
            for key in ("page_count", "block_count", "candidate_count")
        ),
    }

    report_key = f"{args.run_id}-report-idempotent"
    report_seq = probe(
        runner,
        "create-report",
        "--key",
        report_key,
        "--count",
        "5",
        "--concurrency",
        "1",
    )
    report_first = response_payload(report_seq)
    report_id = str(report_first["id"])
    wait_for(
        lambda: report_by_key(runner, report_key),
        lambda value: value.get("status") == "completed",
    )
    report_concurrent = probe(
        runner,
        "create-report",
        "--key",
        report_key,
        "--count",
        "20",
        "--concurrency",
        "20",
    )
    probe(
        runner,
        "dispatch-report",
        "--job-id",
        report_id,
        "--count",
        "25",
        "--concurrency",
        "20",
    )
    report_after = wait_for(
        lambda: report_by_key(runner, report_key),
        lambda value: value.get("status") == "completed",
    )
    response_ids = {
        item.get("payload", {}).get("id")
        for item in report_seq["responses"] + report_concurrent["responses"]
        if item.get("status_code") in {200, 201}
    }
    evidence["scenarios"]["report_idempotency"] = {
        "job_id": report_id,
        "successful_response_ids": sorted(value for value in response_ids if value),
        "sequential_statuses": [item["status_code"] for item in report_seq["responses"]],
        "concurrent_statuses": [item["status_code"] for item in report_concurrent["responses"]],
        "after": report_after,
        "pass": response_ids == {report_id} and report_after["stored_file_id"] is not None,
    }

    analytics_seq = probe(
        runner, "analytics", "--release", "s1", "--count", "5", "--concurrency", "1"
    )
    analytics_concurrent = probe(
        runner, "analytics", "--release", "s1", "--count", "20", "--concurrency", "20"
    )
    s1_ids = {
        item.get("payload", {}).get("id")
        for item in analytics_seq["responses"] + analytics_concurrent["responses"]
        if item.get("status_code") in {200, 201}
    }
    evidence["scenarios"]["analytics_idempotency"] = {
        "s1_snapshot_ids": sorted(value for value in s1_ids if value),
        "sequential_statuses": [item["status_code"] for item in analytics_seq["responses"]],
        "concurrent_statuses": [item["status_code"] for item in analytics_concurrent["responses"]],
        "pass": analytics_idempotency_pass({value for value in s1_ids if value}),
    }

    final = wait_for(
        lambda: probe(runner, "final-audit"),
        lambda value: all(
            value.get(key) == 0
            for key in (
                "recognition_queued",
                "recognition_running",
                "report_queued",
                "report_running",
                "celery_active",
                "celery_reserved",
            )
        ),
        timeout=180,
    )
    evidence["final_audit"] = final
    evidence["immutable_grade_state_unchanged"] = (
        final["immutable_grade_hash"] == evidence["context"]["immutable_grade_hash"]
    )
    evidence["teaching_insight_bindings_unchanged"] = (
        final["teaching_insight_bindings_hash"]
        == evidence["context"]["teaching_insight_bindings_hash"]
    )
    all_services = (
        "postgres",
        "postgres_restore",
        "redis",
        "minio",
        "minio-restore",
        "api",
        "worker",
    )
    for service in all_services:
        wait_healthy(runner, service)
    evidence["final_services"] = [service_snapshot(runner, service) for service in all_services]
    first_engine_check = docker_engine_check()
    time.sleep(3)
    second_engine_check = docker_engine_check()
    evidence["final_engine_checks"] = [first_engine_check, second_engine_check]
    evidence["completed_at"] = utc_now()
    evidence["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    final_zero_keys = (
        "duplicate_report_idempotency_keys",
        "duplicate_analytics_snapshots",
        "duplicate_ocr_pages",
        "duplicate_ocr_blocks",
        "duplicate_candidates",
        "duplicate_stored_file_report_references",
        "teaching_insight_owner_mismatch",
    )
    evidence["overall_pass"] = (
        all(bool(value.get("pass")) for value in evidence["scenarios"].values())
        and all(final.get(key) == 0 for key in final_zero_keys)
        and not final["database_records_missing_object"]
        and not final["object_missing_database"]
        and not final["current_run_unknown_orphans"]
        and not final["unable_to_classify"]
        and evidence["immutable_grade_state_unchanged"]
        and evidence["teaching_insight_bindings_unchanged"]
        and all(
            item["status"] == "running" and item["health"] == "healthy"
            for item in evidence["final_services"]
        )
        and first_engine_check["engine_id"] == second_engine_check["engine_id"]
    )
    raw_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    evidence["raw_evidence_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if evidence["overall_pass"]:
        crash = evidence["scenarios"]["worker_crash_redelivery"]
        redis = evidence["scenarios"]["redis_unavailable"]
        minio = evidence["scenarios"]["minio_report_failure"]
        recognition = evidence["scenarios"]["recognition_duplicate_dispatch"]
        report = evidence["scenarios"]["report_idempotency"]
        analytics = evidence["scenarios"]["analytics_idempotency"]
        summary = {
            "schema_version": "1.0",
            "scope": evidence["scope"],
            "run_id": evidence["run_id"],
            "compose_project": evidence["compose_project"],
            "docker_restart_started_at": evidence["docker_restart_started_at"],
            "docker_engine_recovered_at": evidence["docker_engine_recovered_at"],
            "engine_recovery_checks": evidence["final_engine_checks"],
            "scenarios": [
                {
                    "name": "worker_offline_queue_and_recovery",
                    "pass": evidence["scenarios"]["worker_offline_queue"]["pass"],
                    "transition": ["queued", "completed"],
                },
                {
                    "name": "worker_running_checkpoint",
                    "pass": crash["before"]["status"] == "running",
                    "job_id": crash["job_id"],
                },
                {
                    "name": "visibility_timeout",
                    "pass": crash["redelivery_seconds"] >= crash["visibility_timeout_seconds"],
                    "configured_seconds": crash["visibility_timeout_seconds"],
                    "observed_seconds": crash["redelivery_seconds"],
                },
                {
                    "name": "redelivered_running_resume",
                    "pass": crash["pass"],
                    "attempts": crash["after"]["attempt"],
                },
                {
                    "name": "redis_unavailable_result",
                    "pass": redis["create_response"]["status_code"] == 503,
                    "error_code": redis["during"]["error_code"],
                },
                {
                    "name": "redis_recovery_retry",
                    "pass": redis["after"]["status"] == "completed",
                    "job_id": redis["job_id"],
                },
                {
                    "name": "minio_report_write_failure",
                    "pass": minio["failed"]["status"] == "failed",
                    "old_job_id": minio["old_job_id"],
                },
                {
                    "name": "minio_recovery_new_retry_job",
                    "pass": minio["pass"],
                    "old_job_id": minio["old_job_id"],
                    "new_job_id": minio["new_job_id"],
                },
                {
                    "name": "recognition_duplicate_dispatch",
                    "pass": recognition["pass"],
                },
                {
                    "name": "report_idempotency_and_retry",
                    "pass": report["pass"],
                    "job_id": report["job_id"],
                },
                {
                    "name": "analytics_sequential_five",
                    "pass": len(analytics["s1_snapshot_ids"]) == 1
                    and len(analytics["sequential_statuses"]) == 5,
                    "snapshot_ids": analytics["s1_snapshot_ids"],
                },
                {
                    "name": "analytics_concurrent_twenty",
                    "pass": len(analytics["s1_snapshot_ids"]) == 1
                    and len(analytics["concurrent_statuses"]) == 20,
                    "snapshot_ids": analytics["s1_snapshot_ids"],
                },
            ],
            "final_audit": evidence["final_audit"],
            "immutable_grade_state_unchanged": evidence["immutable_grade_state_unchanged"],
            "teaching_insight_bindings_unchanged": evidence["teaching_insight_bindings_unchanged"],
            "final_services": evidence["final_services"],
            "raw_evidence_sha256": evidence["raw_evidence_sha256"],
            "overall_pass": True,
        }
        (ROOT / "docs" / "failure-recovery-verification.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
