import asyncio
from datetime import timedelta

from temporalio.worker import Worker

from common.config import settings
from common.queues import INGEST_GPU
from common.temporal_client import connect
from common.worker_runtime import run_until_stopped
from ingest.activities import (
    chunk_document,
    extract_document,
    generate_embeddings,
    mark_version_failed,
    publish_version,
    purge_document,
    select_embed_queue,
)
from ingest.workflows import DeleteDocumentWorkflow, IngestWorkflow


async def main() -> None:
    client = await connect()
    queue = settings.worker_queue
    if queue == INGEST_GPU:
        workflows = []
        activities = [generate_embeddings]
    else:
        workflows = [IngestWorkflow, DeleteDocumentWorkflow]
        activities = [
            extract_document,
            chunk_document,
            select_embed_queue,
            generate_embeddings,
            publish_version,
            mark_version_failed,
            purge_document,
        ]
    worker = Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=activities,
        max_concurrent_activities=settings.max_concurrent_activities,
        graceful_shutdown_timeout=timedelta(seconds=25),
    )
    await run_until_stopped(worker)


if __name__ == "__main__":
    asyncio.run(main())
