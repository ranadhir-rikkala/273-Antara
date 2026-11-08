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


async def test_counts_only_unrevoked_grants_and_the_leases_among_them(db) -> None:
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO access_grants (run_id, requester_id, system, role, expires_at, revoked_at) VALUES "
            "('r1', 'alice', 'wiki', 'read', NULL, NULL), "
            "('r2', 'alice', 'prod-db', 'read', now() + interval '1 hour', NULL), "
            "('r3', 'alice', 'prod-db', 'write', now() - interval '1 hour', now())"
        )
    counts = await PostgresOpsStore(db).counts()
    assert (counts["access_grants"], counts["leases_active"]) == (2, 1)


async def test_counts_what_the_harness_did_from_the_audit_log(db) -> None:
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES "
            "('agent', 'harness.answered-from-sources', '{}'), ('agent', 'harness.answered-from-sources', '{}'), "
            "('agent', 'harness.secrets-redacted', '{}'), ('alice', 'agent.retrieve', '{}'), "
            "('alice', 'tool.create_ticket', '{}'), ('alice', 'tool.request_access', '{}'), "
            "('alice', 'approval.opened', '{}'), ('alice', 'document.deleted', '{}')"
        )
    harness = (await PostgresOpsStore(db).counts())["harness"]
    assert harness == {
        "answered-from-sources": 2,
        "secrets-redacted": 1,
        "searches": 1,
        "tool-actions": 2,
        "approvals": 1,
    }
