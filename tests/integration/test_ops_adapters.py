import pytest

from api.ops.adapters import PostgresOpsStore
from tests.integration.test_ingest_adapters import PUBLIC, ingest

pytestmark = pytest.mark.integration


async def test_corrupting_hides_the_document_and_marks_it_stale_for_the_reconciler(db) -> None:
    await ingest(db, PUBLIC, ["employees"], hot=1)
    store = PostgresOpsStore(db)
    broken = await store.corrupt_latest_document()
    assert broken["version_id"] == PUBLIC[1] and broken["embeddings_deleted"] == 2
    async with db.connection() as conn:
        active, status, stale = await (
            await conn.execute(
                "SELECT d.active_version_id, v.status, v.updated_at < now() - interval '30 minutes' "
                "FROM documents d JOIN document_versions v ON v.document_id = d.document_id"
            )
        ).fetchone()
    assert (active, status, stale) == (None, "PROCESSING", True)
    assert await store.corrupt_latest_document() is None


async def test_counts_incidents_and_events(db) -> None:
    store = PostgresOpsStore(db)
    await ingest(db, PUBLIC, ["employees"], hot=1)
    await store.record("chaos.kill-worker", "erin", {"target": "agent"})
    counts = await store.counts()
    assert counts["documents"] == {"READY": 1} and counts["approvals_pending"] == 0
    assert (await store.events(5))[0]["kind"] == "chaos.kill-worker"
    assert await store.open_incidents(5) == []
