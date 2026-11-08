import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, TimeoutType
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

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
    from common.queues import INGEST_CPU, INGEST_GPU
    from common.retry import DEFAULT_RETRY, PATIENT_DEADLINE, PATIENT_RETRY


GPU_PICKUP_TIMEOUT = timedelta(seconds=20)


def never_picked_up(error: ActivityError) -> bool:
    cause = error.cause
    return isinstance(cause, TemporalTimeoutError) and cause.type == TimeoutType.SCHEDULE_TO_START


@workflow.defn(name=Workflows.INGEST)
class IngestWorkflow:
    def __init__(self) -> None:
        self._offloaded = 0

    async def _embed(self, batch: EmbedBatch, queue: str) -> int:
        try:
            return await workflow.execute_activity(
                Activities.EMBED,
                batch,
                result_type=int,
                task_queue=queue,
                schedule_to_start_timeout=GPU_PICKUP_TIMEOUT if queue == INGEST_GPU else None,
                schedule_to_close_timeout=PATIENT_DEADLINE,
                start_to_close_timeout=timedelta(minutes=5),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=PATIENT_RETRY,
            )
        except ActivityError as error:
            if queue != INGEST_GPU or not never_picked_up(error):
                raise
            self._offloaded += 1
            return await self._embed(batch, INGEST_CPU)

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
            await asyncio.gather(*(self._embed(batch, embed_queue) for batch in batches))
            result = await workflow.execute_activity(
                Activities.PUBLISH,
                request,
                result_type=IngestResult,
                task_queue=INGEST_CPU,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_RETRY,
            )
            result.offloaded_batches = self._offloaded
            return result
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
