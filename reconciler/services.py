from common.models import IngestRequest, VersionStatus
from reconciler.ports import VersionScanner, WorkflowInspector

STALE_AFTER_MINUTES = 5
UNFINISHED = [VersionStatus.UPLOADED, VersionStatus.PROCESSING]


def ingest_workflow_id(version_id: str) -> str:
    return f"ingest-{version_id}"


async def find_inconsistent(
    scanner: VersionScanner, workflows: WorkflowInspector, older_than_minutes: int = STALE_AFTER_MINUTES
) -> list[IngestRequest]:
    stale = await scanner.stale_versions(list(UNFINISHED), older_than_minutes)
    return [request for request in stale if not await workflows.is_running(ingest_workflow_id(request.version_id))]
