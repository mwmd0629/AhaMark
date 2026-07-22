import io

from app.storage.base import ObjectMetadata


class MemoryStorage:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        pass

    def put(self, key: str, data: io.BytesIO, size: int, content_type: str) -> ObjectMetadata:
        self.data[key] = data.read()
        return ObjectMetadata(key, size, content_type)

    def stat(self, key: str) -> ObjectMetadata:
        return ObjectMetadata(key, len(self.data[key]), "text/plain")

    def get(self, key: str) -> io.BytesIO:
        return io.BytesIO(self.data[key])

    def delete(self, key: str) -> None:
        del self.data[key]

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return f"signed://{key}?expires={expires_seconds}"


def test_storage_contract() -> None:
    store = MemoryStorage()
    meta = store.put("a", io.BytesIO(b"hello"), 5, "text/plain")
    assert meta.size == 5
    assert store.stat("a").size == 5
    assert store.presigned_get("a").startswith("signed://")
    store.delete("a")
    assert "a" not in store.data
