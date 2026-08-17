import io

import pytest
from app.core.config import Settings
from app.db.session import SessionLocal
from app.demo_reset import (
    STORAGE_CLEANUP_AUDIT_ACTION,
    SYNTHETIC_DEMO_EMAIL,
    SYNTHETIC_DEMO_MARKER,
    SYNTHETIC_DEMO_USER_ID,
    DemoResetRefused,
    DemoResetStorageCleanupFailed,
    reset_synthetic_demo,
)
from app.models import ArchiveStatus, AuditLog, FileStatus, SchoolClass, StoredFile, User
from app.storage.base import ObjectMetadata
from sqlalchemy import select


class RecordingStorage:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.deleted: list[str] = []

    def ensure_bucket(self) -> None:
        raise AssertionError("reset must never create or inspect a bucket")

    def put(self, key: str, data: io.BytesIO, size: int, content_type: str) -> ObjectMetadata:
        raise AssertionError("reset must never write an object")

    def get(self, key: str) -> io.BytesIO:
        raise AssertionError("reset must never read an object")

    def stat(self, key: str) -> ObjectMetadata:
        raise AssertionError("reset must never enumerate or inspect an object")

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        raise AssertionError("reset must never create a signed URL")


class FailingDeleteStorage(RecordingStorage):
    def __init__(self, objects: dict[str, bytes], fail_keys: set[str]) -> None:
        super().__init__(objects)
        self.fail_keys = fail_keys

    def delete(self, key: str) -> None:
        if key in self.fail_keys:
            self.deleted.append(key)
            raise OSError("injected object-storage failure")
        super().delete(key)


def settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "synthetic_demo_reset_enabled": True,
        "minio_bucket": "ahamark-business-e2e-files",
        "synthetic_demo_reset_bucket": "ahamark-business-e2e-files",
    }
    values.update(changes)
    return Settings(**values)


def user(user_id, email: str) -> User:
    return User(
        id=user_id,
        email=email,
        password_hash="!synthetic-test!",
        display_name="Synthetic teacher",
    )


def test_reset_is_exact_idempotent_and_preserves_unknown_owner() -> None:
    real_id = __import__("uuid").uuid4()
    owned_key = f"uploads/{SYNTHETIC_DEMO_USER_ID}/fixture.png"
    real_key = f"uploads/{real_id}/real.png"
    storage = RecordingStorage({owned_key: b"demo", real_key: b"real"})
    with SessionLocal() as db:
        db.add_all(
            [
                user(SYNTHETIC_DEMO_USER_ID, SYNTHETIC_DEMO_EMAIL),
                user(real_id, "teacher@example.com"),
                SchoolClass(
                    owner_id=SYNTHETIC_DEMO_USER_ID,
                    name="Demo",
                    status=ArchiveStatus.active,
                ),
                SchoolClass(owner_id=real_id, name="Real", status=ArchiveStatus.active),
                StoredFile(
                    owner_id=SYNTHETIC_DEMO_USER_ID,
                    storage_key=owned_key,
                    original_name="fixture.png",
                    content_type="image/png",
                    size=4,
                    checksum="a" * 64,
                    status=FileStatus.ready,
                ),
                StoredFile(
                    owner_id=real_id,
                    storage_key=real_key,
                    original_name="real.png",
                    content_type="image/png",
                    size=4,
                    checksum="b" * 64,
                    status=FileStatus.ready,
                ),
            ]
        )
        db.commit()

        result = reset_synthetic_demo(db, storage, settings())
        assert result.deleted_object_keys == (owned_key,)
        assert storage.deleted == [owned_key]
        assert storage.objects == {real_key: b"real"}
        assert db.get(User, SYNTHETIC_DEMO_USER_ID) is not None
        assert (
            db.scalar(select(SchoolClass).where(SchoolClass.owner_id == SYNTHETIC_DEMO_USER_ID))
            is None
        )
        assert db.scalar(select(SchoolClass).where(SchoolClass.owner_id == real_id)) is not None

        again = reset_synthetic_demo(db, storage, settings())
        assert again.deleted_rows == {}
        assert again.deleted_object_keys == ()
        assert storage.deleted == [owned_key]


@pytest.mark.parametrize(
    "changes",
    [
        {"app_env": "development"},
        {"synthetic_demo_reset_enabled": False},
        {"minio_bucket": "unknown-nonempty-bucket"},
    ],
)
def test_guard_failures_are_zero_write(changes: dict[str, object]) -> None:
    storage = RecordingStorage()
    with SessionLocal() as db:
        db.add(user(SYNTHETIC_DEMO_USER_ID, SYNTHETIC_DEMO_EMAIL))
        db.add(
            SchoolClass(
                owner_id=SYNTHETIC_DEMO_USER_ID,
                name="Untouched",
                status=ArchiveStatus.active,
            )
        )
        db.commit()
        with pytest.raises(DemoResetRefused):
            reset_synthetic_demo(db, storage, settings(**changes))
        assert storage.deleted == []
        assert db.scalar(select(SchoolClass)) is not None


