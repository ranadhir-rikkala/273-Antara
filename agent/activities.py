import json
from typing import Any

from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent.tools import HANDLERS, REGISTRY, schemas
from common.db import pool
from common.llm import LLMProvider
from common.models import ChunkResult, GenerateRequest, ModelTurn, RetrieveRequest, ToolCall, ToolRequest, ToolResult
from common.names import Activities, Errors
from ingest.search import search

SYSTEM_PROMPT = (
    "You are Antara, an internal IT and security helpdesk assistant. "
    "Answer only from the numbered context passages and cite them like [1]. "
    "If the context does not contain the answer, say you could not find it in company documents. "
    "Use a tool only when the user asks you to take an action."
)


@activity.defn(name=Activities.RETRIEVE)
async def retrieve_documents(request: RetrieveRequest) -> list[ChunkResult]:
    chunks = await search(request.query, request.user, request.top_k)
    db = await pool()
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES (%s, 'agent.retrieve', %s)",
            (request.user.user_id, Jsonb({"query": request.query, "chunk_ids": [c.chunk_id for c in chunks]})),
        )
    return chunks


def _messages(request: GenerateRequest) -> list[dict[str, Any]]:
    context = "\n\n".join(f"[{index}] {chunk.text}" for index, chunk in enumerate(request.chunks, start=1))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context or 'No passages found.'}\n\nQuestion: {request.question}"},
    ]
    if request.tool_results:
        messages.append(
            {"role": "user", "content": f"Tool results so far: {json.dumps(request.tool_results)}. Reply to the user."}
        )
    return messages


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return json.loads(raw or "{}")


@activity.defn(name=Activities.GENERATE)
async def generate_answer(request: GenerateRequest) -> ModelTurn:
    llm = LLMProvider()
    try:
        message = await llm.generate_with_tools(_messages(request), schemas())
    finally:
        await llm.aclose()
    calls = message.get("tool_calls") or []
    if calls:
        function = calls[0]["function"]
        spec = REGISTRY.get(function["name"])
        return ModelTurn(
            tool_call=ToolCall(
                name=function["name"],
                arguments=_arguments(function.get("arguments")),
                requires_approval=spec.requires_approval if spec else False,
            )
        )
    return ModelTurn(answer=message.get("content") or "")


@activity.defn(name=Activities.EXECUTE_TOOL)
async def execute_tool(request: ToolRequest) -> ToolResult:
    spec = REGISTRY.get(request.call.name)
    if spec is None:
        raise ApplicationError(f"Unknown tool {request.call.name}", type=Errors.UNKNOWN_TOOL, non_retryable=True)
    if not spec.allowed_groups.intersection(request.user.groups):
        raise ApplicationError(
            f"{request.user.user_id} may not use {spec.name}", type=Errors.PERMISSION_DENIED, non_retryable=True
        )
    key = request.idempotency_key
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO tool_actions (idempotency_key, tool, arguments, user_id, status) "
            "VALUES (%s, %s, %s, %s, 'STARTED') ON CONFLICT (idempotency_key) DO NOTHING",
            (key, spec.name, Jsonb(request.call.arguments), request.user.user_id),
        )
        existing = await (
            await conn.execute("SELECT status, result FROM tool_actions WHERE idempotency_key = %s FOR UPDATE", (key,))
        ).fetchone()
        if existing[0] == "COMPLETED":
            return ToolResult(idempotency_key=key, status="COMPLETED", output=existing[1])
        output = await HANDLERS[spec.name](conn, request)
        await conn.execute(
            "UPDATE tool_actions SET status = 'COMPLETED', result = %s, completed_at = now() "
            "WHERE idempotency_key = %s",
            (Jsonb(output), key),
        )
        await conn.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES (%s, %s, %s)",
            (request.user.user_id, f"tool.{spec.name}", Jsonb({"idempotency_key": key, "output": output})),
        )
    return ToolResult(idempotency_key=key, status="COMPLETED", output=output)
