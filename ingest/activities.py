from temporalio import activity
from temporalio.exceptions import ApplicationError

from common.config import settings
from common.db import pool
from common.llm import model_session
from common.models import (
    ChunkedDocument,
    EmbedBatch,
    ExtractedDocument,
    IngestRequest,
    IngestResult,
    MarkFailedRequest,
)
from common.names import Activities, Errors
from common.queue_stats import TemporalQueueInspector
from common.storage import S3ObjectStore
from common.temporal_client import shared
from ingest import services
from ingest.postgres import (
    PostgresChunkStore,
    PostgresDocumentLifecycle,
    PostgresPublishUnitOfWork,
    PostgresVersionStore,
)


@activity.defn(name=Activities.EXTRACT)
async def extract_document(request: IngestRequest) -> ExtractedDocument:
    try:
        return await services.extract(request, S3ObjectStore(), PostgresVersionStore(await pool()))
    except services.CorruptDocumentError as error:
        raise ApplicationError(str(error), type=Errors.CORRUPT_DOCUMENT, non_retryable=True) from error


@activity.defn(name=Activities.CHUNK)
async def chunk_document(document: ExtractedDocument) -> ChunkedDocument:
    return await services.chunk(document, S3ObjectStore(), PostgresChunkStore(await pool()))


@activity.defn(name=Activities.SELECT_EMBED_QUEUE)
async def select_embed_queue() -> str:
    queue, reason = await services.select_embed_queue(
        TemporalQueueInspector(await shared()), settings.gpu_offload_backlog
    )
    activity.logger.info("Embedding on %s: %s", queue, reason)
    return queue


@activity.defn(name=Activities.EMBED)
async def generate_embeddings(batch: EmbedBatch) -> int:
    async with model_session() as model:
        return await services.embed(batch, PostgresChunkStore(await pool()), model)


@activity.defn(name=Activities.PUBLISH)
async def publish_version(request: IngestRequest) -> IngestResult:
    try:
        return await services.publish(request, PostgresPublishUnitOfWork(await pool()))
    except services.IncompleteVersionError as error:
        raise ApplicationError(str(error)) from error


@activity.defn(name=Activities.MARK_FAILED)
async def mark_version_failed(request: MarkFailedRequest) -> None:
    await services.mark_failed(request, PostgresVersionStore(await pool()))


@activity.defn(name=Activities.PURGE)
async def purge_document(document_id: str) -> int:
    return await services.purge(document_id, PostgresDocumentLifecycle(await pool()), S3ObjectStore())
