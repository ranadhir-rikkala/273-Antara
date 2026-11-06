import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from common.config import settings
    from common.models import (
        ChunkedDocument,
        EmbedBatch,
        ExtractedDocument,
        IngestRequest,
        IngestResult,
        MarkFailedRequest,
    )
    from common.names import Activities, Workflows
    from common.queues import INGEST_CPU
    from common.retry import DEFAULT_RETRY


@workflow.defn(name=Workflows.INGEST)
class IngestWorkflow:
    @workflow.run
    async def run(self, request: IngestRequest) -> IngestResult:
        try:
            extracted = await workflow.execute_activity(
                Activities.EXTRACT,
                request,
                result_type=ExtractedDocument,
                task_queue=INGEST_CPU,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=DEFAULT_RETRY,
            )
            chunked = await workflow.execute_activity(
                Activities.CHUNK,
                extracted,
                result_type=ChunkedDocument,
                task_queue=INGEST_CPU,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=DEFAULT_RETRY,
            )
            embed_queue = await workflow.execute_activity(
                Activities.SELECT_EMBED_QUEUE,
                result_type=str,
                task_queue=INGEST_CPU,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
            size = settings.embed_batch_size
            batches = [
                EmbedBatch(version_id=request.version_id, chunk_ids=chunked.chunk_ids[start : start + size])
                for start in range(0, len(chunked.chunk_ids), size)
            ]
            await asyncio.gather(
                *(
                    workflow.execute_activity(
                        Activities.EMBED,
                        batch,
                        result_type=int,
                        task_queue=embed_queue,
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=DEFAULT_RETRY,
                    )
                    for batch in batches
                )
            )
            return await workflow.execute_activity(
                Activities.PUBLISH,
                request,
                result_type=IngestResult,
                task_queue=INGEST_CPU,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_RETRY,
            )
        except ActivityError as error:
            await workflow.execute_activity(
                Activities.MARK_FAILED,
                MarkFailedRequest(version_id=request.version_id, reason=str(error.cause or error)),
                task_queue=INGEST_CPU,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
            raise


@workflow.defn(name=Workflows.DELETE_DOCUMENT)
class DeleteDocumentWorkflow:
    @workflow.run
    async def run(self, document_id: str) -> int:
        return await workflow.execute_activity(
            Activities.PURGE,
            document_id,
            result_type=int,
            task_queue=INGEST_CPU,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
