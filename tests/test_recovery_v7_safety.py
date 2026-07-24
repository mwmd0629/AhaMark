from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.cli.reconcile_recovery import (
    classify_orphans,
    ensure_no_target_objects,
    verify_signed_url,
    verify_signed_url_if_available,
)
from app.cli.recovery_v7_guard import (
    RecoveryGuardError,
    identity_for_run,
    require_recovery_environment,
)
from app.cli.seed_recovery_fixture import (
    assert_database_is_synthetic,
    ensure_fixture_object,
)

from scripts import verify_backup_restore

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "gate-20260724-a1"


def valid_environment() -> dict[str, str]:
    identity = identity_for_run(RUN_ID)
    return {
        "APP_ENV": "test",
        "RECOVERY_V7_ENABLED": "true",
        "RECOVERY_V7_RUN_ID": RUN_ID,
        "RECOVERY_V7_COMPOSE_PROJECT": identity.project,
        "DATABASE_URL": f"postgresql+psycopg://u:p@postgres:5432/{identity.source_database}",
        "MINIO_BUCKET": identity.source_bucket,
    }


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("APP_ENV", "production"),
        ("RECOVERY_V7_ENABLED", ""),
        ("RECOVERY_V7_RUN_ID", ""),
        ("RECOVERY_V7_RUN_ID", "INVALID_run"),
        ("DATABASE_URL", "postgresql+psycopg://u:p@postgres:5432/ahamark"),
        ("MINIO_BUCKET", "ahamark-files"),
    ),
)
def test_recovery_guard_rejects_unsafe_identity(key: str, value: str) -> None:
    environ = valid_environment()
    environ[key] = value
    with pytest.raises(RecoveryGuardError):
        require_recovery_environment(environ)


def test_recovery_guard_accepts_exact_identity() -> None:
    identity = require_recovery_environment(valid_environment())
    assert identity.run_id == RUN_ID
    assert identity.source_database.endswith("gate_20260724_a1")


def test_existing_objects_are_never_overwritten() -> None:
    with pytest.raises(RecoveryGuardError, match="would overwrite"):
        ensure_no_target_objects({"recovery-v7/key"})
    storage = MagicMock()
    storage.bucket = "source"
    storage.client.bucket_exists.return_value = True
    storage.client.list_objects.return_value = [SimpleNamespace(object_name="recovery-v7/key")]
    storage.stat.return_value = SimpleNamespace(size=7, content_type="text/plain")
    storage.get.return_value = io.BytesIO(b"content")
    ensure_fixture_object(storage, "recovery-v7/key", b"content", "text/plain", "fixture.txt")
    storage.put.assert_not_called()
    with pytest.raises(RecoveryGuardError, match="does not match"):
        ensure_fixture_object(
            storage,
            "recovery-v7/key",
            b"changed",
            "text/plain",
            "fixture.txt",
        )


def test_non_synthetic_database_is_rejected() -> None:
    database = MagicMock()
    database.scalars.return_value = [SimpleNamespace(email="teacher@example.com")]
    with pytest.raises(RecoveryGuardError, match="non-synthetic user"):
        assert_database_is_synthetic(database, RUN_ID)


def test_skip_start_rejects_wrong_run_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verify_backup_restore,
        "docker_output",
        lambda *args, **kwargs: "different-run|postgres",
    )
    with pytest.raises(RecoveryGuardError, match="service set|Run ID"):
        verify_backup_restore.assert_existing_stack_identity(identity_for_run(RUN_ID))


def test_orphan_categories_are_computed_from_sets() -> None:
    report = classify_orphans(
        objects={
            f"recovery-v7/{RUN_ID}/stored",
            f"recovery-v7/{RUN_ID}/derived",
            f"recovery-v7/{RUN_ID}/unknown",
            "old/known",
            "outside/unresolved",
        },
        database_keys={f"recovery-v7/{RUN_ID}/stored", "missing/object"},
        legitimate_derived={f"recovery-v7/{RUN_ID}/derived"},
        known_historical={"old/known"},
        current_prefix=f"recovery-v7/{RUN_ID}/",
    )
    assert report["database_missing_object"] == ["missing/object"]
    assert report["known_historical_orphans"] == ["old/known"]
    assert report["legitimate_derived_objects"] == [f"recovery-v7/{RUN_ID}/derived"]
    assert report["current_run_unknown_orphans"] == [f"recovery-v7/{RUN_ID}/unknown"]
    assert report["unable_to_classify"] == ["outside/unresolved"]


def test_empty_object_set_skips_signed_url_without_indexing() -> None:
    result = verify_signed_url_if_available(MagicMock(), "bucket", [], 2)
    assert result["skipped"] is True
    assert result["url_query_recorded"] is False


def test_signed_url_result_never_records_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.presigned_get_object.side_effect = [
        "http://minio/object?secret=first",
        "http://minio/object?secret=second",
    ]
    statuses = iter((200, 403, 200, 403))
    monkeypatch.setattr(
        "app.cli.reconcile_recovery.fetch_status",
        lambda _url: next(statuses),
    )
    monkeypatch.setattr("app.cli.reconcile_recovery.time.sleep", lambda _seconds: None)
    result = verify_signed_url(client, "bucket", "key", 2)
    assert result["old_url_expired"] is True
    assert result["renewed_read_succeeded"] is True
    assert "?" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_compose_has_no_host_ports_or_fixed_credentials() -> None:
    compose = (ROOT / "docker-compose.recovery.yml").read_text(encoding="utf-8")
    assert "\n    ports:" not in compose
    assert "recovery-only" not in compose
    assert "recovery-storage-only" not in compose
    assert "${RECOVERY_POSTGRES_PASSWORD:?required}" in compose
    assert "${RECOVERY_MINIO_SECRET_KEY:?required}" in compose
    assert "\nname:" not in compose


def test_restore_script_never_uses_destructive_clean_restore() -> None:
    script = (ROOT / "scripts" / "verify_backup_restore.py").read_text(encoding="utf-8")
    assert '"--clean"' not in script
    assert '"--if-exists"' not in script
    assert '"--single-transaction"' in script
