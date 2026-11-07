from typing import Protocol

from common.models import IngestRequest


class VersionScanner(Protocol):
    async def stale_versions(self, statuses: list[str], older_than_minutes: int) -> list[IngestRequest]: ...


class WorkflowInspector(Protocol):
    async def is_running(self, workflow_id: str) -> bool: ...
