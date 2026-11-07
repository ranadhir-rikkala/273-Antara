from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from common.db import vector_literal
from common.models import CachedAnswer, ChunkResult, IncidentResult, ToolCall

INCIDENT_DEDUP_LOCK = "antara-incident-dedup"

_FIND_CACHED_SQL = """
SELECT c.cache_id, c.answer, c.citations
FROM answer_cache c
WHERE c.groups_key = %(groups_key)s
  AND c.created_at > now() - make_interval(hours => %(ttl)s)
  AND 1 - (c.embedding <=> %(embedding)s::vector) >= %(threshold)s
  AND NOT EXISTS (
      SELECT 1 FROM unnest(c.version_ids) AS cited(version_id)
      WHERE NOT EXISTS (
          SELECT 1 FROM documents d
          WHERE d.active_version_id = cited.version_id AND d.deleted_at IS NULL
      )
  )
ORDER BY c.embedding <=> %(embedding)s::vector
LIMIT 1
"""

_FIND_SIMILAR_INCIDENT_SQL = """
SELECT incident_id::text, title
FROM incidents
WHERE status = 'OPEN'
  AND system IS NULL
  AND updated_at > now() - make_interval(mins => %(window)s)
  AND 1 - (embedding <=> %(embedding)s::vector) >= %(threshold)s
ORDER BY embedding <=> %(embedding)s::vector
LIMIT 1
"""


async def _record(conn: AsyncConnection, actor_id: str, action: str, detail: dict[str, Any]) -> None:
    await conn.execute(
        "INSERT INTO audit_log (actor_id, action, detail) VALUES (%s, %s, %s)", (actor_id, action, Jsonb(detail))
    )


class PostgresAuditLog:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def record(self, actor_id: str, action: str, detail: dict[str, Any]) -> None:
        async with self._pool.connection() as conn:
            await _record(conn, actor_id, action, detail)


class _ConnectionAudit:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def record(self, actor_id: str, action: str, detail: dict[str, Any]) -> None:
        await _record(self._conn, actor_id, action, detail)


class _Tickets:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(self, title: str, description: str, priority: str, requester_id: str) -> dict[str, Any]:
        row = await (
            await self._conn.execute(
                "INSERT INTO tickets (title, description, priority, requester_id) VALUES (%s, %s, %s, %s) "
                "RETURNING ticket_id::text, status",
                (title, description, priority, requester_id),
            )
        ).fetchone()
        return {"ticket_id": row[0], "status": row[1]}

    async def find_for_requester(self, ticket_id: str, requester_id: str) -> dict[str, Any] | None:
        row = await (
            await self._conn.execute(
                "SELECT ticket_id::text, status, title FROM tickets WHERE ticket_id::text = %s AND requester_id = %s",
                (ticket_id, requester_id),
            )
        ).fetchone()
        return None if row is None else {"ticket_id": row[0], "status": row[1], "title": row[2]}


class _Grants:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def grant(self, run_id: str, requester_id: str, system: str, role: str) -> str:
        row = await (
            await self._conn.execute(
                "INSERT INTO access_grants (run_id, requester_id, system, role) VALUES (%s, %s, %s, %s) "
                "RETURNING grant_id::text",
                (run_id, requester_id, system, role),
            )
        ).fetchone()
        await self._conn.execute(
            "UPDATE approvals SET status = 'GRANTED' WHERE run_id = %s AND status = 'APPROVED'", (run_id,)
        )
        return row[0]


class _ToolWorkspace:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn
        self.tickets = _Tickets(conn)
        self.grants = _Grants(conn)
        self.audit = _ConnectionAudit(conn)

    async def claim(self, key: str, tool: str, arguments: dict[str, Any], user_id: str) -> dict[str, Any] | None:
        await self._conn.execute(
            "INSERT INTO tool_actions (idempotency_key, tool, arguments, user_id, status) "
            "VALUES (%s, %s, %s, %s, 'STARTED') ON CONFLICT (idempotency_key) DO NOTHING",
            (key, tool, Jsonb(arguments), user_id),
        )
        status, result = await (
            await self._conn.execute(
                "SELECT status, result FROM tool_actions WHERE idempotency_key = %s FOR UPDATE", (key,)
            )
        ).fetchone()
        return result if status == "COMPLETED" else None

    async def complete(self, key: str, result: dict[str, Any]) -> None:
        await self._conn.execute(
            "UPDATE tool_actions SET status = 'COMPLETED', result = %s, completed_at = now() "
            "WHERE idempotency_key = %s",
            (Jsonb(result), key),
        )


class PostgresToolUnitOfWork:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_ToolWorkspace]:
        async with self._pool.connection() as conn, conn.transaction():
            yield _ToolWorkspace(conn)


class PostgresApprovalStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def open(self, approval_key: str, run_id: str, requester_id: str, call: ToolCall) -> str:
        args = call.arguments
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO approvals (approval_key, run_id, requester_id, system, role, reason, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'PENDING') "
                    "ON CONFLICT (approval_key) DO UPDATE SET approval_key = EXCLUDED.approval_key "
                    "RETURNING approval_id::text",
                    (
                        approval_key,
                        run_id,
                        requester_id,
                        args.get("system", ""),
                        args.get("role", ""),
                        args.get("reason", ""),
                    ),
                )
            ).fetchone()
        return row[0]

    async def escalate(self, approval_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE approvals SET escalated = true WHERE approval_id = %s AND status = 'PENDING'", (approval_id,)
            )


class _IncidentWorkspace:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def find_report(self, report_id: str) -> IncidentResult | None:
        row = await (
            await self._conn.execute(
                "SELECT i.incident_id::text, i.title, i.reporter_count FROM incident_reports r "
                "JOIN incidents i ON i.incident_id = r.incident_id WHERE r.report_id = %s",
                (report_id,),
            )
        ).fetchone()
        return None if row is None else IncidentResult(row[0], row[1], created=False, reporter_count=row[2])

    async def find_similar_open(
        self, embedding: Sequence[float], window_minutes: int, threshold: float
    ) -> tuple[str, str] | None:
        row = await (
            await self._conn.execute(
                _FIND_SIMILAR_INCIDENT_SQL,
                {"window": window_minutes, "embedding": vector_literal(embedding), "threshold": threshold},
            )
        ).fetchone()
        return None if row is None else (row[0], row[1])

    async def find_open_for_system(self, system: str, window_minutes: int) -> tuple[str, str] | None:
        row = await (
            await self._conn.execute(
                "SELECT incident_id::text, title FROM incidents WHERE status = 'OPEN' AND system = %s "
                "AND updated_at > now() - make_interval(mins => %s) ORDER BY updated_at DESC LIMIT 1",
                (system, window_minutes),
            )
        ).fetchone()
        return None if row is None else (row[0], row[1])

    async def open_incident(self, title: str, embedding: Sequence[float], system: str | None) -> str:
        row = await (
            await self._conn.execute(
                "INSERT INTO incidents (title, embedding, system) VALUES (%s, %s::vector, %s) "
                "RETURNING incident_id::text",
                (title, vector_literal(embedding), system),
            )
        ).fetchone()
        return row[0]

    async def add_report(self, report_id: str, incident_id: str, reporter_id: str, text: str) -> int:
        await self._conn.execute(
            "INSERT INTO incident_reports (report_id, incident_id, reporter_id, text) VALUES (%s, %s, %s, %s)",
            (report_id, incident_id, reporter_id, text),
        )
        row = await (
            await self._conn.execute(
                "UPDATE incidents SET reporter_count = "
                "(SELECT count(DISTINCT reporter_id) FROM incident_reports WHERE incident_id = %s), updated_at = now() "
                "WHERE incident_id = %s RETURNING reporter_count",
                (incident_id, incident_id),
            )
        ).fetchone()
        return row[0]

    async def record(self, actor_id: str, action: str, detail: dict[str, Any]) -> None:
        await _record(self._conn, actor_id, action, detail)


class PostgresIncidentUnitOfWork:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def begin_exclusive(self) -> AsyncIterator[_IncidentWorkspace]:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (INCIDENT_DEDUP_LOCK,))
            yield _IncidentWorkspace(conn)


class PostgresAnswerCache:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def find(
        self, groups_key: str, embedding: Sequence[float], ttl_hours: int, threshold: float
    ) -> CachedAnswer | None:
        params = {
            "groups_key": groups_key,
            "ttl": ttl_hours,
            "embedding": vector_literal(embedding),
            "threshold": threshold,
        }
        async with self._pool.connection() as conn:
            row = await (await conn.execute(_FIND_CACHED_SQL, params)).fetchone()
            if row is None:
                return None
            await conn.execute("UPDATE answer_cache SET hits = hits + 1 WHERE cache_id = %s", (row[0],))
        return CachedAnswer(answer=row[1], citations=[ChunkResult(**citation) for citation in row[2]])

    async def save(
        self,
        question: str,
        embedding: Sequence[float],
        groups_key: str,
        answer: str,
        citations: list[ChunkResult],
        version_ids: list[str],
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO answer_cache (question, embedding, groups_key, answer, citations, version_ids) "
                "VALUES (%s, %s::vector, %s, %s, %s, %s::uuid[])",
                (
                    question,
                    vector_literal(embedding),
                    groups_key,
                    answer,
                    Jsonb([asdict(citation) for citation in citations]),
                    version_ids,
                ),
            )
