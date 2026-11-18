from psycopg_pool import AsyncConnectionPool
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError

from common.models import IngestRequest


class PostgresVersionScanner:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def stale_versions(self, statuses: list[str], older_than_minutes: int) -> list[IngestRequest]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT document_id::text, version_id::text, object_key, content_type FROM document_versions "
                    "WHERE status = ANY(%s) AND updated_at < now() - make_interval(mins => %s)",
                    (statuses, older_than_minutes),
                )
            ).fetchall()
        return [IngestRequest(*row) for row in rows]

    async def failed_versions(self, older_than_minutes: int) -> list[tuple[IngestRequest, str]]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT v.document_id::text, v.version_id::text, v.object_key, v.content_type, "
                    "coalesce(v.error, '') FROM document_versions v JOIN documents d USING (document_id) "
                    "WHERE v.status = 'FAILED' AND d.deleted_at IS NULL "
                    "AND v.updated_at < now() - make_interval(mins => %s)",
                    (older_than_minutes,),
                )
            ).fetchall()
        return [(IngestRequest(*row[:4]), row[4]) for row in rows]


class TemporalWorkflowInspector:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def is_running(self, workflow_id: str) -> bool:
        try:
            description = await self._client.get_workflow_handle(workflow_id).describe()
        except RPCError:
            return False
        return description.status == WorkflowExecutionStatus.RUNNING
