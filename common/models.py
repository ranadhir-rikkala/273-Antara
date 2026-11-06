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


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    DENIED = "DENIED"
    FAILED = "FAILED"


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


@dataclass
class GenerateRequest:
    question: str
    chunks: list[ChunkResult]
    tool_results: list[dict[str, Any]] = field(default_factory=list)


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


@dataclass
class ApprovalDecision:
    approved: bool
    approver_id: str
    comment: str = ""


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


@dataclass
class ReconcileReport:
    redriven: list[str] = field(default_factory=list)
