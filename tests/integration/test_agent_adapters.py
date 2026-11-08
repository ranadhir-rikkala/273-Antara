import pytest

from agent.postgres import (
    PostgresAnswerCache,
    PostgresApprovalStore,
    PostgresAuditLog,
    PostgresIncidentUnitOfWork,
    PostgresToolUnitOfWork,
)
from api.postgres import PostgresDocumentCatalog
from common.models import ChunkResult, ToolCall
from tests.integration.conftest import vector

pytestmark = pytest.mark.integration


async def count(db, sql: str) -> int:
    async with db.connection() as conn:
        return (await (await conn.execute(sql)).fetchone())[0]


async def test_completed_tool_action_is_returned_instead_of_running_again(db) -> None:
    unit_of_work = PostgresToolUnitOfWork(db)
    async with unit_of_work.begin() as workspace:
        assert await workspace.claim("run-1:0", "create_ticket", {"title": "T"}, "alice") is None
        ticket = await workspace.tickets.create("T", "D", "low", "alice")
        await workspace.complete("run-1:0", ticket)
    async with unit_of_work.begin() as workspace:
        assert await workspace.claim("run-1:0", "create_ticket", {"title": "T"}, "alice") == ticket
    assert await count(db, "SELECT count(*) FROM tickets") == 1


async def test_a_failure_inside_the_unit_of_work_rolls_back_the_ticket_and_the_claim(db) -> None:
    unit_of_work = PostgresToolUnitOfWork(db)
    with pytest.raises(RuntimeError):
        async with unit_of_work.begin() as workspace:
            await workspace.claim("run-2:0", "create_ticket", {}, "alice")
            await workspace.tickets.create("T", "D", "low", "alice")
            raise RuntimeError("crash before commit")
    assert await count(db, "SELECT count(*) FROM tickets") == 0
    assert await count(db, "SELECT count(*) FROM tool_actions") == 0


async def test_ticket_lookup_is_scoped_to_the_requester_and_tolerates_bad_ids(db) -> None:
    unit_of_work = PostgresToolUnitOfWork(db)
    async with unit_of_work.begin() as workspace:
        ticket = await workspace.tickets.create("Screen", "Does not lock", "high", "alice")
        assert (await workspace.tickets.find_for_requester(ticket["ticket_id"], "alice"))["title"] == "Screen"
        assert await workspace.tickets.find_for_requester(ticket["ticket_id"], "bob") is None
        assert await workspace.tickets.find_for_requester("not-a-uuid", "alice") is None


async def test_granting_access_marks_the_approved_request_granted(db) -> None:
    store = PostgresApprovalStore(db)
    approval_id = await store.open(
        "run-3:0", "run-3", "alice", ToolCall("request_access", {"system": "vpn", "role": "user"})
    )
    assert await store.open("run-3:0", "run-3", "alice", ToolCall("request_access", {})) == approval_id
    await store.escalate(approval_id)
    async with db.connection() as conn:
        await conn.execute("UPDATE approvals SET status = 'APPROVED' WHERE approval_id = %s", (approval_id,))
    async with PostgresToolUnitOfWork(db).begin() as workspace:
        await workspace.grants.grant("run-3", "alice", "vpn", "user", 600)
    async with db.connection() as conn:
        status, escalated = await (
            await conn.execute("SELECT status, escalated FROM approvals WHERE approval_id = %s", (approval_id,))
        ).fetchone()
    assert (status, escalated) == ("GRANTED", True)


