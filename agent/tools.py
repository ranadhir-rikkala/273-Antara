import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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
    clarify: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    needs_description: bool = False
    extract: dict[str, re.Pattern[str]] = field(default_factory=dict)


ACCESS_TARGET = re.compile(r"access to (?:the |our )?(.+?)(?:\s+(?:for|so|because|since|to)\b|[.,?!]|$)", re.IGNORECASE)

FILLER_WORDS = frozenset(
    {
        "please",
        "can",
        "could",
        "you",
        "i",
        "we",
        "need",
        "want",
        "would",
        "like",
        "to",
        "create",
        "open",
        "file",
        "make",
        "raise",
        "log",
        "a",
        "an",
        "the",
        "new",
        "for",
        "me",
        "my",
        "ticket",
        "tickets",
        "help",
    }
)
MIN_DESCRIPTION_WORDS = 3


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
            clarify={
                "title": "What is the problem? Describe what is broken and since when.",
                "description": "What is the problem? Describe what is broken and since when.",
            },
            defaults={"priority": "medium"},
            needs_description=True,
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
            clarify={"ticket_id": "What is the ticket ID?"},
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
                    "role": {"type": "string", "enum": ["read", "write"]},
                    "reason": {"type": "string"},
                },
                "required": ["system", "role"],
            },
            allowed_groups=frozenset({"employees", "managers"}),
            requires_approval=True,
            extract={"system": ACCESS_TARGET},
            clarify={
                "system": "Which system do you need access to?",
                "role": "Which access level do you need: read or write?",
            },
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


def describes_problem(question: str) -> bool:
    words = re.findall(r"[a-z']+", question.lower())
    return sum(1 for word in words if word not in FILLER_WORDS) >= MIN_DESCRIPTION_WORDS


def prepare_arguments(spec: ToolSpec, arguments: Any, question: str) -> tuple[dict[str, Any], list[str]]:
    raw = arguments if isinstance(arguments, dict) else {}
    args = {key: value.strip() if isinstance(value, str) else value for key, value in raw.items()}
    lowered = question.lower()
    for name, schema in spec.parameters["properties"].items():
        choices = schema.get("enum")
        if not choices or args.get(name) in choices:
            continue
        inferred = next((choice for choice in choices if re.search(rf"\b{choice}\b", lowered)), None)
        if inferred is not None:
            args[name] = inferred
        elif name in spec.defaults:
            args[name] = spec.defaults[name]
        else:
            args.pop(name, None)
    for name, pattern in spec.extract.items():
        found = pattern.search(question)
        if found and not str(args.get(name, "")).strip():
            args[name] = found.group(1).strip()
    missing = [name for name in spec.parameters.get("required", []) if not str(args.get(name, "")).strip()]
    if spec.needs_description and not describes_problem(question) and "description" not in missing:
        missing.append("description")
    return args, missing


def clarifying_question(spec: ToolSpec, missing: list[str]) -> str:
    prompts = dict.fromkeys(spec.clarify.get(name, f"Please tell me the {name}.") for name in missing)
    return " ".join(prompts)
