from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from common.models import ChunkResult, QueueStats


class ObjectStore(Protocol):
    async def get(self, key: str) -> bytes: ...

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> int: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class VersionStore(Protocol):
    async def set_status(self, version_id: str, status: str, error: str | None = None) -> None: ...

    async def set_text_key(self, version_id: str, text_key: str) -> None: ...


class ChunkStore(Protocol):
    async def replace_chunks(self, document_id: str, version_id: str, pieces: list[tuple[int, str]]) -> list[str]: ...

    async def texts(self, chunk_ids: list[str]) -> list[tuple[str, str]]: ...

    async def save_embeddings(self, pairs: list[tuple[str, Sequence[float]]]) -> None: ...


class PublishWorkspace(Protocol):
    async def embedding_counts(self, version_id: str) -> tuple[int, int]: ...

    async def activate(self, document_id: str, version_id: str) -> None: ...


class PublishUnitOfWork(Protocol):
    def begin(self) -> AbstractAsyncContextManager[PublishWorkspace]: ...


class DocumentLifecycle(Protocol):
    async def start_deletion(self, document_id: str) -> None: ...

    async def finish_deletion(self, document_id: str, removed_objects: int) -> None: ...


class QueueInspector(Protocol):
    async def stats(self, queue: str) -> QueueStats: ...


class SearchIndex(Protocol):
    async def nearest(self, embedding: Sequence[float], groups: list[str], top_k: int) -> list[ChunkResult]: ...
