from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from common.models import CachedAnswer, ChunkResult, IncidentResult, ToolCall, UserContext


class ChatModel(Protocol):
    async def generate(self, messages: list[dict[str, Any]]) -> str: ...

    async def generate_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class Retriever(Protocol):
    async def search(self, query: str, user: UserContext, top_k: int) -> list[ChunkResult]: ...


class AuditLog(Protocol):
    async def record(self, actor_id: str, action: str, detail: dict[str, Any]) -> None: ...


class TicketStore(Protocol):
    async def create(self, title: str, description: str, priority: str, requester_id: str) -> dict[str, Any]: ...

    async def find_for_requester(self, ticket_id: str, requester_id: str) -> dict[str, Any] | None: ...


class AccessGrants(Protocol):
    async def grant(self, run_id: str, requester_id: str, system: str, role: str) -> str: ...


class ToolWorkspace(Protocol):
    tickets: TicketStore
    grants: AccessGrants
    audit: AuditLog

    async def claim(self, key: str, tool: str, arguments: dict[str, Any], user_id: str) -> dict[str, Any] | None: ...

    async def complete(self, key: str, result: dict[str, Any]) -> None: ...


class ToolUnitOfWork(Protocol):
    def begin(self) -> AbstractAsyncContextManager[ToolWorkspace]: ...


class ApprovalStore(Protocol):
    async def open(self, approval_key: str, run_id: str, requester_id: str, call: ToolCall) -> str: ...

    async def escalate(self, approval_id: str) -> None: ...

    async def expire(self, approval_id: str) -> None: ...


class IncidentWorkspace(Protocol):
    async def find_report(self, report_id: str) -> IncidentResult | None: ...

    async def find_similar_open(
        self, embedding: Sequence[float], window_minutes: int, threshold: float
    ) -> tuple[str, str] | None: ...

    async def find_open_for_system(self, system: str, window_minutes: int) -> tuple[str, str] | None: ...

    async def open_incident(self, title: str, embedding: Sequence[float], system: str | None) -> str: ...

    async def add_report(self, report_id: str, incident_id: str, reporter_id: str, text: str) -> int: ...

    async def record(self, actor_id: str, action: str, detail: dict[str, Any]) -> None: ...


class IncidentUnitOfWork(Protocol):
    def begin_exclusive(self) -> AbstractAsyncContextManager[IncidentWorkspace]: ...


class AnswerCache(Protocol):
    async def find(
        self, groups_key: str, embedding: Sequence[float], ttl_hours: int, threshold: float
    ) -> CachedAnswer | None: ...

    async def save(
        self,
        question: str,
        embedding: Sequence[float],
        groups_key: str,
        answer: str,
        citations: list[ChunkResult],
        version_ids: list[str],
    ) -> None: ...
