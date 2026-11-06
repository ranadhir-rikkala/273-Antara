from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError

from common.db import pool
from common.models import IngestRequest, VersionStatus
from common.names import Activities
from common.temporal_client import shared

STALE_AFTER = "5 minutes"


async def _ingest_running(version_id: str) -> bool:
    client = await shared()
    try:
        description = await client.get_workflow_handle(f"ingest-{version_id}").describe()
    except RPCError:
        return False
    return description.status == WorkflowExecutionStatus.RUNNING


@activity.defn(name=Activities.FIND_INCONSISTENT)
async def find_inconsistent_versions() -> list[IngestRequest]:
    db = await pool()
    async with db.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT document_id::text, version_id::text, object_key, content_type FROM document_versions "
                "WHERE status = ANY(%s) AND updated_at < now() - %s::interval",
                ([VersionStatus.UPLOADED, VersionStatus.PROCESSING], STALE_AFTER),
            )
        ).fetchall()
    stuck = []
    for document_id, version_id, object_key, content_type in rows:
        if not await _ingest_running(version_id):
            stuck.append(
                IngestRequest(
                    document_id=document_id,
                    version_id=version_id,
                    object_key=object_key,
                    content_type=content_type,
                )
            )
    return stuck
