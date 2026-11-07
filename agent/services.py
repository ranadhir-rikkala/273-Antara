import json
from typing import Any

from agent.ports import (
    AnswerCache,
    ApprovalStore,
    AuditLog,
    ChatModel,
    Embedder,
    IncidentUnitOfWork,
    Retriever,
    ToolUnitOfWork,
)
from agent.tools import HANDLERS, REGISTRY, clarifying_question, prepare_arguments, schemas
from agent.triage import CLASSIFY_PROMPT, affected_system, classify_by_rules, needs_model, parse_intent
from common.models import (
    ApprovalOpenRequest,
    CachedAnswer,
    CacheLookupRequest,
    CacheStoreRequest,
    ChunkResult,
    GenerateRequest,
    IncidentReport,
    IncidentResult,
    Intent,
    ModelTurn,
    RetrieveRequest,
    ToolCall,
    ToolRequest,
    ToolResult,
)

SYSTEM_PROMPT = (
    "You are Antara, an internal IT and security helpdesk assistant. "
    "Answer only from the numbered context passages and cite them like [1]. "
    "If the context does not contain the answer, say you could not find it in company documents. "
    "Use a tool only when the user asks you to take an action."
)

INCIDENT_TITLE_LENGTH = 120
NOT_FOUND_ANSWER = "I couldn't find that in company documents. Try rephrasing, or ask the IT helpdesk."


class UnknownToolError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


async def retrieve(request: RetrieveRequest, retriever: Retriever, audit: AuditLog) -> list[ChunkResult]:
    chunks = await retriever.search(request.query, request.user, request.top_k)
    await audit.record(
        request.user.user_id, "agent.retrieve", {"query": request.query, "chunk_ids": [c.chunk_id for c in chunks]}
    )
    return chunks


async def classify(question: str, model: ChatModel) -> Intent:
    by_rules = classify_by_rules(question)
    if not needs_model(question, by_rules):
        return by_rules
    try:
        reply = await model.generate(
            [{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": question}]
        )
    except Exception:
        return by_rules
    return parse_intent(reply) or by_rules


def build_messages(request: GenerateRequest) -> list[dict[str, Any]]:
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


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return json.loads(raw or "{}")


def is_supported(chunks: list[ChunkResult], min_score: float) -> bool:
    return bool(chunks) and chunks[0].score >= min_score


async def generate(request: GenerateRequest, model: ChatModel, min_score: float = 0.0) -> ModelTurn:
    if not request.allow_tools and not is_supported(request.chunks, min_score):
        return ModelTurn(answer=NOT_FOUND_ANSWER)
    messages = build_messages(request)
    if not request.allow_tools:
        return ModelTurn(answer=await model.generate(messages))
    message = await model.generate_with_tools(messages, schemas(request.tool_names))
    calls = message.get("tool_calls") or []
    if not calls:
        return ModelTurn(answer=message.get("content") or "")
    function = calls[0]["function"]
    spec = REGISTRY.get(function["name"])
    arguments = parse_arguments(function.get("arguments"))
    if spec is None:
        return ModelTurn(tool_call=ToolCall(name=function["name"], arguments=arguments))
    arguments, missing = prepare_arguments(spec, arguments, request.question)
    if missing:
        return ModelTurn(answer=clarifying_question(spec, missing), needs_input=True)
    return ModelTurn(tool_call=ToolCall(name=spec.name, arguments=arguments, requires_approval=spec.requires_approval))


async def execute_tool(request: ToolRequest, unit_of_work: ToolUnitOfWork) -> ToolResult:
    spec = REGISTRY.get(request.call.name)
    if spec is None:
        raise UnknownToolError(f"Unknown tool {request.call.name}")
    if not spec.allowed_groups.intersection(request.user.groups):
        raise PermissionDeniedError(f"{request.user.user_id} may not use {spec.name}")
    key = request.idempotency_key
    async with unit_of_work.begin() as workspace:
        previous = await workspace.claim(key, spec.name, request.call.arguments, request.user.user_id)
        if previous is not None:
            return ToolResult(idempotency_key=key, status="COMPLETED", output=previous)
        output = await HANDLERS[spec.name](workspace, request)
        await workspace.complete(key, output)
        await workspace.audit.record(
            request.user.user_id, f"tool.{spec.name}", {"idempotency_key": key, "output": output}
        )
    return ToolResult(idempotency_key=key, status="COMPLETED", output=output)


async def open_approval(request: ApprovalOpenRequest, store: ApprovalStore, audit: AuditLog) -> str:
    approval_id = await store.open(request.approval_key, request.run_id, request.user.user_id, request.call)
    await audit.record(
        request.user.user_id,
        "approval.opened",
        {"approval_id": approval_id, "run_id": request.run_id, "tool": request.call.name},
    )
    return approval_id


async def escalate_approval(approval_id: str, store: ApprovalStore, audit: AuditLog) -> None:
    await store.escalate(approval_id)
    await audit.record("system", "approval.escalated", {"approval_id": approval_id})


async def expire_approval(approval_id: str, store: ApprovalStore, audit: AuditLog) -> None:
    await store.expire(approval_id)
    await audit.record("system", "approval.expired", {"approval_id": approval_id})


async def report_incident(
    report: IncidentReport,
    embedder: Embedder,
    unit_of_work: IncidentUnitOfWork,
    window_minutes: int,
    threshold: float,
) -> IncidentResult:
    [embedding] = await embedder.embed([report.text])
    async with unit_of_work.begin_exclusive() as workspace:
        existing = await workspace.find_report(report.report_id)
        if existing is not None:
            return existing
        system = affected_system(report.text)
        if system is not None:
            match = await workspace.find_open_for_system(system, window_minutes)
        else:
            match = await workspace.find_similar_open(embedding, window_minutes, threshold)
        created = match is None
        if created:
            title = report.text.strip()[:INCIDENT_TITLE_LENGTH]
            incident_id = await workspace.open_incident(title, embedding, system)
        else:
            incident_id, title = match
        count = await workspace.add_report(report.report_id, incident_id, report.reporter.user_id, report.text)
        await workspace.record(
            report.reporter.user_id,
            "incident.opened" if created else "incident.linked",
            {"incident_id": incident_id, "report_id": report.report_id},
        )
    return IncidentResult(incident_id=incident_id, title=title, created=created, reporter_count=count, system=system)


def groups_key(groups: list[str]) -> str:
    return ",".join(sorted(set(groups)))


async def lookup_cached_answer(
    request: CacheLookupRequest, embedder: Embedder, cache: AnswerCache, ttl_hours: int, threshold: float
) -> CachedAnswer | None:
    [embedding] = await embedder.embed([request.question])
    return await cache.find(groups_key(request.user.groups), embedding, ttl_hours, threshold)


async def store_cached_answer(request: CacheStoreRequest, embedder: Embedder, cache: AnswerCache) -> None:
    [embedding] = await embedder.embed([request.question])
    version_ids = sorted({citation.version_id for citation in request.citations})
    await cache.save(
        request.question, embedding, groups_key(request.user.groups), request.answer, request.citations, version_ids
    )
