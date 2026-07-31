"""Fail-closed reset for one fixed, test-only synthetic teacher.

The reset deliberately derives its database scope from ``owner_id`` roots and
foreign-key descendants.  It never accepts an owner, bucket, or object prefix
from a request, and it never lists or removes a bucket.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Table, delete, func, select, tuple_
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AuditLog, User
from app.storage.base import ObjectStorage

SYNTHETIC_DEMO_MARKER = "ahamark-web-demo-v1.synthetic.invalid"
SYNTHETIC_DEMO_EMAIL = f"teacher@{SYNTHETIC_DEMO_MARKER}"
SYNTHETIC_DEMO_USER_ID = uuid.uuid5(uuid.NAMESPACE_URL, f"ahamark:{SYNTHETIC_DEMO_MARKER}:teacher")
ALLOWED_STORAGE_PREFIXES = (
    "answer-evidence",
    "assignments",
    "recognition",
    "reports",
    "submission-processing",
    "submissions",
    "uploads",
)
STORAGE_CLEANUP_AUDIT_ACTION = "synthetic_demo_reset_storage_cleanup"
STORAGE_CLEANUP_RESOURCE_TYPE = "synthetic_demo_reset"


class DemoResetRefused(RuntimeError):
    pass


class DemoResetStorageCleanupFailed(DemoResetRefused):
    pass


@dataclass(frozen=True)
class DemoResetResult:
    deleted_rows: dict[str, int]
    deleted_object_keys: tuple[str, ...]


def _pk(row: Any, table: Table) -> tuple[Any, ...]:
    return tuple(row._mapping[column] for column in table.primary_key.columns)


def _where_pk(table: Table, keys: set[tuple[Any, ...]]) -> Any:
    columns = list(table.primary_key.columns)
    if len(columns) == 1:
        return columns[0].in_([key[0] for key in keys])
    return tuple_(*columns).in_(list(keys))


def _rows_for_keys(db: Session, table: Table, keys: set[tuple[Any, ...]]) -> list[Any]:
    if not keys:
        return []
    return list(db.execute(select(table).where(_where_pk(table, keys))).all())


def _validate_object_keys(object_keys: set[str]) -> None:
    owner_segment = str(SYNTHETIC_DEMO_USER_ID)
    for key in object_keys:
        parts = key.split("/")
        if (
            len(parts) < 3
            or parts[0] not in ALLOWED_STORAGE_PREFIXES
            or parts[1] != owner_segment
            or key.startswith("/")
            or ".." in parts
        ):
            raise DemoResetRefused(f"DEMO_RESET_STORAGE_KEY_REFUSED:{key}")


def _scope(db: Session) -> tuple[dict[Table, set[tuple[Any, ...]]], set[str]]:
    metadata = User.metadata
    selected: dict[Table, set[tuple[Any, ...]]] = defaultdict(set)

    # Only owner_id is a scope root.  Actor/reviewer/creator references must
    # never make another teacher's resource eligible for deletion.
    for table in metadata.tables.values():
        if "owner_id" not in table.c:
            continue
        if not list(table.primary_key.columns):
            raise DemoResetRefused(f"DEMO_RESET_TABLE_WITHOUT_PK:{table.name}")
        rows = db.execute(select(table).where(table.c.owner_id == SYNTHETIC_DEMO_USER_ID)).all()
        selected[table].update(_pk(row, table) for row in rows)

    changed = True
    while changed:
        changed = False
        for child in metadata.tables.values():
            if not list(child.primary_key.columns):
                continue
            for fk in child.foreign_key_constraints:
                if len(fk.elements) != 1:
                    # Composite relationships are denied if their parent is in
                    # scope; silently guessing their semantics would be unsafe.
                    if any(element.column.table in selected for element in fk.elements):
                        raise DemoResetRefused(f"DEMO_RESET_COMPOSITE_FK_UNSUPPORTED:{child.name}")
                    continue
                element = next(iter(fk.elements))
                parent = element.column.table
                parent_keys = selected.get(parent)
                parent_pk = list(parent.primary_key.columns)
                if not parent_keys or len(parent_pk) != 1 or element.column is not parent_pk[0]:
                    continue
                rows = db.execute(
                    select(child).where(element.parent.in_([key[0] for key in parent_keys]))
                ).all()
                before = len(selected[child])
                selected[child].update(_pk(row, child) for row in rows)
                changed = changed or len(selected[child]) != before

    object_keys: set[str] = set()
    for table, keys in selected.items():
        for row in _rows_for_keys(db, table, keys):
            mapping = row._mapping
            if "owner_id" in table.c and mapping["owner_id"] != SYNTHETIC_DEMO_USER_ID:
                raise DemoResetRefused(f"DEMO_RESET_CROSS_OWNER:{table.name}")
            for column in table.columns:
                if "storage_key" not in column.name:
                    continue
                value = mapping[column]
                if value:
                    object_keys.add(str(value))

    _validate_object_keys(object_keys)
    return selected, object_keys


def _pending_cleanup_audits(db: Session) -> list[AuditLog]:
    audits = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action == STORAGE_CLEANUP_AUDIT_ACTION,
                AuditLog.resource_type == STORAGE_CLEANUP_RESOURCE_TYPE,
                AuditLog.resource_id == SYNTHETIC_DEMO_MARKER,
            )
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )
    return [
        audit
        for audit in audits
        if audit.metadata_.get("status") in {"pending", "failed"}
    ]


def reset_synthetic_demo(
    db: Session, storage: ObjectStorage, settings: Settings
) -> DemoResetResult:
    if settings.app_env.lower() != "test" or not settings.synthetic_demo_reset_enabled:
        raise DemoResetRefused("DEMO_RESET_TEST_ONLY")
    if settings.minio_bucket != settings.synthetic_demo_reset_bucket:
        raise DemoResetRefused("DEMO_RESET_BUCKET_MISMATCH")

    user_by_id = db.get(User, SYNTHETIC_DEMO_USER_ID)
    user_by_email = db.scalar(select(User).where(User.email == SYNTHETIC_DEMO_EMAIL))
    if user_by_id is None and user_by_email is None:
        return DemoResetResult({}, ())
    if (
        user_by_id is None
        or user_by_email is None
        or user_by_id.id != user_by_email.id
        or user_by_id.email != SYNTHETIC_DEMO_EMAIL
    ):
        raise DemoResetRefused("DEMO_RESET_IDENTITY_MISMATCH")

    selected, object_keys = _scope(db)
    pending_audits = _pending_cleanup_audits(db)
    for audit in pending_audits:
        keys = audit.metadata_.get("object_keys", [])
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise DemoResetRefused("DEMO_RESET_CLEANUP_AUDIT_INVALID")
        object_keys.update(keys)
    _validate_object_keys(object_keys)
    selected.pop(User.__table__, None)

    if not any(selected.values()) and not object_keys:
        return DemoResetResult({}, ())

    # Database deletion and a durable, exact-key cleanup intent are committed
    # before touching object storage. ObjectStorage intentionally has no
    # bucket-delete operation, so this code cannot wipe or enumerate a bucket.
    cleanup_audit = AuditLog(
        actor_id=SYNTHETIC_DEMO_USER_ID,
        action=STORAGE_CLEANUP_AUDIT_ACTION,
        resource_type=STORAGE_CLEANUP_RESOURCE_TYPE,
        resource_id=SYNTHETIC_DEMO_MARKER,
        metadata_={
            "status": "pending",
            "object_keys": sorted(object_keys),
            "deleted_rows": {},
        },
    )
    db.add(cleanup_audit)
    try:
        deleted_rows: dict[str, int] = {}
        for table in reversed(User.metadata.sorted_tables):
            keys = selected.get(table)
            if not keys:
                continue
            result = db.execute(delete(table).where(_where_pk(table, keys)))
            deleted_rows[table.name] = int(result.rowcount or 0)
        for table in User.metadata.tables.values():
            if "owner_id" not in table.c:
                continue
            remaining = db.scalar(
                select(func.count())
                .select_from(table)
                .where(table.c.owner_id == SYNTHETIC_DEMO_USER_ID)
            )
            if remaining:
                raise DemoResetRefused(f"DEMO_RESET_INCOMPLETE:{table.name}")
        cleanup_audit.metadata_ = {
            **cleanup_audit.metadata_,
            "deleted_rows": deleted_rows,
        }
        for audit in pending_audits:
            audit.metadata_ = {
                **audit.metadata_,
                "status": "superseded",
                "superseded_by": str(cleanup_audit.id),
            }
        db.commit()
    except Exception:
        db.rollback()
        raise

    deleted_object_keys: list[str] = []
    failed_object_keys: list[str] = []
    failure_types: dict[str, str] = {}
    for key in sorted(object_keys):
        try:
            storage.delete(key)
            deleted_object_keys.append(key)
        except Exception as exc:
            failed_object_keys.append(key)
            failure_types[key] = type(exc).__name__

    cleanup_audit.metadata_ = {
        **cleanup_audit.metadata_,
        "status": "failed" if failed_object_keys else "complete",
        "deleted_object_keys": deleted_object_keys,
        "failed_object_keys": failed_object_keys,
        "failure_types": failure_types,
    }
    try:
        db.commit()
    except Exception:
        # The first commit already made the pending intent durable, so a later
        # reset can safely retry every exact key even if this status write fails.
        db.rollback()
        raise
    if failed_object_keys:
        raise DemoResetStorageCleanupFailed(
            "DEMO_RESET_STORAGE_CLEANUP_FAILED:" + ",".join(failed_object_keys)
        )
    return DemoResetResult(deleted_rows, tuple(deleted_object_keys))
