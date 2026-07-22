"""Produce a read-only database/MinIO consistency report; never delete objects."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import StoredFile
from app.storage.dependencies import get_storage
from app.storage.minio import MinioStorage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    storage = get_storage()
    if not isinstance(storage, MinioStorage):
        raise RuntimeError("orphan scan requires MinIO storage")
    with SessionLocal() as db:
        records = set(db.scalars(select(StoredFile.storage_key)).all())
    objects = {
        item.object_name
        for item in storage.client.list_objects(storage.bucket, recursive=True)
        if item.object_name
    }
    report = {
        "mode": "read_only",
        "bucket": storage.bucket,
        "database_records": len(records),
        "objects": len(objects),
        "database_missing_object": sorted(records - objects),
        "object_missing_database": sorted(objects - records),
        "automatic_deletion": False,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
