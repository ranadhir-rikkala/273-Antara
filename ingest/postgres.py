import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from common.db import vector_literal
from common.models import ChunkResult, VersionStatus

_NEAREST_SQL = """
SELECT c.document_id::text, c.version_id::text, c.chunk_id::text, c.text, c.page_number,
       1 - (e.embedding <=> %(query)s::vector) AS score
FROM document_chunks c
JOIN document_embeddings e ON e.chunk_id = c.chunk_id
JOIN documents d ON d.document_id = c.document_id AND d.active_version_id = c.version_id
WHERE d.deleted_at IS NULL
  AND EXISTS (
      SELECT 1 FROM document_acl a
      WHERE a.document_id = d.document_id AND a.group_name = ANY(%(groups)s)
  )
ORDER BY e.embedding <=> %(query)s::vector
LIMIT %(top_k)s
"""


class PostgresVersionStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def set_status(self, version_id: str, status: str, error: str | None = None) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE document_versions SET status = %s, error = %s, updated_at = now() WHERE version_id = %s",
                (status, error, version_id),
            )

    async def set_text_key(self, version_id: str, text_key: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE document_versions SET text_key = %s, updated_at = now() WHERE version_id = %s",
                (text_key, version_id),
            )


class PostgresChunkStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def replace_chunks(self, document_id: str, version_id: str, pieces: list[tuple[int, str]]) -> list[str]:
        chunk_ids = [str(uuid.uuid4()) for _ in pieces]
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute("DELETE FROM document_chunks WHERE version_id = %s", (version_id,))
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO document_chunks (chunk_id, document_id, version_id, chunk_index, page_number, text) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [
                        (chunk_id, document_id, version_id, index, page_number, text)
                        for index, (chunk_id, (page_number, text)) in enumerate(zip(chunk_ids, pieces, strict=True))
                    ],
                )
        return chunk_ids

    async def texts(self, chunk_ids: list[str]) -> list[tuple[str, str]]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT chunk_id::text, text FROM document_chunks "
                    "WHERE chunk_id = ANY(%s::uuid[]) ORDER BY chunk_index",
                    (chunk_ids,),
                )
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    async def save_embeddings(self, pairs: list[tuple[str, Sequence[float]]]) -> None:
        async with self._pool.connection() as conn, conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO document_embeddings (chunk_id, embedding) VALUES (%s, %s::vector) "
                "ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding",
                [(chunk_id, vector_literal(vector)) for chunk_id, vector in pairs],
            )


class _PublishWorkspace:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def embedding_counts(self, version_id: str) -> tuple[int, int]:
        row = await (
            await self._conn.execute(
                "SELECT count(c.chunk_id), count(e.chunk_id) FROM document_chunks c "
                "LEFT JOIN document_embeddings e ON e.chunk_id = c.chunk_id WHERE c.version_id = %s",
                (version_id,),
            )
        ).fetchone()
        return row[0], row[1]

    async def activate(self, document_id: str, version_id: str) -> None:
        await self._conn.execute(
            "UPDATE document_versions SET status = %s, updated_at = now() WHERE version_id = %s",
            (VersionStatus.READY, version_id),
        )
        await self._conn.execute(
            "UPDATE documents SET active_version_id = %s WHERE document_id = %s", (version_id, document_id)
        )


class PostgresPublishUnitOfWork:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_PublishWorkspace]:
        async with self._pool.connection() as conn, conn.transaction():
            yield _PublishWorkspace(conn)


class PostgresDocumentLifecycle:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def start_deletion(self, document_id: str) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO deletion_jobs (document_id, status) VALUES (%s, 'RUNNING') "
                "ON CONFLICT (document_id) DO UPDATE SET status = 'RUNNING', started_at = now()",
                (document_id,),
            )
            await conn.execute(
                "UPDATE document_versions SET status = %s, updated_at = now() WHERE document_id = %s",
                (VersionStatus.DELETING, document_id),
            )

    async def finish_deletion(self, document_id: str, removed_objects: int) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE documents SET active_version_id = NULL, deleted_at = now() WHERE document_id = %s",
                (document_id,),
            )
            await conn.execute(
                "DELETE FROM answer_cache WHERE version_ids && "
                "(SELECT coalesce(array_agg(version_id), '{}') FROM document_versions WHERE document_id = %s)",
                (document_id,),
            )
            await conn.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))
            await conn.execute(
                "UPDATE document_versions SET status = %s, updated_at = now() WHERE document_id = %s",
                (VersionStatus.DELETED, document_id),
            )
            await conn.execute(
                "UPDATE deletion_jobs SET status = 'DONE', finished_at = now() WHERE document_id = %s", (document_id,)
            )
            await conn.execute(
                "INSERT INTO audit_log (actor_id, action, detail) VALUES ('system', 'document.deleted', "
                "jsonb_build_object('document_id', %s::text, 'objects_removed', %s::int))",
                (document_id, removed_objects),
            )


class PostgresSearchIndex:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def nearest(self, embedding: Sequence[float], groups: list[str], top_k: int) -> list[ChunkResult]:
        params = {"query": vector_literal(embedding), "groups": groups, "top_k": top_k}
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(_NEAREST_SQL, params)).fetchall()
        return [
            ChunkResult(
                document_id=row[0],
                version_id=row[1],
                chunk_id=row[2],
                text=row[3],
                page_number=row[4],
                score=float(row[5]),
            )
            for row in rows
        ]
