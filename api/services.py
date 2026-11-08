import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from api.ports import ApprovalInbox, DocumentCatalog, ObjectStore, TraceStore
from common.models import AgentRunState, IngestRequest, UserContext

APPROVER_GROUP = "managers"
ESCALATION_GROUP = "security-leads"
DEFAULT_TITLE = "document"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
MAX_QUESTION_LENGTH = 2000
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PUBLISHER_GROUPS = frozenset({"security-leads", "operators"})
TRACE_ID = re.compile(r"[0-9a-f]{32}")
PRIVATE_PREFIXES = ("10.", "127.", "192.168.") + tuple(f"172.{n}." for n in range(16, 32))


class NotFoundError(Exception):
    pass


class ForbiddenError(Exception):
    pass


class ConflictError(Exception):
    pass


class InvalidQuestionError(Exception):
    pass


class TooLargeError(Exception):
    pass


@dataclass(frozen=True)
class DecisionOutcome:
    approval_id: str
    run_id: str
    approved: bool


def parse_groups(raw: str) -> list[str]:
    return [group.strip() for group in raw.split(",") if group.strip()]


def clean_question(text: str) -> str:
    question = text.strip()
    if not question:
        raise InvalidQuestionError("Ask a question or describe what you need")
    if len(question) > MAX_QUESTION_LENGTH:
        raise InvalidQuestionError(f"Keep questions under {MAX_QUESTION_LENGTH} characters")
    return question


def can_view_inbox(user: UserContext) -> bool:
    return APPROVER_GROUP in user.groups or ESCALATION_GROUP in user.groups


def sees_only_escalated(user: UserContext) -> bool:
    return APPROVER_GROUP not in user.groups


def can_decide(user: UserContext, escalated: bool) -> bool:
    return APPROVER_GROUP in user.groups or (escalated and ESCALATION_GROUP in user.groups)


def check_sharing(user: UserContext, groups: list[str]) -> None:
    if not groups:
        raise ForbiddenError("Share the document with at least one group")
    if PUBLISHER_GROUPS.intersection(user.groups):
        return
    outside = [group for group in groups if group not in user.groups]
    if outside:
        raise ForbiddenError(
            f"You can only share with your own groups ({', '.join(user.groups)}), not {', '.join(outside)}"
        )


def check_upload_size(size: int) -> None:
    if size > MAX_UPLOAD_BYTES:
        raise TooLargeError(f"Documents must be {MAX_UPLOAD_BYTES // (1024 * 1024)} MB or smaller")


def may_read_document(user: UserContext, groups: list[str]) -> bool:
    return bool(set(user.groups).intersection(groups))


async def readable_document(document_id: str, user: UserContext, catalog: DocumentCatalog) -> dict[str, object]:
    document = await catalog.get(document_id)
    if document is None or document["deleted"] or not may_read_document(user, document["groups"]):
        raise NotFoundError("Document not found")
    return document


async def authorize_delete(document_id: str, user: UserContext, catalog: DocumentCatalog) -> None:
    document = await catalog.get(document_id)
    if document is None or document["deleted"]:
        raise NotFoundError("Document not found")
    if PUBLISHER_GROUPS.intersection(user.groups):
        return
    if not may_read_document(user, document["groups"]):
        raise NotFoundError("Document not found")
    if document["owner_id"] != user.user_id:
        raise ForbiddenError("Only the owner, operators or security leads can delete this document")


def may_read_run(user: UserContext, state: AgentRunState) -> bool:
    return bool(state.requester_id) and state.requester_id == user.user_id


def inside_cluster(host: str) -> bool:
    return "." not in host or host.endswith((".svc", ".svc.cluster.local")) or host.startswith(PRIVATE_PREFIXES)


def summarize_trace(trace: dict[str, object]) -> dict[str, object]:
    processes = {key: value["serviceName"] for key, value in trace["processes"].items()}
    services: list[str] = []
    hosts: list[str] = []
    for span in sorted(trace["spans"], key=lambda item: item["startTime"]):
        if processes[span["processID"]] not in services:
            services.append(processes[span["processID"]])
        tags = {tag["key"]: tag["value"] for tag in span["tags"]}
        host = urlsplit(tags.get("http.url", "")).hostname
        if tags.get("span.kind") == "client" and host and host not in hosts:
            hosts.append(host)
    return {
        "services": services,
        "hosts": hosts,
        "outside": [host for host in hosts if not inside_cluster(host)],
        "spans": len(trace["spans"]),
    }


async def trace_summary(trace_id: str, traces: TraceStore) -> dict[str, object]:
    if not TRACE_ID.fullmatch(trace_id):
        raise NotFoundError("Trace not found")
    trace = await traces.get(trace_id)
    if trace is None:
        raise NotFoundError("Trace not found")
    return summarize_trace(trace)


async def register_upload(
    user: UserContext,
    filename: str | None,
    content_type: str | None,
    data: bytes,
    groups: list[str],
    objects: ObjectStore,
    catalog: DocumentCatalog,
) -> IngestRequest:
    check_upload_size(len(data))
    check_sharing(user, groups)
    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    title = filename or DEFAULT_TITLE
    content_type = content_type or DEFAULT_CONTENT_TYPE
    object_key = f"originals/{document_id}/{version_id}/{title}"
    await objects.put(object_key, data, content_type)
    await catalog.create_upload(document_id, version_id, user.user_id, title, object_key, content_type, groups)
    return IngestRequest(
        document_id=document_id, version_id=version_id, object_key=object_key, content_type=content_type
    )


async def approvals_for(user: UserContext, inbox: ApprovalInbox) -> list[dict[str, object]]:
    if not can_view_inbox(user):
        raise ForbiddenError("Only approvers can see the approvals inbox")
    return await inbox.pending(sees_only_escalated(user))


async def decide(approval_id: str, user: UserContext, approved: bool, inbox: ApprovalInbox) -> DecisionOutcome:
    async with inbox.begin() as workspace:
        row = await workspace.lock(approval_id)
        if row is None:
            raise NotFoundError("Approval not found")
        run_id, status, escalated, requester_id = row
        if status != "PENDING":
            raise ConflictError(f"Approval is already {status}")
        if requester_id == user.user_id:
            raise ForbiddenError("You cannot approve your own request")
        if not can_decide(user, escalated):
            raise ForbiddenError("You cannot decide this approval")
        await workspace.record(approval_id, user.user_id, approved)
    return DecisionOutcome(approval_id=approval_id, run_id=run_id, approved=approved)
