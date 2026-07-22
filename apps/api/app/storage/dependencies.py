from app.core.config import get_settings
from app.storage.base import ObjectStorage
from app.storage.minio import MinioStorage
from minio import Minio


def get_storage() -> ObjectStorage:
    s = get_settings()
    return MinioStorage(
        Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        ),
        s.minio_bucket,
    )
