import uuid

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from common.models import (
    ChunkedDocument,
    EmbedBatch,
    ExtractedDocument,
    IngestRequest,
    IngestResult,
    MarkFailedRequest,
    VersionStatus,
)
from common.names import Activities, Errors, Workflows
from common.queues import INGEST_CPU, INGEST_GPU
from ingest.workflows import IngestWorkflow

embedded_on_gpu: list[int] = []
embedded_on_cpu: list[int] = []
busy_attempts: list[int] = []
failed_versions: list[str] = []


@activity.defn(name=Activities.EXTRACT)
async def fake_extract(request: IngestRequest) -> ExtractedDocument:
    if request.content_type == "application/broken":
        raise ApplicationError("unreadable PDF", type=Errors.CORRUPT_DOCUMENT, non_retryable=True)
    return ExtractedDocument(request.document_id, request.version_id, f"text/{request.version_id}.txt")


@activity.defn(name=Activities.CHUNK)
async def fake_chunk(document: ExtractedDocument) -> ChunkedDocument:
    return ChunkedDocument(document.version_id, [str(uuid.uuid4()) for _ in range(70)])


@activity.defn(name=Activities.SELECT_EMBED_QUEUE)
async def fake_select_queue() -> str:
    return INGEST_GPU


@activity.defn(name=Activities.EMBED)
async def fake_embed_gpu(batch: EmbedBatch) -> int:
    embedded_on_gpu.append(len(batch.chunk_ids))
    return len(batch.chunk_ids)


@activity.defn(name=Activities.EMBED)
async def fake_embed_cpu(batch: EmbedBatch) -> int:
    embedded_on_cpu.append(len(batch.chunk_ids))
    return len(batch.chunk_ids)


@activity.defn(name=Activities.PUBLISH)
async def fake_publish(request: IngestRequest) -> IngestResult:
    return IngestResult(request.version_id, sum(embedded_on_gpu), VersionStatus.READY)


@activity.defn(name=Activities.MARK_FAILED)
async def fake_mark_failed(request: MarkFailedRequest) -> None:
    failed_versions.append(request.version_id)


def _workers(env: WorkflowEnvironment) -> tuple[Worker, Worker]:
    return (
        Worker(
            env.client,
            task_queue=INGEST_CPU,
            workflows=[IngestWorkflow],
            activities=[fake_extract, fake_chunk, fake_select_queue, fake_publish, fake_mark_failed],
        ),
        Worker(env.client, task_queue=INGEST_GPU, activities=[fake_embed_gpu]),
    )


async def test_fans_out_embedding_batches_to_selected_queue_and_publishes() -> None:
    embedded_on_gpu.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        cpu_worker, gpu_worker = _workers(env)
        async with cpu_worker, gpu_worker:
            result = await env.client.execute_workflow(
                Workflows.INGEST,
                IngestRequest("doc-1", "ver-1", "originals/doc-1/ver-1/policy.pdf", "application/pdf"),
                id="ingest-ver-1",
                task_queue=INGEST_CPU,
                result_type=IngestResult,
            )
    assert sorted(embedded_on_gpu) == [6, 32, 32]
    assert result.status == VersionStatus.READY
    assert result.chunk_count == 70


async def test_corrupt_document_is_not_retried_and_is_marked_failed() -> None:
    failed_versions.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        cpu_worker, gpu_worker = _workers(env)
        async with cpu_worker, gpu_worker:
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    Workflows.INGEST,
                    IngestRequest("doc-2", "ver-2", "originals/doc-2/ver-2/bad.pdf", "application/broken"),
                    id="ingest-ver-2",
                    task_queue=INGEST_CPU,
                )
    assert failed_versions == ["ver-2"]


async def test_embedding_moves_to_cpu_when_the_gpu_lane_never_picks_it_up() -> None:
    embedded_on_gpu.clear()
    embedded_on_cpu.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        cpu_only = Worker(
            env.client,
            task_queue=INGEST_CPU,
            workflows=[IngestWorkflow],
            activities=[fake_extract, fake_chunk, fake_select_queue, fake_embed_cpu, fake_publish, fake_mark_failed],
        )
        async with cpu_only:
            result = await env.client.execute_workflow(
                Workflows.INGEST,
                IngestRequest("doc-3", "ver-3", "originals/doc-3/ver-3/policy.pdf", "application/pdf"),
                id="ingest-ver-3",
                task_queue=INGEST_CPU,
                result_type=IngestResult,
            )
    assert embedded_on_gpu == []
    assert sorted(embedded_on_cpu) == [6, 32, 32]
    assert result.offloaded_batches == 3


@activity.defn(name=Activities.EMBED)
async def busy_then_ok_embed(batch: EmbedBatch) -> int:
    attempt = activity.info().attempt
    busy_attempts.append(attempt)
    if attempt <= 8:
        raise ApplicationError("429 Too Many Requests", type="ModelBusy")
    return len(batch.chunk_ids)


@activity.defn(name=Activities.CHUNK)
async def one_chunk(document: ExtractedDocument) -> ChunkedDocument:
    return ChunkedDocument(document.version_id, ["only-chunk"])


async def test_a_busy_model_delays_embedding_but_never_fails_the_document() -> None:
    busy_attempts.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        cpu_worker = Worker(
            env.client,
            task_queue=INGEST_CPU,
            workflows=[IngestWorkflow],
            activities=[fake_extract, one_chunk, fake_select_queue, fake_publish, fake_mark_failed],
        )
        gpu_worker = Worker(env.client, task_queue=INGEST_GPU, activities=[busy_then_ok_embed])
        async with cpu_worker, gpu_worker:
            result = await env.client.execute_workflow(
                Workflows.INGEST,
                IngestRequest("doc-4", "ver-4", "originals/doc-4/ver-4/a.txt", "text/plain"),
                id="ingest-ver-4",
                task_queue=INGEST_CPU,
                result_type=IngestResult,
            )
    assert result.status == VersionStatus.READY
    assert max(busy_attempts) == 9
