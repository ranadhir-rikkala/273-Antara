import io
import uuid

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError

from common import storage
from common.config import settings
from common.db import pool, vector_literal
from common.llm import LLMProvider
from common.models import (
    ChunkedDocument,
    EmbedBatch,
    ExtractedDocument,
    IngestRequest,
    IngestResult,
    MarkFailedRequest,
    VersionStatus,
)
from common.names import Activities, Errors
from common.queue_stats import queue_stats
from common.queues import INGEST_CPU, INGEST_GPU
from common.temporal_client import shared
from ingest.dispatch import choose_embed_queue

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
PAGE_BREAK = "\f"


def _extract_text(data: bytes, content_type: str) -> str:
    if content_type == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            return PAGE_BREAK.join(page.extract_text() or "" for page in reader.pages)
        except PdfReadError as error:
            raise ApplicationError(str(error), type=Errors.CORRUPT_DOCUMENT, non_retryable=True) from error
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ApplicationError(str(error), type=Errors.CORRUPT_DOCUMENT, non_retryable=True) from error


def _split(text: str) -> list[tuple[int, str]]:
    pieces: list[tuple[int, str]] = []
    for page_number, page in enumerate(text.split(PAGE_BREAK), start=1):
        page = page.strip()
        start = 0
        while start < len(page):
            pieces.append((page_number, page[start : start + CHUNK_SIZE]))
            start += CHUNK_SIZE - CHUNK_OVERLAP
    return pieces


async def _set_status(version_id: str, status: VersionStatus, error: str | None = None) -> None:
    db = await pool()
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE document_versions SET status = %s, error = %s, updated_at = now() WHERE version_id = %s",
            (status, error, version_id),
        )


@activity.defn(name=Activities.EXTRACT)
async def extract_document(request: IngestRequest) -> ExtractedDocument:
    await _set_status(request.version_id, VersionStatus.PROCESSING)
    data = await storage.get_bytes(request.object_key)
    text = _extract_text(data, request.content_type)
    text_key = f"text/{request.document_id}/{request.version_id}.txt"
    await storage.put_bytes(text_key, text.encode("utf-8"), "text/plain")
    db = await pool()
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE document_versions SET text_key = %s, updated_at = now() WHERE version_id = %s",
            (text_key, request.version_id),
        )
    return ExtractedDocument(document_id=request.document_id, version_id=request.version_id, text_key=text_key)


@activity.defn(name=Activities.CHUNK)
async def chunk_document(document: ExtractedDocument) -> ChunkedDocument:
    text = (await storage.get_bytes(document.text_key)).decode("utf-8")
    pieces = _split(text)
    chunk_ids = [str(uuid.uuid4()) for _ in pieces]
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        await conn.execute("DELETE FROM document_chunks WHERE version_id = %s", (document.version_id,))
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO document_chunks (chunk_id, document_id, version_id, chunk_index, page_number, text) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (chunk_id, document.document_id, document.version_id, index, page_number, piece)
                    for index, (chunk_id, (page_number, piece)) in enumerate(zip(chunk_ids, pieces, strict=True))
                ],
            )
    return ChunkedDocument(version_id=document.version_id, chunk_ids=chunk_ids)


@activity.defn(name=Activities.SELECT_EMBED_QUEUE)
async def select_embed_queue() -> str:
    try:
        stats = await queue_stats(await shared(), INGEST_GPU)
    except RPCError as error:
        activity.logger.warning("Queue stats unavailable, embedding on CPU: %s", error)
        return INGEST_CPU
    queue, reason = choose_embed_queue(stats, settings.gpu_offload_backlog)
    activity.logger.info("Embedding on %s: %s", queue, reason)
    return queue


@activity.defn(name=Activities.EMBED)
async def generate_embeddings(batch: EmbedBatch) -> int:
    db = await pool()
    async with db.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT chunk_id::text, text FROM document_chunks "
                "WHERE chunk_id = ANY(%s::uuid[]) ORDER BY chunk_index",
                (batch.chunk_ids,),
            )
        ).fetchall()
    if not rows:
        return 0
    llm = LLMProvider()
    try:
        vectors = await llm.embed([row[1] for row in rows])
    finally:
        await llm.aclose()
    async with db.connection() as conn, conn.cursor() as cursor:
        await cursor.executemany(
            "INSERT INTO document_embeddings (chunk_id, embedding) VALUES (%s, %s::vector) "
            "ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding",
            [(row[0], vector_literal(vector)) for row, vector in zip(rows, vectors, strict=True)],
        )
    return len(rows)


@activity.defn(name=Activities.PUBLISH)
async def publish_version(request: IngestRequest) -> IngestResult:
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        counts = await (
            await conn.execute(
                "SELECT count(c.chunk_id), count(e.chunk_id) FROM document_chunks c "
                "LEFT JOIN document_embeddings e ON e.chunk_id = c.chunk_id WHERE c.version_id = %s",
                (request.version_id,),
            )
        ).fetchone()
        chunk_count, embedded_count = counts
        if chunk_count != embedded_count:
            raise ApplicationError(f"{chunk_count - embedded_count} chunks are missing embeddings")
        await conn.execute(
            "UPDATE document_versions SET status = %s, updated_at = now() WHERE version_id = %s",
            (VersionStatus.READY, request.version_id),
        )
        await conn.execute(
            "UPDATE documents SET active_version_id = %s WHERE document_id = %s",
            (request.version_id, request.document_id),
        )
    return IngestResult(version_id=request.version_id, chunk_count=chunk_count, status=VersionStatus.READY)


@activity.defn(name=Activities.MARK_FAILED)
async def mark_version_failed(request: MarkFailedRequest) -> None:
    await _set_status(request.version_id, VersionStatus.FAILED, request.reason[:2000])


@activity.defn(name=Activities.PURGE)
async def purge_document(document_id: str) -> int:
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO deletion_jobs (document_id, status) VALUES (%s, 'RUNNING') "
            "ON CONFLICT (document_id) DO UPDATE SET status = 'RUNNING', started_at = now()",
            (document_id,),
        )
        await conn.execute(
            "UPDATE document_versions SET status = %s, updated_at = now() WHERE document_id = %s",
            (VersionStatus.DELETING, document_id),
        )
    removed_objects = await storage.delete_prefix(f"originals/{document_id}/")
    removed_objects += await storage.delete_prefix(f"text/{document_id}/")
    async with db.connection() as conn, conn.transaction():
        await conn.execute(
            "UPDATE documents SET active_version_id = NULL, deleted_at = now() WHERE document_id = %s", (document_id,)
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
    return removed_objects
