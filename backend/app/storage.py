from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Storage(Protocol):
    """Where uploaded files live. Keys are relative paths such as "ab/abcdef.pdf"."""

    def save(self, key: str, content: bytes) -> None: ...

    def read(self, key: str) -> bytes: ...


class LocalStorage:
    """Files on a local folder (a Docker volume in compose). An S3 version would implement the
    same two methods."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Keys are built by the app, but never let one escape the storage root.
        if not path.is_relative_to(self.root):
            raise ValueError(f"Storage key outside the storage root: {key}")
        return path

    def save(self, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()


@lru_cache
def get_storage() -> Storage:
    return LocalStorage(get_settings().storage_dir)
