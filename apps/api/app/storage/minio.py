from datetime import timedelta
from io import BytesIO
from typing import BinaryIO

from app.storage.base import ObjectMetadata
from minio import Minio


class MinioStorage:
    def __init__(self, client: Minio, bucket: str, public_client: Minio | None = None):
        self.client, self.bucket = client, bucket
        self.public_client = public_client or client

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put(self, key: str, data: BinaryIO, size: int, content_type: str) -> ObjectMetadata:
        self.ensure_bucket()
        self.client.put_object(self.bucket, key, data, size, content_type=content_type)
        return ObjectMetadata(key, size, content_type)

    def stat(self, key: str) -> ObjectMetadata:
        obj = self.client.stat_object(self.bucket, key)
        return ObjectMetadata(key, obj.size or 0, obj.content_type)

    def get(self, key: str) -> BinaryIO:
        response = self.client.get_object(self.bucket, key)
        try:
            return BytesIO(response.read())
        finally:
            response.close()
            response.release_conn()

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, key)

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return self.public_client.presigned_get_object(
            self.bucket, key, expires=timedelta(seconds=expires_seconds)
        )
