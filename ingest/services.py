from common.models import (
    ChunkedDocument,
    ChunkResult,
    EmbedBatch,
    ExtractedDocument,
    IngestRequest,
    IngestResult,
    MarkFailedRequest,
    UserContext,
    VersionStatus,
)
from common.queues import INGEST_CPU, INGEST_GPU
from ingest.dispatch import choose_embed_queue
from ingest.ports import (
    ChunkStore,
    DocumentLifecycle,
    Embedder,
    ObjectStore,
    PdfPages,
    PublishUnitOfWork,
    QueueInspector,
    SearchIndex,
    VersionStore,
)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
PAGE_BREAK = "\f"
MAX_ERROR_LENGTH = 2000


class CorruptDocumentError(Exception):
    pass


class IncompleteVersionError(Exception):
    pass


def extract_text(data: bytes, content_type: str, pdf: PdfPages) -> str:
    if content_type == "application/pdf":
        return PAGE_BREAK.join(pdf.pages(data))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorruptDocumentError(str(error)) from error


def split_pages(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, str]]:
    pieces: list[tuple[int, str]] = []
    for page_number, page in enumerate(text.split(PAGE_BREAK), start=1):
        page = page.strip()
        start = 0
        while start < len(page):
            pieces.append((page_number, page[start : start + size]))
            start += size - overlap
    return pieces


def text_key_for(document_id: str, version_id: str) -> str:
    return f"text/{document_id}/{version_id}.txt"


def object_prefixes(document_id: str) -> list[str]:
    return [f"originals/{document_id}/", f"text/{document_id}/"]


async def extract(
    request: IngestRequest, objects: ObjectStore, versions: VersionStore, pdf: PdfPages
) -> ExtractedDocument:
    await versions.set_status(request.version_id, VersionStatus.PROCESSING)
    text = extract_text(await objects.get(request.object_key), request.content_type, pdf)
    text_key = text_key_for(request.document_id, request.version_id)
    await objects.put(text_key, text.encode("utf-8"), "text/plain")
    await versions.set_text_key(request.version_id, text_key)
    return ExtractedDocument(document_id=request.document_id, version_id=request.version_id, text_key=text_key)


async def chunk(document: ExtractedDocument, objects: ObjectStore, chunks: ChunkStore) -> ChunkedDocument:
    text = (await objects.get(document.text_key)).decode("utf-8")
    chunk_ids = await chunks.replace_chunks(document.document_id, document.version_id, split_pages(text))
    return ChunkedDocument(version_id=document.version_id, chunk_ids=chunk_ids)


async def select_embed_queue(inspector: QueueInspector, max_gpu_backlog: int) -> tuple[str, str]:
    try:
        stats = await inspector.stats(INGEST_GPU)
    except Exception as error:
        return INGEST_CPU, f"GPU queue stats unavailable: {error}"
    return choose_embed_queue(stats, max_gpu_backlog)


async def embed(batch: EmbedBatch, chunks: ChunkStore, embedder: Embedder) -> int:
    rows = await chunks.texts(batch.chunk_ids)
    if not rows:
        return 0
    vectors = await embedder.embed([text for _, text in rows])
    await chunks.save_embeddings([(chunk_id, vector) for (chunk_id, _), vector in zip(rows, vectors, strict=True)])
    return len(rows)


async def publish(request: IngestRequest, unit_of_work: PublishUnitOfWork) -> IngestResult:
    async with unit_of_work.begin() as workspace:
        chunk_count, embedded = await workspace.embedding_counts(request.version_id)
        if chunk_count != embedded:
            raise IncompleteVersionError(f"{chunk_count - embedded} chunks are missing embeddings")
        await workspace.activate(request.document_id, request.version_id)
    return IngestResult(version_id=request.version_id, chunk_count=chunk_count, status=VersionStatus.READY)


async def mark_failed(request: MarkFailedRequest, versions: VersionStore) -> None:
    await versions.set_status(request.version_id, VersionStatus.FAILED, request.reason[:MAX_ERROR_LENGTH])


async def purge(document_id: str, lifecycle: DocumentLifecycle, objects: ObjectStore) -> int:
    await lifecycle.start_deletion(document_id)
    removed = 0
    for prefix in object_prefixes(document_id):
        removed += await objects.delete_prefix(prefix)
    await lifecycle.finish_deletion(document_id, removed)
    return removed


async def search(
    query: str, user: UserContext, top_k: int, embedder: Embedder, index: SearchIndex
) -> list[ChunkResult]:
    if not user.groups:
        return []
    [embedding] = await embedder.embed([query])
    return await index.nearest(embedding, user.groups, top_k)
