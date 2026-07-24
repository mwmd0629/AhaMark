"""Run fail-closed PostgreSQL and MinIO recovery checks in a unique v7 stack."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.cli.recovery_v7_guard import (  # noqa: E402
    RecoveryGuardError,
    RecoveryIdentity,
    identity_for_run,
    require_recovery_environment,
)

COMPOSE_FILE = ROOT / "docker-compose.recovery.yml"
WORK_ROOT = ROOT / ".recovery-v7"
EXPECTED_SERVICES = frozenset(
    {"postgres", "postgres_restore", "redis", "minio", "minio-restore", "api", "worker"}
)
VOLUME_KEYS = (
    "postgres_source_data",
    "postgres_restore_data",
    "redis_data",
    "minio_source_data",
    "minio_restore_data",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def runtime_values(identity: RecoveryIdentity) -> dict[str, str]:
    """Generate per-run credentials without returning or printing them as evidence."""

    return {
        "APP_ENV": "test",
        "RECOVERY_V7_ENABLED": "true",
        "RECOVERY_V7_RUN_ID": identity.run_id,
        "RECOVERY_V7_COMPOSE_PROJECT": identity.project,
        "RECOVERY_SOURCE_DATABASE": identity.source_database,
        "RECOVERY_RESTORED_DATABASE": identity.restored_database,
        "RECOVERY_SOURCE_BUCKET": identity.source_bucket,
        "RECOVERY_RESTORED_BUCKET": identity.restored_bucket,
        "RECOVERY_POSTGRES_USER": f"rv7_{identity.run_id.replace('-', '_')}",
        "RECOVERY_POSTGRES_PASSWORD": secrets.token_hex(32),
        "RECOVERY_MINIO_ACCESS_KEY": f"rv7{secrets.token_hex(12)}",
        "RECOVERY_MINIO_SECRET_KEY": secrets.token_hex(32),
    }


def write_runtime_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    path.chmod(0o600)


def read_runtime_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RecoveryGuardError("runtime.env is missing for --skip-start")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if not separator or not key:
            raise RecoveryGuardError("runtime.env contains an invalid line")
        values[key] = value
    return values


def assert_work_directory_absent(path: Path) -> None:
    if path.exists():
        raise RecoveryGuardError(f"recovery work directory already exists: {path}")


def raw_command(
    command: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=check,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        env=env,
    )


def docker_output(*args: str, check: bool = True) -> str:
    result = raw_command(["docker", *args], check=check)
    return result.stdout.strip()


def assert_no_existing_resources(identity: RecoveryIdentity) -> None:
    projects_raw = docker_output("compose", "ls", "-a", "--format", "json")
    projects = json.loads(projects_raw or "[]")
    if any(project.get("Name") == identity.project for project in projects):
        raise RecoveryGuardError("Compose project already exists")
    containers = docker_output(
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={identity.project}",
        "--format",
        "{{.ID}}",
    )
    if containers:
        raise RecoveryGuardError("containers already exist for this recovery project")
    for service in EXPECTED_SERVICES:
        container_name = f"{identity.project}-{service}-1"
        inspected = raw_command(["docker", "container", "inspect", container_name], check=False)
        if inspected.returncode == 0:
            raise RecoveryGuardError(f"same-name container already exists: {container_name}")
    for volume_key in VOLUME_KEYS:
        volume_name = f"{identity.project}_{volume_key}"
        inspected = raw_command(
            ["docker", "volume", "inspect", volume_name],
            check=False,
        )
        if inspected.returncode == 0:
            raise RecoveryGuardError(f"volume already exists: {volume_name}")
    network_name = f"{identity.project}_default"
    network = raw_command(["docker", "network", "inspect", network_name], check=False)
    if network.returncode == 0:
        raise RecoveryGuardError(f"network already exists: {network_name}")


def assert_existing_stack_identity(identity: RecoveryIdentity) -> None:
    lines = docker_output(
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={identity.project}",
        "--format",
        '{{.Label "com.ahamark.recovery-v7.run-id"}}|{{.Label "com.docker.compose.service"}}',
    ).splitlines()
    identities: dict[str, str] = {}
    for line in lines:
        run_id, separator, service = line.partition("|")
        if not separator:
            raise RecoveryGuardError("existing container lacks recovery identity labels")
        identities[service] = run_id
    if set(identities) != EXPECTED_SERVICES:
        raise RecoveryGuardError("existing stack service set does not match this recovery run")
    if any(run_id != identity.run_id for run_id in identities.values()):
        raise RecoveryGuardError("existing stack Run ID label mismatch")
    for volume_key in VOLUME_KEYS:
        volume_name = f"{identity.project}_{volume_key}"
        result = raw_command(
            [
                "docker",
                "volume",
                "inspect",
                volume_name,
                "--format",
                '{{index .Labels "com.ahamark.recovery-v7.run-id"}}',
            ],
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != identity.run_id:
            raise RecoveryGuardError(f"volume identity mismatch: {volume_name}")


@dataclass
class RecoveryRunner:
    identity: RecoveryIdentity
    runtime_env: Path
    values: dict[str, str]

    @property
    def compose(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(self.runtime_env),
            "-p",
            self.identity.project,
            "-f",
            str(COMPOSE_FILE),
        ]

    def run(self, *args: str) -> str:
        result = raw_command([*self.compose, *args])
        return result.stdout.strip()

    def json_exec(
        self,
        service: str,
        *command: str,
        env: list[str] | None = None,
    ) -> dict[str, Any]:
        args = ["exec", "-T"]
        for item in env or []:
            args.extend(["-e", item])
        args.extend([service, *command])
        return json.loads(self.run(*args).splitlines()[-1])


def restored_database_url(identity: RecoveryIdentity, values: dict[str, str]) -> str:
    return (
        f"postgresql+psycopg://{values['RECOVERY_POSTGRES_USER']}:"
        f"{values['RECOVERY_POSTGRES_PASSWORD']}@postgres_restore:5432/"
        f"{identity.restored_database}"
    )


def assert_empty_restored_database(runner: RecoveryRunner) -> None:
    identity = runner.identity
    current_name = runner.run(
        "exec",
        "-T",
        "postgres_restore",
        "psql",
        "-U",
        runner.values["RECOVERY_POSTGRES_USER"],
        "-d",
        identity.restored_database,
        "-Atc",
        "select current_database();",
    )
    if current_name != identity.restored_database:
        raise RecoveryGuardError("restore connection reached an unexpected database")
    business_tables = runner.run(
        "exec",
        "-T",
        "postgres_restore",
        "psql",
        "-U",
        runner.values["RECOVERY_POSTGRES_USER"],
        "-d",
        identity.restored_database,
        "-Atc",
        "select count(*) from pg_tables where schemaname='public';",
    )
    if business_tables != "0":
        raise RecoveryGuardError("restored database is not empty")


def prepare_runner(run_id: str, *, skip_start: bool) -> tuple[RecoveryRunner, Path]:
    identity = identity_for_run(run_id)
    work = WORK_ROOT / run_id
    runtime_env = work / "runtime.env"
    if skip_start:
        values = read_runtime_env(runtime_env)
        require_recovery_environment(
            {
                **values,
                "DATABASE_URL": (
                    f"postgresql+psycopg://{values.get('RECOVERY_POSTGRES_USER', '')}:"
                    f"{values.get('RECOVERY_POSTGRES_PASSWORD', '')}@postgres:5432/"
                    f"{values.get('RECOVERY_SOURCE_DATABASE', '')}"
                ),
                "MINIO_BUCKET": values.get("RECOVERY_SOURCE_BUCKET", ""),
            }
        )
        runner = RecoveryRunner(identity, runtime_env, values)
        assert_existing_stack_identity(identity)
        return runner, work
    assert_work_directory_absent(work)
    assert_no_existing_resources(identity)
    values = runtime_values(identity)
    guard_values = {
        **values,
        "DATABASE_URL": (
            f"postgresql+psycopg://{values['RECOVERY_POSTGRES_USER']}:"
            f"{values['RECOVERY_POSTGRES_PASSWORD']}@postgres:5432/"
            f"{identity.source_database}"
        ),
        "MINIO_BUCKET": identity.source_bucket,
    }
    require_recovery_environment(guard_values)
    work.mkdir(parents=True)
    write_runtime_env(runtime_env, values)
    return RecoveryRunner(identity, runtime_env, values), work


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--skip-start",
        action="store_true",
        help="Use only with an existing stack carrying the exact same Run ID labels.",
    )
    args = parser.parse_args()
    runner, work = prepare_runner(args.run_id, skip_start=args.skip_start)
    identity = runner.identity
    dump = work / f"{identity.project}.dump"
    evidence_path = work / "backup-restore-verification.json"
    elapsed_started = time.perf_counter()
    if not args.skip_start:
        runner.run("up", "--build", "-d")
    runner.run("exec", "-T", "api", "python", "-m", "app.cli.seed_capacity_demo")
    runner.run("exec", "-T", "api", "python", "-m", "app.cli.seed_capacity_results")
    runner.run("exec", "-T", "api", "python", "-m", "app.cli.seed_recovery_fixture")
    fixture_last_write_at = utc_now()
    source_before = runner.json_exec(
        "api", "python", "-m", "app.cli.reconcile_recovery", "database"
    )
    version = runner.run(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        runner.values["RECOVERY_POSTGRES_USER"],
        "-d",
        identity.source_database,
        "-Atc",
        "select version();",
    )
    backup_started_at = utc_now()
    backup_started = time.perf_counter()
    container_dump = f"/tmp/{identity.project}.dump"
    runner.run(
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        runner.values["RECOVERY_POSTGRES_USER"],
        "-d",
        identity.source_database,
        "-Fc",
        "-f",
        container_dump,
    )
    source_container = runner.run("ps", "-q", "postgres")
    raw_command(["docker", "cp", f"{source_container}:{container_dump}", str(dump)])
    backup_seconds = time.perf_counter() - backup_started
    backup_completed_at = utc_now()
    source_at_backup_end = runner.json_exec(
        "api", "python", "-m", "app.cli.reconcile_recovery", "database"
    )
    writes_during_backup = source_before["stable_hash"] != source_at_backup_end["stable_hash"]

    assert_empty_restored_database(runner)
    restore_container = runner.run("ps", "-q", "postgres_restore")
    raw_command(["docker", "cp", str(dump), f"{restore_container}:{container_dump}"])
    restore_started = time.perf_counter()
    runner.run(
        "exec",
        "-T",
        "postgres_restore",
        "pg_restore",
        "-U",
        runner.values["RECOVERY_POSTGRES_USER"],
        "-d",
        identity.restored_database,
        "--no-owner",
        "--no-privileges",
        "--single-transaction",
        container_dump,
    )
    restore_seconds = time.perf_counter() - restore_started
    restore_completed_at = utc_now()
    restored_url = restored_database_url(identity, runner.values)
    restored = runner.json_exec(
        "api",
        "python",
        "-m",
        "app.cli.reconcile_recovery",
        "database",
        env=[f"DATABASE_URL={restored_url}"],
    )
    source_after = runner.json_exec("api", "python", "-m", "app.cli.reconcile_recovery", "database")
    objects = runner.json_exec(
        "api",
        "python",
        "-m",
        "app.cli.reconcile_recovery",
        "objects-copy",
        env=[
            "RECOVERY_TARGET_MINIO_ENDPOINT=minio-restore:9000",
            "RECOVERY_TARGET_MINIO_PUBLIC_ENDPOINT=minio-restore:9000",
            f"RECOVERY_TARGET_MINIO_BUCKET={identity.restored_bucket}",
        ],
    )
    database_match = (
        source_at_backup_end["counts"] == restored["counts"]
        and source_at_backup_end["stable_hash"] == restored["stable_hash"]
        and source_at_backup_end["alembic_revision"] == restored["alembic_revision"]
    )
    source_unchanged = source_before == source_after
    observed_lost_records = sum(
        max(
            0,
            int(source_at_backup_end["counts"][table_name])
            - int(restored["counts"].get(table_name, 0)),
        )
        for table_name in source_at_backup_end["counts"]
    )
    invariant_keys = (
        "invalid_release_sources",
        "invalid_report_release_links",
        "invalid_analytics_release_links",
        "invalid_insight_analytics_links",
        "duplicate_complete_analytics",
        "unfinalized_students_with_complete_snapshot",
        "incomplete_students_scored_as_zero",
    )
    invariants = all(not restored[key] for key in invariant_keys) and all(
        not count for count in restored["stored_file_reference_violations"].values()
    )
    evidence = {
        "schema_version": "2.0",
        "scope": "isolated disposable synthetic development recovery exercise",
        "run_id": identity.run_id,
        "compose_project": identity.project,
        "source_database": identity.source_database,
        "restored_database": identity.restored_database,
        "source_bucket": identity.source_bucket,
        "restored_bucket": identity.restored_bucket,
        "postgresql_version": version,
        "backup_format": "PostgreSQL custom (-Fc)",
        "backup_size_bytes": dump.stat().st_size,
        "fixture_last_write_at": fixture_last_write_at,
        "backup_started_at": backup_started_at,
        "backup_completed_at": backup_completed_at,
        "restore_completed_at": restore_completed_at,
        "backup_seconds": round(backup_seconds, 3),
        "restore_seconds": round(restore_seconds, 3),
        "writes_during_backup": writes_during_backup,
        "observed_data_loss_records": observed_lost_records,
        "observed_development_rpo_seconds": (
            0 if database_match and observed_lost_records == 0 else None
        ),
        "observed_development_rto_seconds": round(restore_seconds, 3),
        "source_before": source_before,
        "source_at_backup_end": source_at_backup_end,
        "restored": restored,
        "source_unchanged": source_unchanged,
        "database_match": database_match,
        "business_invariants_pass": invariants,
        "objects": objects,
        "current_run_unknown_orphans": len(objects["current_run_unknown_orphans"]),
        "full_signed_urls_recorded": False,
        "credentials_recorded": False,
        "automatic_orphan_deletion": False,
        "overall_pass": bool(
            database_match
            and source_unchanged
            and invariants
            and not objects["mismatches"]
            and not objects["database_missing_object"]
            and not objects["current_run_unknown_orphans"]
            and not objects["unable_to_classify"]
        ),
        "elapsed_seconds": round(time.perf_counter() - elapsed_started, 3),
    }
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