def test_unknown_storage_prefix_refuses_before_any_write() -> None:
    unsafe_key = f"synthetic/{SYNTHETIC_DEMO_MARKER}/fixture.png"
    storage = RecordingStorage({unsafe_key: b"demo"})
    with SessionLocal() as db:
        db.add(user(SYNTHETIC_DEMO_USER_ID, SYNTHETIC_DEMO_EMAIL))
        db.add(
            StoredFile(
                owner_id=SYNTHETIC_DEMO_USER_ID,
                storage_key=unsafe_key,
                original_name="fixture.png",
                content_type="image/png",
                size=4,
                checksum="a" * 64,
                status=FileStatus.ready,
            )
        )
        db.commit()
        with pytest.raises(DemoResetRefused, match="STORAGE_KEY_REFUSED"):
            reset_synthetic_demo(db, storage, settings())
        assert storage.deleted == []
        assert db.scalar(select(StoredFile)) is not None


def test_fixed_uuid_and_email_must_match() -> None:
    storage = RecordingStorage()
    with SessionLocal() as db:
        db.add(user(SYNTHETIC_DEMO_USER_ID, "other.synthetic.invalid@example.com"))
        db.commit()
        with pytest.raises(DemoResetRefused, match="IDENTITY_MISMATCH"):
            reset_synthetic_demo(db, storage, settings())
        assert storage.deleted == []


def test_database_commit_failure_never_deletes_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    owned_key = f"uploads/{SYNTHETIC_DEMO_USER_ID}/commit-failure.png"
    storage = RecordingStorage({owned_key: b"demo"})
    with SessionLocal() as db:
        db.add(user(SYNTHETIC_DEMO_USER_ID, SYNTHETIC_DEMO_EMAIL))
        db.add(
            StoredFile(
                owner_id=SYNTHETIC_DEMO_USER_ID,
                storage_key=owned_key,
                original_name="commit-failure.png",
                content_type="image/png",
                size=4,
                checksum="c" * 64,
                status=FileStatus.ready,
            )
        )
        db.commit()

        def fail_commit() -> None:
            raise RuntimeError("injected database commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="database commit failure"):
            reset_synthetic_demo(db, storage, settings())

        assert storage.deleted == []
        assert storage.objects == {owned_key: b"demo"}
        assert db.scalar(select(StoredFile).where(StoredFile.storage_key == owned_key)) is not None


def test_storage_failure_is_audited_and_retried_by_exact_key() -> None:
    owned_key = f"uploads/{SYNTHETIC_DEMO_USER_ID}/storage-failure.png"
    storage = FailingDeleteStorage({owned_key: b"demo"}, {owned_key})
    with SessionLocal() as db:
        db.add(user(SYNTHETIC_DEMO_USER_ID, SYNTHETIC_DEMO_EMAIL))
        db.add(
            StoredFile(
                owner_id=SYNTHETIC_DEMO_USER_ID,
                storage_key=owned_key,
                original_name="storage-failure.png",
                content_type="image/png",
                size=4,
                checksum="d" * 64,
                status=FileStatus.ready,
            )
        )
        db.commit()

        with pytest.raises(DemoResetStorageCleanupFailed, match="STORAGE_CLEANUP_FAILED"):
            reset_synthetic_demo(db, storage, settings())

        assert db.scalar(select(StoredFile).where(StoredFile.storage_key == owned_key)) is None
        failed_audit = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == STORAGE_CLEANUP_AUDIT_ACTION)
            .order_by(AuditLog.created_at.desc())
        )
        assert failed_audit is not None
        assert failed_audit.metadata_["status"] == "failed"
        assert failed_audit.metadata_["object_keys"] == [owned_key]
        assert failed_audit.metadata_["failed_object_keys"] == [owned_key]
        assert failed_audit.metadata_["failure_types"] == {owned_key: "OSError"}

        storage.fail_keys.clear()
        result = reset_synthetic_demo(db, storage, settings())
        assert result.deleted_rows == {}
        assert result.deleted_object_keys == (owned_key,)
        assert storage.objects == {}

        audits = list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.action == STORAGE_CLEANUP_AUDIT_ACTION)
                .order_by(AuditLog.created_at)
            )
        )
        assert [audit.metadata_["status"] for audit in audits] == ["superseded", "complete"]
