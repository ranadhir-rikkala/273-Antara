from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class VersionStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class Intent(StrEnum):
    QUESTION = "question"
    ACTION = "action"
    INCIDENT = "incident"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


@dataclass
class ConversationTurn:
    question: str
    answer: str


@dataclass
class UserContext:
    user_id: str
    groups: list[str] = field(default_factory=list)


@dataclass
class ChunkResult:
    document_id: str
    version_id: str
    chunk_id: str
    text: str
    page_number: int
    score: float


@dataclass
class IngestRequest:
    document_id: str
    version_id: str
    object_key: str
    content_type: str


@dataclass
class ExtractedDocument:
    document_id: str
    version_id: str
    text_key: str


@dataclass
class ChunkedDocument:
    version_id: str
    chunk_ids: list[str]


@dataclass
class EmbedBatch:
    version_id: str
    chunk_ids: list[str]


@dataclass
class MarkFailedRequest:
    version_id: str
    reason: str


@dataclass
class IngestResult:
    version_id: str
    chunk_count: int
    status: str
    offloaded_batches: int = 0


@dataclass
class RetrieveRequest:
    query: str
    user: UserContext
    top_k: int = 5


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False


@dataclass
class ModelTurn:
    answer: str | None = None
    tool_call: ToolCall | None = None
    needs_input: bool = False
    flags: list[str] = field(default_factory=list)


@dataclass
class GenerateRequest:
    question: str
    chunks: list[ChunkResult]
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    allow_tools: bool = True
    tool_names: list[str] | None = None
    history: list[ConversationTurn] = field(default_factory=list)


@dataclass
class ToolRequest:
    idempotency_key: str
    call: ToolCall
    user: UserContext
    run_id: str = ""


@dataclass
class ToolResult:
    idempotency_key: str
    status: str
    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRunRequest:
    question: str
    user: UserContext
    history: list[ConversationTurn] = field(default_factory=list)


@dataclass
class ApprovalDecision:
    approved: bool
    approver_id: str
    comment: str = ""


@dataclass
class IncidentReport:
    report_id: str
    reporter: UserContext
    text: str


@dataclass
class IncidentResult:
    incident_id: str
    title: str
    created: bool
    reporter_count: int
    system: str | None = None


@dataclass
class CacheLookupRequest:
    question: str
    user: UserContext


@dataclass
class CachedAnswer:
    answer: str
    citations: list[ChunkResult]


@dataclass
class CacheStoreRequest:
    question: str
    user: UserContext
    answer: str
    citations: list[ChunkResult]


@dataclass
class ApprovalOpenRequest:
    approval_key: str
    run_id: str
    user: UserContext
    call: ToolCall


@dataclass
class AgentRunState:
    status: str
    answer: str | None = None
    citations: list[ChunkResult] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    escalated: bool = False
    approval_id: str | None = None
    intent: str | None = None
    incident: IncidentResult | None = None
    cached: bool = False
    needs_input: bool = False
    flags: list[str] = field(default_factory=list)


@dataclass
class ReconcileReport:
    redriven: list[str] = field(default_factory=list)
