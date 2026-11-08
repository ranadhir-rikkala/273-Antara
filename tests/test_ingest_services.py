import io

import pytest
from pypdf import PdfWriter

from common.models import (
    ChunkResult,
    EmbedBatch,
    ExtractedDocument,
    IngestRequest,
    MarkFailedRequest,
    QueueStats,
    UserContext,
    VersionStatus,
)
from common.queues import INGEST_CPU, INGEST_GPU
from ingest import services
from tests.fakes import (
    FakeChunkStore,
    FakeLifecycle,
    FakeModel,
    FakeObjectStore,
    FakePublishUnitOfWork,
    FakeQueueInspector,
    FakeSearchIndex,
    FakeVersionStore,
)

REQUEST = IngestRequest("doc-1", "ver-1", "originals/doc-1/ver-1/policy.txt", "text/plain")


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_text_and_pdf_are_extracted_and_garbage_is_rejected() -> None:
    assert services.extract_text(b"VPN policy", "text/plain") == "VPN policy"
    assert services.extract_text(blank_pdf(), "application/pdf") == ""
    with pytest.raises(services.CorruptDocumentError):
        services.extract_text(b"\xff\xfe\xfa", "text/plain")
    with pytest.raises(services.CorruptDocumentError):
        services.extract_text(b"not a pdf", "application/pdf")


def test_pages_split_into_overlapping_chunks_with_page_numbers() -> None:
    pieces = services.split_pages("a" * 25 + "\f" + "b" * 5 + "\f   ", size=10, overlap=2)
    assert [page for page, _ in pieces] == [1, 1, 1, 1, 2]
    assert pieces[0][1] == "a" * 10 and pieces[1][1].startswith("aa")
    assert pieces[-1] == (2, "bbbbb")


async def test_extract_stores_text_and_marks_the_version_processing() -> None:
    objects, versions = FakeObjectStore({REQUEST.object_key: b"VPN needs MFA"}), FakeVersionStore()
    extracted = await services.extract(REQUEST, objects, versions)
    assert extracted.text_key == "text/doc-1/ver-1.txt"
    assert objects.objects["text/doc-1/ver-1.txt"] == b"VPN needs MFA"
    assert versions.status["ver-1"] == (VersionStatus.PROCESSING, None)
    assert versions.text_keys["ver-1"] == "text/doc-1/ver-1.txt"


async def test_rechunking_a_version_replaces_its_chunks() -> None:
    objects = FakeObjectStore({"text/doc-1/ver-1.txt": ("x" * 900).encode()})
    chunks = FakeChunkStore()
    document = ExtractedDocument("doc-1", "ver-1", "text/doc-1/ver-1.txt")
    first = await services.chunk(document, objects, chunks)
    second = await services.chunk(document, objects, chunks)
    assert len(first.chunk_ids) == 2
    assert sorted(chunks.chunks) == sorted(second.chunk_ids)


async def test_embedding_queue_follows_gpu_health_and_falls_back_when_stats_fail() -> None:
    healthy = await services.select_embed_queue(FakeQueueInspector(QueueStats(backlog=3, pollers=1)), 64)
    unknown = await services.select_embed_queue(FakeQueueInspector(fail=True), 64)
    assert healthy[0] == INGEST_GPU
    assert unknown[0] == INGEST_CPU and "unavailable" in unknown[1]


async def test_embed_writes_one_vector_per_chunk_and_skips_missing_chunks() -> None:
    chunks = FakeChunkStore()
    ids = await chunks.replace_chunks("doc-1", "ver-1", [(1, "vpn policy"), (1, "laptop policy")])
    assert await services.embed(EmbedBatch("ver-1", ids), chunks, FakeModel()) == 2
    assert sorted(chunks.embeddings) == sorted(ids)
    assert await services.embed(EmbedBatch("ver-1", ["gone"]), chunks, FakeModel()) == 0


async def test_a_version_is_published_only_when_every_chunk_has_an_embedding() -> None:
    complete = FakePublishUnitOfWork({"ver-1": (3, 3)})
    result = await services.publish(REQUEST, complete)
    assert result.status == VersionStatus.READY and result.chunk_count == 3
    assert complete.active == {"doc-1": "ver-1"}
    partial = FakePublishUnitOfWork({"ver-1": (3, 2)})
    with pytest.raises(services.IncompleteVersionError, match="1 chunks"):
        await services.publish(REQUEST, partial)
    assert partial.active == {}


async def test_failure_reasons_are_truncated() -> None:
    versions = FakeVersionStore()
    await services.mark_failed(MarkFailedRequest("ver-1", "x" * 5000), versions)
    status, error = versions.status["ver-1"]
    assert status == VersionStatus.FAILED and len(error) == services.MAX_ERROR_LENGTH


async def test_purge_removes_originals_and_text_then_finishes_the_deletion() -> None:
    objects = FakeObjectStore(
        {"originals/doc-1/ver-1/a.txt": b"1", "text/doc-1/ver-1.txt": b"2", "originals/doc-2/ver-9/b.txt": b"3"}
    )
    lifecycle = FakeLifecycle()
    assert await services.purge("doc-1", lifecycle, objects) == 2
    assert list(objects.objects) == ["originals/doc-2/ver-9/b.txt"]
    assert lifecycle.events == [("start", "doc-1", None), ("finish", "doc-1", 2)]


async def test_search_needs_at_least_one_group() -> None:
    chunk = ChunkResult("doc-1", "ver-1", "c1", "VPN", 1, 0.9)
    index = FakeSearchIndex([chunk])
    assert await services.search("vpn", UserContext("nobody", []), 5, FakeModel(), index) == []
    assert index.calls == []
    assert await services.search("vpn", UserContext("alice", ["employees"]), 5, FakeModel(), index) == [chunk]
    assert index.calls == [(["employees"], 5)]
