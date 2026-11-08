from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class DocumentCatalog(Protocol):
    async def create_upload(
        self,
        document_id: str,
        version_id: str,
        owner_id: str,
        title: str,
        object_key: str,
        content_type: str,
        groups: list[str],
    ) -> None: ...

    async def list_for_groups(self, groups: list[str]) -> list[dict[str, Any]]: ...

    async def get(self, document_id: str) -> dict[str, Any] | None: ...

    async def count_in_flight(self) -> int: ...


class ApprovalDecisionWorkspace(Protocol):
    async def lock(self, approval_id: str) -> tuple[str, str, bool, str] | None: ...

    async def record(self, approval_id: str, approver_id: str, approved: bool) -> None: ...


class ApprovalInbox(Protocol):
    async def pending(self, escalated_only: bool) -> list[dict[str, Any]]: ...

    def begin(self) -> AbstractAsyncContextManager[ApprovalDecisionWorkspace]: ...


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...


class BacklogReader(Protocol):
    async def backlog(self, queue: str) -> int | None: ...


class AdmissionMetrics(Protocol):
    def rejected(self, reason: str) -> None: ...

    def backlog(self, queue: str, value: int) -> None: ...