async def test_incidents_merge_by_vector_similarity_inside_the_lock(db) -> None:
    unit_of_work = PostgresIncidentUnitOfWork(db)
    async with unit_of_work.begin_exclusive() as workspace:
        incident_id = await workspace.open_incident("VPN is down", vector(1), "vpn")
        unknown_id = await workspace.open_incident("Something odd", vector(3), None)
        assert await workspace.add_report("r1", incident_id, "alice", "VPN is down") == 1
        assert await workspace.find_open_for_system("vpn", 60) == (incident_id, "VPN is down")
        assert await workspace.find_open_for_system("email", 60) is None
        assert await workspace.find_similar_open(vector(3), 60, 0.8) == (unknown_id, "Something odd")
        assert await workspace.find_similar_open(vector(1), 60, 0.8) is None
        assert await workspace.add_report("r2", incident_id, "bob", "vpn down") == 2
        assert await workspace.add_report("r2b", incident_id, "bob", "vpn still down") == 2
        found = await workspace.find_report("r2")
        await workspace.record("bob", "incident.linked", {"incident_id": incident_id})
    assert found.incident_id == incident_id and found.reporter_count == 2
    assert await count(db, "SELECT count(*) FROM audit_log WHERE action = 'incident.linked'") == 1


async def test_cached_answers_expire_when_the_cited_document_is_deleted(db) -> None:
    catalog = PostgresDocumentCatalog(db)
    await catalog.create_upload(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-0000000000a1",
        "alice",
        "vpn.md",
        "originals/x",
        "text/plain",
        ["employees"],
    )
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE documents SET active_version_id = '00000000-0000-0000-0000-0000000000a1' "
            "WHERE document_id = '00000000-0000-0000-0000-000000000001'"
        )
    cache = PostgresAnswerCache(db)
    citation = ChunkResult(
        "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-0000000000a1", "c1", "VPN", 1, 0.9
    )
    await cache.save("vpn?", vector(5), "employees", "Use the helpdesk [1]", [citation], [citation.version_id])
    hit = await cache.find("employees", vector(5), 24, 0.95)
    assert hit.answer == "Use the helpdesk [1]" and hit.citations == [citation]
    assert await cache.find("contractors", vector(5), 24, 0.95) is None
    async with db.connection() as conn:
        await conn.execute("UPDATE documents SET deleted_at = now()")
    assert await cache.find("employees", vector(5), 24, 0.95) is None


async def test_audit_log_records_entries(db) -> None:
    await PostgresAuditLog(db).record("alice", "agent.retrieve", {"query": "vpn"})
    assert await count(db, "SELECT count(*) FROM audit_log") == 1


async def test_expiring_only_touches_pending_approvals(db) -> None:
    store = PostgresApprovalStore(db)
    pending = await store.open(
        "run-7:0", "run-7", "alice", ToolCall("request_access", {"system": "vpn", "role": "read"})
    )
    decided = await store.open(
        "run-8:0", "run-8", "alice", ToolCall("request_access", {"system": "vpn", "role": "read"})
    )
    async with db.connection() as conn:
        await conn.execute("UPDATE approvals SET status = 'APPROVED' WHERE approval_id = %s", (decided,))
    await store.expire(pending)
    await store.expire(decided)
    async with db.connection() as conn:
        rows = dict(await (await conn.execute("SELECT run_id, status FROM approvals")).fetchall())
    assert rows == {"run-7": "EXPIRED", "run-8": "APPROVED"}


async def test_a_grant_expires_after_its_lease_and_is_revoked_only_once(db) -> None:
    async with PostgresToolUnitOfWork(db).begin() as workspace:
        grant_id = await workspace.grants.grant("run-4", "alice", "prod-db", "read", 3600)
    async with db.connection() as conn:
        lease = await (
            await conn.execute(
                "SELECT extract(epoch FROM expires_at - granted_at)::int FROM access_grants WHERE grant_id = %s",
                (grant_id,),
            )
        ).fetchone()
    assert lease == (3600,)
    async with PostgresToolUnitOfWork(db).begin() as workspace:
        assert await workspace.grants.revoke(grant_id) is True
        assert await workspace.grants.revoke(grant_id) is False
    async with db.connection() as conn:
        revoked = await (
            await conn.execute("SELECT revoked_at IS NOT NULL FROM access_grants WHERE grant_id = %s", (grant_id,))
        ).fetchone()
    assert revoked == (True,)
