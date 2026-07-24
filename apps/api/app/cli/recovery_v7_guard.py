"""Fail-closed identity checks for the isolated version-7 recovery exercise."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

RUN_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FORBIDDEN_DATABASES = frozenset({"ahamark", "ahamark_business_e2e"})
FORBIDDEN_BUCKETS = frozenset({"ahamark-files", "ahamark-business-e2e"})


class RecoveryGuardError(RuntimeError):
    """Raised before any write when recovery identity checks fail."""


@dataclass(frozen=True)
class RecoveryIdentity:
    run_id: str
    project: str
    source_database: str
    restored_database: str
    source_bucket: str
    restored_bucket: str

    @property
    def object_prefix(self) -> str:
        return f"recovery-v7/{self.run_id}/"


def identity_for_run(run_id: str) -> RecoveryIdentity:
    if len(run_id) > 32 or not RUN_ID_PATTERN.fullmatch(run_id):
        raise RecoveryGuardError(
            "RECOVERY_V7_RUN_ID must be 1-32 lowercase letters, digits, and single hyphens"
        )
    database_suffix = run_id.replace("-", "_")
    return RecoveryIdentity(
        run_id=run_id,
        project=f"ahamark-recovery-v7-{run_id}",
        source_database=f"ahamark_recovery_{database_suffix}",
        restored_database=f"ahamark_recovery_restored_{database_suffix}",
        source_bucket=f"ahamark-recovery-source-{run_id}",
        restored_bucket=f"ahamark-recovery-restored-{run_id}",
    )


def database_name_from_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    name = unquote(parsed.path.lstrip("/"))
    if not name or "/" in name:
        raise RecoveryGuardError("DATABASE_URL must identify exactly one named database")
    return name


def require_recovery_environment(
    environ: Mapping[str, str] | None = None,
    *,
    database_roles: Sequence[str] = ("source",),
    bucket_roles: Sequence[str] = ("source",),
) -> RecoveryIdentity:
    """Validate the complete recovery identity before a command may write."""

    values = os.environ if environ is None else environ
    if values.get("APP_ENV", "").lower() != "test":
        raise RecoveryGuardError("recovery commands require APP_ENV=test")
    if values.get("RECOVERY_V7_ENABLED", "").lower() != "true":
        raise RecoveryGuardError("recovery commands require RECOVERY_V7_ENABLED=true")
    run_id = values.get("RECOVERY_V7_RUN_ID", "")
    if not run_id:
        raise RecoveryGuardError("RECOVERY_V7_RUN_ID is required")
    identity = identity_for_run(run_id)
    if values.get("RECOVERY_V7_COMPOSE_PROJECT") != identity.project:
        raise RecoveryGuardError("Compose project does not match RECOVERY_V7_RUN_ID")

    allowed_databases = {
        "source": identity.source_database,
        "restored": identity.restored_database,
    }
    try:
        expected_databases = {allowed_databases[role] for role in database_roles}
    except KeyError as exc:
        raise RecoveryGuardError(f"unknown database role: {exc.args[0]}") from exc
    actual_database = database_name_from_url(values.get("DATABASE_URL", ""))
    if actual_database in FORBIDDEN_DATABASES or actual_database not in expected_databases:
        raise RecoveryGuardError("database name does not match this recovery run")

    allowed_buckets = {
        "source": identity.source_bucket,
        "restored": identity.restored_bucket,
    }
    try:
        expected_buckets = {allowed_buckets[role] for role in bucket_roles}
    except KeyError as exc:
        raise RecoveryGuardError(f"unknown bucket role: {exc.args[0]}") from exc
    actual_bucket = values.get("MINIO_BUCKET", "")
    if actual_bucket in FORBIDDEN_BUCKETS or actual_bucket not in expected_buckets:
        raise RecoveryGuardError("MinIO bucket does not match this recovery run")
    return identity
