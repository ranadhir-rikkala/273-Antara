import copy
import math
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from common.models import CachedAnswer, ChunkResult, IncidentResult, IngestRequest, ToolCall
from common.queue_stats import QueueStats

VOCABULARY = ("vpn", "down", "password", "laptop", "access", "database", "printer", "salary")


def embed_text(text: str) -> list[float]:
    lowered = text.lower()
    return [1.0 if word in lowered else 0.0 for word in VOCABULARY] + [0.01]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


class FakeModel:
    def __init__(
        self,
        reply: str = "An answer [1]",
        tool_message: dict[str, Any] | None = None,
        fail: bool = False,
    ) -> None:
        self.reply = reply
        self.tool_message = tool_message
        self.fail = fail
        self.chat_calls: list[list[dict[str, Any]]] = []
        self.offered_tools: list[list[str]] = []
        self.embedded: list[str] = []

    async def generate(self, messages: list[dict[str, Any]]) -> str:
        if self.fail:
            raise RuntimeError("model unavailable")
        self.chat_calls.append(messages)
        return self.reply

    async def generate_with_tools(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.chat_calls.append(messages)
        self.offered_tools.append([tool["function"]["name"] for tool in tools])
        return self.tool_message or {"content": self.reply}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [embed_text(text) for text in texts]


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, dict[str, Any]]] = []

    async def record(self, actor_id: str, action: str, detail: dict[str, Any]) -> None:
        self.entries.append((actor_id, action, detail))


class FakeRetriever:
    def __init__(self, chunks: list[ChunkResult]) -> None:
        self.chunks = chunks

    async def search(self, query: str, user: Any, top_k: int) -> list[ChunkResult]:
        return self.chunks[:top_k]


class _Tickets:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def create(self, title: str, description: str, priority: str, requester_id: str) -> dict[str, Any]:
        ticket_id = f"ticket-{len(self._state['tickets']) + 1}"
        self._state["tickets"][ticket_id] = {"title": title, "status": "OPEN", "requester_id": requester_id}
        return {"ticket_id": ticket_id, "status": "OPEN"}

    async def find_for_requester(self, ticket_id: str, requester_id: str) -> dict[str, Any] | None:
        ticket = self._state["tickets"].get(ticket_id)
        if ticket is None or ticket["requester_id"] != requester_id:
            return None
        return {"ticket_id": ticket_id, "status": ticket["status"], "title": ticket["title"]}


class _Grants:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def grant(self, run_id: str, requester_id: str, system: str, role: str) -> str:
        self._state["grants"].append((run_id, requester_id, system, role))
        return f"grant-{len(self._state['grants'])}"


class _ToolWorkspace:
    def __init__(self, state: dict[str, Any], audit: FakeAudit) -> None:
        self._state = state
        self.tickets = _Tickets(state)
        self.grants = _Grants(state)
        self.audit = audit

    async def claim(self, key: str, tool: str, arguments: dict[str, Any], user_id: str) -> dict[str, Any] | None:
        entry = self._state["ledger"].setdefault(key, {"status": "STARTED", "result": None})
        return entry["result"] if entry["status"] == "COMPLETED" else None

    async def complete(self, key: str, result: dict[str, Any]) -> None:
        self._state["ledger"][key] = {"status": "COMPLETED", "result": result}


class FakeToolUnitOfWork:
    def __init__(self, crash_after_commit: bool = False) -> None:
        self.state: dict[str, Any] = {"ledger": {}, "tickets": {}, "grants": []}
        self.audit = FakeAudit()
        self.crash_after_commit = crash_after_commit

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_ToolWorkspace]:
        working = copy.deepcopy(self.state)
        yield _ToolWorkspace(working, self.audit)
        self.state = working
        if self.crash_after_commit:
            self.crash_after_commit = False
            raise ConnectionError("worker died after commit")


class FakeApprovalStore:
    def __init__(self) -> None:
        self.opened: dict[str, dict[str, Any]] = {}
        self.escalated: list[str] = []
        self.expired: list[str] = []

    async def open(self, approval_key: str, run_id: str, requester_id: str, call: ToolCall) -> str:
        self.opened.setdefault(approval_key, {"id": f"approval-{len(self.opened) + 1}", "run_id": run_id})
        return self.opened[approval_key]["id"]

    async def escalate(self, approval_id: str) -> None:
        self.escalated.append(approval_id)

    async def expire(self, approval_id: str) -> None:
        self.expired.append(approval_id)


