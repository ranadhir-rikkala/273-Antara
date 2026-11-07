import uuid
from dataclasses import dataclass

from api.ports import ApprovalInbox, DocumentCatalog, ObjectStore
from common.models import IngestRequest, UserContext

APPROVER_GROUP = "managers"
ESCALATION_GROUP = "security-leads"
DEFAULT_TITLE = "document"
DEFAULT_CONTENT_TYPE = "application/octet-stream"


class NotFoundError(Exception):
    pass


class ForbiddenError(Exception):
    pass


class ConflictError(Exception):
    pass


@dataclass(frozen=True)
class DecisionOutcome:
    approval_id: str
    run_id: str
    approved: bool


def parse_groups(raw: str) -> list[str]:
    return [group.strip() for group in raw.split(",") if group.strip()]


def can_view_inbox(user: UserContext) -> bool:
    return APPROVER_GROUP in user.groups or ESCALATION_GROUP in user.groups


def sees_only_escalated(user: UserContext) -> bool:
    return APPROVER_GROUP not in user.groups


def can_decide(user: UserContext, escalated: bool) -> bool:
    return APPROVER_GROUP in user.groups or (escalated and ESCALATION_GROUP in user.groups)


async def register_upload(
    user: UserContext,
    filename: str | None,
    content_type: str | None,
    data: bytes,
    groups: list[str],
    objects: ObjectStore,
    catalog: DocumentCatalog,
) -> IngestRequest:
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
