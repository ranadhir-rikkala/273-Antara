from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent.ports import ToolWorkspace
from common.models import ToolRequest


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    allowed_groups: frozenset[str]
    requires_approval: bool


async def _create_ticket(workspace: ToolWorkspace, request: ToolRequest) -> dict[str, Any]:
    args = request.call.arguments
    return await workspace.tickets.create(
        args["title"], args["description"], args.get("priority", "medium"), request.user.user_id
    )


async def _get_ticket_status(workspace: ToolWorkspace, request: ToolRequest) -> dict[str, Any]:
    ticket = await workspace.tickets.find_for_requester(request.call.arguments["ticket_id"], request.user.user_id)
    if ticket is None:
        return {"found": False}
    return {"found": True, **ticket}


async def _request_access(workspace: ToolWorkspace, request: ToolRequest) -> dict[str, Any]:
    args = request.call.arguments
    grant_id = await workspace.grants.grant(request.run_id, request.user.user_id, args["system"], args["role"])
    return {"grant_id": grant_id, "system": args["system"], "role": args["role"], "status": "GRANTED"}


Handler = Callable[[ToolWorkspace, ToolRequest], Awaitable[dict[str, Any]]]

_EVERYONE = frozenset({"employees", "contractors", "managers"})

REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            name="create_ticket",
            description=(
                "Report a problem that IT must fix, such as broken hardware or software. "
                "Never use it to request access."
            ),
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
            description=(
                "Request access to an internal system, database or tool. "
                "Use it whenever the user asks for access. A manager approves it."
            ),
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


def schemas(names: list[str] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters},
        }
        for spec in REGISTRY.values()
        if names is None or spec.name in names
    ]


def may_use(tool: str, groups: list[str]) -> bool:
    spec = REGISTRY.get(tool)
    return spec is not None and bool(spec.allowed_groups.intersection(groups))