class _IncidentWorkspace:
    def __init__(self, state: dict[str, Any], audit: FakeAudit) -> None:
        self._state = state
        self._audit = audit

    async def find_report(self, report_id: str) -> IncidentResult | None:
        incident_id = self._state["reports"].get(report_id)
        if incident_id is None:
            return None
        incident = self._state["incidents"][incident_id]
        return IncidentResult(incident_id, incident["title"], created=False, reporter_count=incident["count"])

    async def find_similar_open(
        self, embedding: Sequence[float], window_minutes: int, threshold: float
    ) -> tuple[str, str] | None:
        scored = [
            (cosine(embedding, incident["embedding"]), incident_id, incident["title"])
            for incident_id, incident in self._state["incidents"].items()
            if incident["open"] and incident["system"] is None
        ]
        best = max(scored, default=None)
        return None if best is None or best[0] < threshold else (best[1], best[2])

    async def find_open_for_system(self, system: str, window_minutes: int) -> tuple[str, str] | None:
        for incident_id, incident in self._state["incidents"].items():
            if incident["open"] and incident["system"] == system:
                return incident_id, incident["title"]
        return None

    async def open_incident(self, title: str, embedding: Sequence[float], system: str | None) -> str:
        incident_id = f"incident-{len(self._state['incidents']) + 1}"
        self._state["incidents"][incident_id] = {
            "title": title,
            "embedding": list(embedding),
            "count": 0,
            "open": True,
            "system": system,
        }
        return incident_id

    async def add_report(self, report_id: str, incident_id: str, reporter_id: str, text: str) -> int:
        self._state["reports"][report_id] = incident_id
        self._state.setdefault("reporters", {})[report_id] = reporter_id
        incident = self._state["incidents"][incident_id]
        incident["count"] = len(
            {self._state["reporters"][rid] for rid, linked in self._state["reports"].items() if linked == incident_id}
        )
        return incident["count"]

    async def record(self, actor_id: str, action: str, detail: dict[str, Any]) -> None:
        await self._audit.record(actor_id, action, detail)


class FakeIncidentUnitOfWork:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"incidents": {}, "reports": {}}
        self.audit = FakeAudit()

    @asynccontextmanager
    async def begin_exclusive(self) -> AsyncIterator[_IncidentWorkspace]:
        yield _IncidentWorkspace(self.state, self.audit)


class FakeAnswerCache:
    def __init__(self, active_versions: set[str] | None = None) -> None:
        self.entries: list[dict[str, Any]] = []
        self.active_versions = active_versions if active_versions is not None else set()

    async def find(
        self, groups_key: str, embedding: Sequence[float], ttl_hours: int, threshold: float
    ) -> CachedAnswer | None:
        for entry in self.entries:
            fresh = all(version in self.active_versions for version in entry["version_ids"])
            similar = cosine(embedding, entry["embedding"]) >= threshold
            if entry["groups_key"] == groups_key and fresh and similar:
                return CachedAnswer(entry["answer"], entry["citations"])
        return None

    async def save(
        self,
        question: str,
        embedding: Sequence[float],
        groups_key: str,
        answer: str,
        citations: list[ChunkResult],
        version_ids: list[str],
    ) -> None:
        self.entries.append(
            {
                "question": question,
                "embedding": list(embedding),
                "groups_key": groups_key,
                "answer": answer,
                "citations": citations,
                "version_ids": version_ids,
            }
        )


class FakeObjectStore:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [key for key in self.objects if key.startswith(prefix)]
        for key in doomed:
            del self.objects[key]
        return len(doomed)


class FakeVersionStore:
    def __init__(self) -> None:
        self.status: dict[str, tuple[str, str | None]] = {}
        self.text_keys: dict[str, str] = {}

    async def set_status(self, version_id: str, status: str, error: str | None = None) -> None:
        self.status[version_id] = (status, error)

    async def set_text_key(self, version_id: str, text_key: str) -> None:
        self.text_keys[version_id] = text_key


