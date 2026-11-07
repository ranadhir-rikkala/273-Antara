from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from common.models import VersionStatus


class PostgresDocumentCatalog:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create_upload(
        self,
        document_id: str,
        version_id: str,
        owner_id: str,
        title: str,
        object_key: str,
        content_type: str,
        groups: list[str],
    ) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO documents (document_id, owner_id, title) VALUES (%s, %s, %s)",
                (document_id, owner_id, title),
            )
            await conn.execute(
                "INSERT INTO document_versions "
                "(version_id, document_id, version_number, status, object_key, content_type) "
                "VALUES (%s, %s, 1, %s, %s, %s)",
                (version_id, document_id, VersionStatus.UPLOADED, object_key, content_type),
            )
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO document_acl (document_id, group_name) VALUES (%s, %s)",
                    [(document_id, group) for group in groups],
                )

    async def list_for_groups(self, groups: list[str]) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT d.document_id::text, d.title, v.status, v.version_number, d.created_at, "
                    "array(SELECT group_name FROM document_acl a WHERE a.document_id = d.document_id ORDER BY 1) "
                    "FROM documents d "
                    "JOIN LATERAL (SELECT status, version_number FROM document_versions "
                    "WHERE document_id = d.document_id ORDER BY version_number DESC LIMIT 1) v ON true "
                    "WHERE d.deleted_at IS NULL AND EXISTS ("
                    "SELECT 1 FROM document_acl a WHERE a.document_id = d.document_id AND a.group_name = ANY(%s)) "
                    "ORDER BY d.created_at DESC LIMIT 200",
                    (groups,),
                )
            ).fetchall()
        return [
            {
                "document_id": row[0],
                "title": row[1],
                "status": row[2],
                "version_number": row[3],
                "created_at": row[4].isoformat(),
                "groups": row[5],
            }
            for row in rows
        ]

    async def count_in_flight(self) -> int:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT count(*) FROM document_versions WHERE status = ANY(%s)",
                    ([VersionStatus.UPLOADED, VersionStatus.PROCESSING],),
                )
            ).fetchone()
        return row[0]

    async def get(self, document_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            document = await (
                await conn.execute(
                    "SELECT title, active_version_id::text, deleted_at IS NOT NULL FROM documents "
                    "WHERE document_id::text = %s",
                    (document_id,),
                )
            ).fetchone()
            if document is None:
                return None
            versions = await (
                await conn.execute(
                    "SELECT version_id::text, version_number, status, error FROM document_versions "
                    "WHERE document_id::text = %s ORDER BY version_number",
                    (document_id,),
                )
            ).fetchall()
        return {
            "document_id": document_id,
            "title": document[0],
            "active_version_id": document[1],
            "deleted": document[2],
            "versions": [{"version_id": v[0], "version_number": v[1], "status": v[2], "error": v[3]} for v in versions],
        }


class _DecisionWorkspace:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def lock(self, approval_id: str) -> tuple[str, str, bool, str] | None:
        row = await (
            await self._conn.execute(
                "SELECT run_id, status, escalated, requester_id FROM approvals WHERE approval_id::text = %s FOR UPDATE",
                (approval_id,),
            )
        ).fetchone()
        return None if row is None else (row[0], row[1], row[2], row[3])

    async def record(self, approval_id: str, approver_id: str, approved: bool) -> None:
        await self._conn.execute(
            "UPDATE approvals SET status = %s, decided_by = %s, decided_at = now() WHERE approval_id::text = %s",
            ("APPROVED" if approved else "DENIED", approver_id, approval_id),
        )


class PostgresApprovalInbox:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def pending(self, escalated_only: bool) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT approval_id::text, run_id, requester_id, system, role, reason, escalated, created_at "
                    "FROM approvals WHERE status = 'PENDING' AND (escalated OR NOT %s) ORDER BY created_at",
                    (escalated_only,),
                )
            ).fetchall()
        keys = ("approval_id", "run_id", "requester_id", "system", "role", "reason", "escalated")
        return [{**dict(zip(keys, row[:7], strict=True)), "created_at": row[7].isoformat()} for row in rows]

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_DecisionWorkspace]:
        async with self._pool.connection() as conn, conn.transaction():
            yield _DecisionWorkspace(conn)
