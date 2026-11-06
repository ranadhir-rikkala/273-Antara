import asyncio

from temporalio.worker import Worker

from common.config import settings
from common.queues import INGEST_GPU
from common.temporal_client import connect
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
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
