from common.models import IngestRequest, VersionStatus
from reconciler.ports import VersionScanner, WorkflowInspector

STALE_AFTER_MINUTES = 5
UNFINISHED = [VersionStatus.UPLOADED, VersionStatus.PROCESSING]
TEMPORARY_MARKERS = ("429", "503", "timeout", "ConnectError")


def ingest_workflow_id(version_id: str) -> str:
    return f"ingest-{version_id}"


def is_temporary(error: str) -> bool:
    return any(marker.lower() in error.lower() for marker in TEMPORARY_MARKERS)


async def find_inconsistent(
    scanner: VersionScanner, workflows: WorkflowInspector, older_than_minutes: int = STALE_AFTER_MINUTES
) -> list[IngestRequest]:
    stale = await scanner.stale_versions(list(UNFINISHED), older_than_minutes)
    failed = await scanner.failed_versions(older_than_minutes)
    candidates = stale + [request for request, error in failed if is_temporary(error)]
    return [request for request in candidates if not await workflows.is_running(ingest_workflow_id(request.version_id))]
