import io
import subprocess
import uuid
from types import SimpleNamespace

import pytest
from app.cli.failure_recovery_probe import structured_json
from app.cli.recovery_v7_guard import RecoveryGuardError
from app.core.config import Settings
from app.failure_recovery import recovery_fault_checkpoint
from app.models import ReportJob, StoredFile
from app.results.jobs import run_report_job
from app.storage.base import ObjectMetadata
from test_storage import MemoryStorage

from scripts.verify_backup_restore import raw_command
from scripts.verify_failure_recovery import analytics_idempotency_pass, redelivery_pass


class ReportDb:
    def __init__(self, job: object, release: object, *, fail_final_commit: bool = False) -> None:
        self.job = job
        self.release = release
        self.fail_final_commit = fail_final_commit
        self.commits = 0

    def get(self, model: object, object_id: object) -> object | None:
        if model is ReportJob and object_id == self.job.id:
            return self.job
        return self.release

    def add(self, item: object) -> None:
        if isinstance(item, StoredFile) and item.id is None:
            item.id = uuid.uuid4()

    def flush(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1
        if self.fail_final_commit and self.commits == 2:
            raise RuntimeError("synthetic database commit failure")


class FailingPutStorage(MemoryStorage):
    def put(self, key: str, data: io.BytesIO, size: int, content_type: str) -> ObjectMetadata:
        raise RuntimeError("synthetic object write failure")


def report_fixture(status: str = "queued") -> tuple[object, object]:
    job_id, owner_id, release_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = SimpleNamespace(
        id=job_id,
        owner_id=owner_id,
        grade_release_id=release_id,
        report_type="gradebook_xlsx",
        status=status,
        started_at=None,
        progress=0,
        stored_file_id=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    return job, SimpleNamespace(id=release_id, version=1)


def test_fault_checkpoint_refuses_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOVERY_V7_FAULT_CHECKPOINT", "recognition-running")
    monkeypatch.setenv("RECOVERY_V7_FAULT_DELAY_SECONDS", "1")
    monkeypatch.setenv("RECOVERY_V7_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RecoveryGuardError, match="APP_ENV=test"):
        recovery_fault_checkpoint("recognition-running")


def test_celery_visibility_timeout_is_configurable_without_changing_default() -> None:
    assert Settings(_env_file=None).celery_visibility_timeout == 3600
    assert Settings(_env_file=None, celery_visibility_timeout=15).celery_visibility_timeout == 15


def test_redelivery_accepts_real_late_restore_but_not_early_or_unmarked_resume() -> None:
    completed = {"status": "completed", "attempt": 2}
    assert redelivery_pass(completed, elapsed_seconds=98.712, configured_seconds=15)
    assert not redelivery_pass(completed, elapsed_seconds=14.999, configured_seconds=15)
    assert not redelivery_pass(
        {"status": "completed", "attempt": 1},
        elapsed_seconds=98.712,
        configured_seconds=15,
    )


def test_analytics_gate_requires_one_snapshot_for_same_release() -> None:
    assert analytics_idempotency_pass({"snapshot-1"})
    assert not analytics_idempotency_pass(set())
    assert not analytics_idempotency_pass({"snapshot-1", "snapshot-2"})


def test_structured_probe_json_is_console_encoding_independent() -> None:
    encoded = structured_json({"message": "Redis 或 Celery Worker 不可用"})
    assert encoded.isascii()
    assert encoded.encode("ascii").decode("cp936") == encoded


def test_docker_subprocess_output_is_decoded_as_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}\n', stderr="")

    monkeypatch.setattr("scripts.verify_backup_restore.subprocess.run", fake_run)
    result = raw_command(["docker", "version"])
    assert result.stdout == '{"ok":true}\n'
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "strict"


def test_fault_checkpoint_requires_recovery_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOVERY_V7_FAULT_CHECKPOINT", "recognition-running")
    monkeypatch.setenv("RECOVERY_V7_FAULT_DELAY_SECONDS", "1")
    monkeypatch.setenv("RECOVERY_V7_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(
        "app.failure_recovery.require_recovery_environment",
        lambda: (_ for _ in ()).throw(RecoveryGuardError("wrong run")),
    )
    with pytest.raises(RecoveryGuardError, match="wrong run"):
        recovery_fault_checkpoint("recognition-running")


def test_report_checkpoint_is_also_production_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECOVERY_V7_FAULT_CHECKPOINT", "report-before-storage")
    monkeypatch.setenv("RECOVERY_V7_FAULT_DELAY_SECONDS", "1")
    monkeypatch.setenv("RECOVERY_V7_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RecoveryGuardError, match="APP_ENV=test"):
        recovery_fault_checkpoint("report-before-storage")


def test_redelivered_running_report_can_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    job, release = report_fixture("running")
    db, storage = ReportDb(job, release), MemoryStorage()
    monkeypatch.setattr("app.results.jobs.gradebook_xlsx", lambda _db, _release: b"xlsx")
    run_report_job(db, storage, job.id, allow_running_resume=True)  # type: ignore[arg-type]
    assert job.status == "completed"
    assert job.stored_file_id is not None
    assert len(storage.data) == 1


def test_report_object_failure_has_no_stored_file(monkeypatch: pytest.MonkeyPatch) -> None:
    job, release = report_fixture()
    db = ReportDb(job, release)
    monkeypatch.setattr("app.results.jobs.gradebook_xlsx", lambda _db, _release: b"xlsx")
    run_report_job(db, FailingPutStorage(), job.id)  # type: ignore[arg-type]
    assert job.status == "failed"
    assert job.error_code == "REPORT_GENERATION_FAILED"
    assert job.stored_file_id is None


def test_report_commit_failure_deletes_written_object(monkeypatch: pytest.MonkeyPatch) -> None:
    job, release = report_fixture()
    db, storage = ReportDb(job, release, fail_final_commit=True), MemoryStorage()
    monkeypatch.setattr("app.results.jobs.gradebook_xlsx", lambda _db, _release: b"xlsx")
    run_report_job(db, storage, job.id)  # type: ignore[arg-type]
    assert job.status == "failed"
    assert job.error_code == "REPORT_GENERATION_FAILED"
    assert storage.data == {}
