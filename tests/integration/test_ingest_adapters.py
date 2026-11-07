import pytest

from api.postgres import PostgresApprovalInbox, PostgresDocumentCatalog
from ingest.postgres import (
    PostgresChunkStore,
    PostgresDocumentLifecycle,
    PostgresPublishUnitOfWork,
    PostgresSearchIndex,
    PostgresVersionStore,
)
from reconciler.adapters import PostgresVersionScanner
from tests.integration.conftest import vector

pytestmark = pytest.mark.integration

PUBLIC = ("00000000-0000-0000-0000-000000000010", "00000000-0000-0000-0000-0000000000b0")
SECRET = ("00000000-0000-0000-0000-000000000020", "00000000-0000-0000-0000-0000000000c0")


async def ingest(db, document: tuple[str, str], groups: list[str], hot: int) -> list[str]:
    document_id, version_id = document
    await PostgresDocumentCatalog(db).create_upload(
        document_id, version_id, "alice", f"{document_id}.md", f"originals/{document_id}", "text/plain", groups
    )
    chunks = PostgresChunkStore(db)
    ids = await chunks.replace_chunks(document_id, version_id, [(1, "first"), (2, "second")])
    await chunks.save_embeddings([(chunk_id, vector(hot)) for chunk_id in ids])
    async with PostgresPublishUnitOfWork(db).begin() as workspace:
        assert await workspace.embedding_counts(version_id) == (2, 2)
        await workspace.activate(document_id, version_id)
    return ids


async def test_chunks_are_replaced_per_version_and_texts_come_back_in_order(db) -> None:
    await PostgresDocumentCatalog(db).create_upload(
        *PUBLIC, "alice", "a.md", "originals/a", "text/plain", ["employees"]
    )
    chunks = PostgresChunkStore(db)
    await chunks.replace_chunks(*PUBLIC, [(1, "old")])
    ids = await chunks.replace_chunks(*PUBLIC, [(1, "new one"), (1, "new two")])
    assert await chunks.texts(ids) == [(ids[0], "new one"), (ids[1], "new two")]
    async with PostgresPublishUnitOfWork(db).begin() as workspace:
        assert await workspace.embedding_counts(PUBLIC[1]) == (2, 0)


async def test_search_respects_document_permissions_and_ranks_by_similarity(db) -> None:
    await ingest(db, PUBLIC, ["employees", "contractors"], hot=1)
    await ingest(db, SECRET, ["employees"], hot=2)
    index = PostgresSearchIndex(db)
    contractor = await index.nearest(vector(2), ["contractors"], 5)
    employee = await index.nearest(vector(2), ["employees"], 5)
    assert {chunk.document_id for chunk in contractor} == {PUBLIC[0]}
    assert employee[0].document_id == SECRET[0] and employee[0].score == pytest.approx(1.0)


async def test_deletion_removes_chunks_and_hides_the_document(db) -> None:
    await ingest(db, PUBLIC, ["employees"], hot=1)
    lifecycle = PostgresDocumentLifecycle(db)
    await lifecycle.start_deletion(PUBLIC[0])
    await lifecycle.finish_deletion(PUBLIC[0], 2)
    assert await PostgresSearchIndex(db).nearest(vector(1), ["employees"], 5) == []
    document = await PostgresDocumentCatalog(db).get(PUBLIC[0])
    assert document["deleted"] is True and document["versions"][0]["status"] == "DELETED"
    assert await PostgresDocumentCatalog(db).list_for_groups(["employees"]) == []


async def test_catalog_lists_by_group_and_reports_missing_documents(db) -> None:
    await ingest(db, PUBLIC, ["contractors"], hot=1)
    assert [doc["document_id"] for doc in await PostgresDocumentCatalog(db).list_for_groups(["contractors"])] == [
        PUBLIC[0]
    ]
    assert await PostgresDocumentCatalog(db).list_for_groups(["employees"]) == []
    assert await PostgresDocumentCatalog(db).get("missing") is None


async def test_version_status_and_reconciler_scan(db) -> None:
    await PostgresDocumentCatalog(db).create_upload(
        *PUBLIC, "alice", "a.md", "originals/a", "text/plain", ["employees"]
    )
    versions = PostgresVersionStore(db)
    await versions.set_status(PUBLIC[1], "PROCESSING")
    await versions.set_text_key(PUBLIC[1], "text/a.txt")
    assert await PostgresDocumentCatalog(db).count_in_flight() == 1
    stale = await PostgresVersionScanner(db).stale_versions(["PROCESSING"], 0)
    assert [request.version_id for request in stale] == [PUBLIC[1]]
    assert await PostgresVersionScanner(db).stale_versions(["PROCESSING"], 60) == []


async def test_approval_inbox_lists_and_records_decisions(db) -> None:
    async with db.connection() as conn:
        approval_id = (
            await (
                await conn.execute(
                    "INSERT INTO approvals (requester_id, system, role, status, run_id, escalated) "
                    "VALUES ('alice', 'vpn', 'user', 'PENDING', 'run-9', true) RETURNING approval_id::text"
                )
            ).fetchone()
        )[0]
    inbox = PostgresApprovalInbox(db)
    assert [row["approval_id"] for row in await inbox.pending(escalated_only=True)] == [approval_id]
    async with inbox.begin() as workspace:
        assert await workspace.lock(approval_id) == ("run-9", "PENDING", True, "alice")
        assert await workspace.lock("missing") is None
        await workspace.record(approval_id, "dave", True)
    assert await inbox.pending(escalated_only=False) == []
