from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from common.models import ToolRequest


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    allowed_groups: frozenset[str]
    requires_approval: bool


async def _create_ticket(conn: AsyncConnection, request: ToolRequest) -> dict[str, Any]:
    args = request.call.arguments
    row = await (
        await conn.execute(
            "INSERT INTO tickets (title, description, priority, requester_id) VALUES (%s, %s, %s, %s) "
            "RETURNING ticket_id::text, status",
            (args["title"], args["description"], args.get("priority", "medium"), request.user.user_id),
        )
    ).fetchone()
    return {"ticket_id": row[0], "status": row[1]}


async def _get_ticket_status(conn: AsyncConnection, request: ToolRequest) -> dict[str, Any]:
    row = await (
        await conn.execute(
            "SELECT ticket_id::text, status, title FROM tickets WHERE ticket_id = %s AND requester_id = %s",
            (request.call.arguments["ticket_id"], request.user.user_id),
        )
    ).fetchone()
    if row is None:
        return {"found": False}
    return {"found": True, "ticket_id": row[0], "status": row[1], "title": row[2]}


async def _request_access(conn: AsyncConnection, request: ToolRequest) -> dict[str, Any]:
    args = request.call.arguments
    row = await (
        await conn.execute(
            "INSERT INTO approvals (requester_id, system, role, reason, status) VALUES (%s, %s, %s, %s, 'APPROVED') "
            "RETURNING approval_id::text",
            (request.user.user_id, args["system"], args["role"], args.get("reason", "")),
        )
    ).fetchone()
    return {"approval_id": row[0], "system": args["system"], "role": args["role"], "status": "APPROVED"}


Handler = Callable[[AsyncConnection, ToolRequest], Awaitable[dict[str, Any]]]

_EVERYONE = frozenset({"employees", "contractors", "managers"})

REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            name="create_ticket",
            description="Create an IT or security helpdesk ticket for the current user.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["title", "description"],
            },
            allowed_groups=_EVERYONE,
            requires_approval=False,
        ),
        ToolSpec(
            name="get_ticket_status",
            description="Look up the status of one of the current user's tickets.",
            parameters={
                "type": "object",
                "properties": {"ticket_id": {"type": "string"}},
                "required": ["ticket_id"],
            },
            allowed_groups=_EVERYONE,
            requires_approval=False,
        ),
        ToolSpec(
            name="request_access",
            description="Request access to an internal system. A manager must approve it before it is granted.",
            parameters={
                "type": "object",
                "properties": {
                    "system": {"type": "string"},
                    "role": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["system", "role"],
            },
            allowed_groups=frozenset({"employees", "managers"}),
            requires_approval=True,
        ),
    ]
}

HANDLERS: dict[str, Handler] = {
    "create_ticket": _create_ticket,
    "get_ticket_status": _get_ticket_status,
    "request_access": _request_access,
}


def schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters},
        }
        for spec in REGISTRY.values()
    ]
