import uuid
from types import SimpleNamespace

from app.models import ReportJob, StoredFile
from app.results.jobs import run_report_job
from test_storage import MemoryStorage


class FakeDb:
    def __init__(self, job: object, release: object) -> None:
        self.job, self.release = job, release
        self.added: list[object] = []

    def get(self, model: object, object_id: object) -> object | None:
        if model is ReportJob and object_id == self.job.id:
            return self.job
        return self.release

    def add(self, item: object) -> None:
        self.added.append(item)
        if isinstance(item, StoredFile) and item.id is None:
            item.id = uuid.uuid4()

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass


def test_report_worker_accepts_only_job_id_and_is_idempotent(monkeypatch: object) -> None:
    job_id, owner_id, release_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = SimpleNamespace(
        id=job_id,
        owner_id=owner_id,
        grade_release_id=release_id,
        report_type="gradebook_xlsx",
        status="queued",
        started_at=None,
        progress=0,
        stored_file_id=None,
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    release = SimpleNamespace(id=release_id, version=3)
    db, storage = FakeDb(job, release), MemoryStorage()
    monkeypatch.setattr("app.results.jobs.gradebook_xlsx", lambda _db, _release: b"xlsx")  # type: ignore[attr-defined]
    run_report_job(db, storage, job_id)  # type: ignore[arg-type]
    assert job.status == "completed" and job.progress == 100 and job.stored_file_id
    assert len(storage.data) == 1
    run_report_job(db, storage, job_id)  # type: ignore[arg-type]
    assert len(storage.data) == 1