class FakeChunkStore:
    def __init__(self) -> None:
        self.chunks: dict[str, tuple[str, str, int, str]] = {}
        self.embeddings: dict[str, list[float]] = {}

    async def replace_chunks(self, document_id: str, version_id: str, pieces: list[tuple[int, str]]) -> list[str]:
        self.chunks = {key: value for key, value in self.chunks.items() if value[1] != version_id}
        ids = [f"{version_id}-chunk-{index}" for index in range(len(pieces))]
        for chunk_id, (page, text) in zip(ids, pieces, strict=True):
            self.chunks[chunk_id] = (document_id, version_id, page, text)
        return ids

    async def texts(self, chunk_ids: list[str]) -> list[tuple[str, str]]:
        return [(chunk_id, self.chunks[chunk_id][3]) for chunk_id in chunk_ids if chunk_id in self.chunks]

    async def save_embeddings(self, pairs: list[tuple[str, Sequence[float]]]) -> None:
        for chunk_id, vector in pairs:
            self.embeddings[chunk_id] = list(vector)


class _PublishWorkspace:
    def __init__(self, owner: "FakePublishUnitOfWork") -> None:
        self._owner = owner

    async def embedding_counts(self, version_id: str) -> tuple[int, int]:
        return self._owner.counts[version_id]

    async def activate(self, document_id: str, version_id: str) -> None:
        self._owner.active[document_id] = version_id


class FakePublishUnitOfWork:
    def __init__(self, counts: dict[str, tuple[int, int]]) -> None:
        self.counts = counts
        self.active: dict[str, str] = {}

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_PublishWorkspace]:
        yield _PublishWorkspace(self)


class FakeLifecycle:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, int | None]] = []

    async def start_deletion(self, document_id: str) -> None:
        self.events.append(("start", document_id, None))

    async def finish_deletion(self, document_id: str, removed_objects: int) -> None:
        self.events.append(("finish", document_id, removed_objects))


class FakeQueueInspector:
    def __init__(self, stats: QueueStats | None = None, fail: bool = False) -> None:
        self._stats = stats
        self._fail = fail

    async def stats(self, queue: str) -> QueueStats:
        if self._fail:
            raise ConnectionError("temporal unreachable")
        return self._stats


class FakeSearchIndex:
    def __init__(self, chunks: list[ChunkResult]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[list[str], int]] = []

    async def nearest(self, embedding: Sequence[float], groups: list[str], top_k: int) -> list[ChunkResult]:
        self.calls.append((groups, top_k))
        return self.chunks[:top_k]


class FakeVersionScanner:
    def __init__(self, stale: list[IngestRequest]) -> None:
        self.stale = stale
        self.queries: list[tuple[list[str], int]] = []

    async def stale_versions(self, statuses: list[str], older_than_minutes: int) -> list[IngestRequest]:
        self.queries.append((statuses, older_than_minutes))
        return self.stale


class FakeWorkflowInspector:
    def __init__(self, running: set[str]) -> None:
        self.running = running

    async def is_running(self, workflow_id: str) -> bool:
        return workflow_id in self.running


class FakeCatalog:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

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
        self.uploads.append({"document_id": document_id, "title": title, "groups": groups, "owner_id": owner_id})

    async def list_for_groups(self, groups: list[str]) -> list[dict[str, Any]]:
        return []

    async def get(self, document_id: str) -> dict[str, Any] | None:
        return None

    async def count_in_flight(self) -> int:
        return len(self.uploads)


class _DecisionWorkspace:
    def __init__(self, inbox: "FakeInbox") -> None:
        self._inbox = inbox

    async def lock(self, approval_id: str) -> tuple[str, str, bool, str] | None:
        return self._inbox.rows.get(approval_id)

    async def record(self, approval_id: str, approver_id: str, approved: bool) -> None:
        run_id, _, escalated, requester = self._inbox.rows[approval_id]
        self._inbox.rows[approval_id] = (run_id, "APPROVED" if approved else "DENIED", escalated, requester)
        self._inbox.decided_by[approval_id] = approver_id


class FakeInbox:
    def __init__(self, rows: dict[str, tuple[str, str, bool, str]] | None = None) -> None:
        self.rows = rows or {}
        self.decided_by: dict[str, str] = {}
        self.pending_queries: list[bool] = []

    async def pending(self, escalated_only: bool) -> list[dict[str, Any]]:
        self.pending_queries.append(escalated_only)
        return [
            {"approval_id": key, "escalated": escalated}
            for key, (_, status, escalated, _requester) in self.rows.items()
            if status == "PENDING" and (escalated or not escalated_only)
        ]

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_DecisionWorkspace]:
        yield _DecisionWorkspace(self)
