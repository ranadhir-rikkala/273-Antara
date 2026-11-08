from datetime import timedelta

from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agent.triage import classify_by_rules
from agent.workflows import AccessLeaseWorkflow, AgentRunWorkflow, IncidentReportWorkflow
from common.models import (
    AccessLease,
    AgentRunRequest,
    AgentRunState,
    ApprovalDecision,
    ApprovalOpenRequest,
    CachedAnswer,
    CacheLookupRequest,
    CacheStoreRequest,
    ChunkResult,
    GenerateRequest,
    IncidentReport,
    IncidentResult,
    ModelTurn,
    RetrieveRequest,
    RunStatus,
    ToolCall,
    ToolRequest,
    ToolResult,
    UserContext,
)
from common.names import Activities, Queries, Signals, Workflows
from common.queues import AGENT, TOOLS

executed: list[str] = []
tool_offers: list[bool] = []
escalations: list[str] = []
expirations: list[str] = []
opened_approvals: list[str] = []
failing_tools: list[bool] = []
failing_model: list[bool] = []


incident_reports: list[str] = []
cache: dict[str, CachedAnswer] = {}
weak_retrieval: list[bool] = []
retrievals: list[str] = []
revocations: list[str] = []


@activity.defn(name=Activities.CACHE_LOOKUP)
async def fake_cache_lookup(request: CacheLookupRequest) -> CachedAnswer | None:
    return cache.get(request.question.lower())


@activity.defn(name=Activities.CACHE_STORE)
async def fake_cache_store(request: CacheStoreRequest) -> None:
    cache[request.question.lower()] = CachedAnswer(request.answer, request.citations)


@activity.defn(name=Activities.CLASSIFY)
async def fake_classify(question: str) -> str:
    return classify_by_rules(question)


@activity.defn(name=Activities.REPORT_INCIDENT)
async def fake_report_incident(report: IncidentReport) -> IncidentResult:
    incident_reports.append(report.report_id)
    return IncidentResult(
        "incident-1", "VPN is down", created=len(incident_reports) == 1, reporter_count=len(incident_reports)
    )


@activity.defn(name=Activities.RETRIEVE)
async def fake_retrieve(request: RetrieveRequest) -> list[ChunkResult]:
    retrievals.append(request.query)
    if weak_retrieval:
        return [ChunkResult("doc-2", "ver-2", "chunk-9", "Unrelated laptop policy.", 1, 0.38)]
    return [ChunkResult("doc-1", "ver-1", "chunk-1", "Prod database access needs manager approval.", 1, 0.91)]


@activity.defn(name=Activities.GENERATE)
async def fake_generate(request: GenerateRequest) -> ModelTurn:
    if failing_model:
        raise ApplicationError("model unavailable", non_retryable=True)
    tool_offers.append(request.allow_tools)
    if request.allow_tools and "access" in request.question and not request.tool_results:
        return ModelTurn(
            tool_call=ToolCall("request_access", {"system": "prod-db", "role": "read"}, requires_approval=True)
        )
    return ModelTurn(answer="Done [1]")


@activity.defn(name=Activities.EXECUTE_TOOL)
async def fake_execute_tool(request: ToolRequest) -> ToolResult:
    if failing_tools:
        raise ApplicationError("ticket system rejected the request", type="ToolError", non_retryable=True)
    executed.append(request.idempotency_key)
    granted = {"grant_id": "grant-1", "system": "prod-db", "role": "read", "status": "GRANTED", "lease_seconds": 3600}
    return ToolResult(request.idempotency_key, "COMPLETED", granted)


@activity.defn(name=Activities.REVOKE_ACCESS)
async def fake_revoke_access(grant_id: str) -> bool:
    revocations.append(grant_id)
    return True


@activity.defn(name=Activities.OPEN_APPROVAL)
async def fake_open_approval(request: ApprovalOpenRequest) -> str:
    opened_approvals.append(request.approval_key)
    return f"approval-for-{request.approval_key}"


@activity.defn(name=Activities.EXPIRE_APPROVAL)
async def fake_expire_approval(approval_id: str) -> None:
    expirations.append(approval_id)


@activity.defn(name=Activities.ESCALATE_APPROVAL)
async def fake_escalate_approval(approval_id: str) -> None:
    escalations.append(approval_id)


async def _run(env: WorkflowEnvironment, workflow_id: str, question: str, user: UserContext | None = None):
    return await env.client.start_workflow(
        Workflows.AGENT_RUN,
        AgentRunRequest(question=question, user=user or UserContext("alice", ["employees"])),
        id=workflow_id,
        task_queue=AGENT,
        result_type=AgentRunState,
    )


def _workers(env: WorkflowEnvironment) -> tuple[Worker, Worker]:
    return (
        Worker(
            env.client,
            task_queue=AGENT,
            workflows=[AgentRunWorkflow],
            activities=[fake_classify, fake_cache_lookup, fake_retrieve, fake_generate, fake_cache_store],
        ),
        Worker(
            env.client,
            task_queue=TOOLS,
            workflows=[IncidentReportWorkflow, AccessLeaseWorkflow],
            activities=[
                fake_revoke_access,
                fake_execute_tool,
                fake_open_approval,
                fake_escalate_approval,
                fake_expire_approval,
                fake_report_incident,
            ],
        ),
    )


async def test_answers_with_citations_without_tools() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            handle = await _run(env, "agent-answer", "How do I reset my password?")
            result = await handle.result()
    assert result.status == RunStatus.COMPLETED
    assert result.answer == "Done [1]"
    assert result.citations[0].chunk_id == "chunk-1"


async def test_access_request_escalates_then_runs_tool_once_after_approval() -> None:
    executed.clear()
    escalations.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            handle = await _run(env, "agent-approve", "I need access to prod-db please")
            await env.sleep(timedelta(minutes=3))
            waiting = await handle.query(Queries.STATE, result_type=AgentRunState)
            assert waiting.status == RunStatus.ESCALATED
            assert waiting.approval_id == "approval-for-agent-approve:0"
            assert escalations == ["approval-for-agent-approve:0"]
            assert executed == []
            await handle.signal(Signals.APPROVAL, ApprovalDecision(approved=True, approver_id="manager-1"))
            result = await handle.result()
    assert result.status == RunStatus.COMPLETED
    assert result.escalated is True
    assert result.answer == (
        "Your read access to prod-db was granted after manager-1 approved it. It ends automatically in 1 hour."
    )
    assert executed == ["agent-approve:0"]


async def test_granted_access_starts_a_lease_that_outlives_the_run() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            handle = await _run(env, "agent-lease", "I need access to prod-db")
            await handle.signal(Signals.APPROVAL, ApprovalDecision(approved=True, approver_id="manager-1"))
            assert (await handle.result()).status == RunStatus.COMPLETED
            lease = await env.client.get_workflow_handle("lease-grant-1").describe()
    assert lease.status == WorkflowExecutionStatus.RUNNING
    assert lease.workflow_type == Workflows.ACCESS_LEASE


async def test_a_lease_revokes_access_once_its_timer_fires() -> None:
    revocations.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            started = await env.get_current_time()
            outcome = await env.client.execute_workflow(
                Workflows.ACCESS_LEASE, AccessLease("grant-9", 3600), id="lease-grant-9", task_queue=TOOLS
            )
            elapsed = await env.get_current_time() - started
    assert outcome == "revoked"
    assert revocations == ["grant-9"]
    assert elapsed >= timedelta(hours=1)


async def test_denied_access_request_never_runs_tool() -> None:
    executed.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            handle = await _run(env, "agent-deny", "I need access to prod-db")
            await handle.signal(Signals.APPROVAL, ApprovalDecision(approved=False, approver_id="manager-1"))
            result = await handle.result()
    assert result.status == RunStatus.DENIED
    assert executed == []


async def test_incident_reports_go_to_dedup_child_workflow_not_rag() -> None:
    incident_reports.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            first = await (await _run(env, "agent-vpn-1", "The VPN is down for me")).result()
            second = await (await _run(env, "agent-vpn-2", "vpn not working since 9am")).result()
    assert first.intent == "incident"
    assert first.incident.created is True
    assert first.citations == []
    assert second.incident.created is False
    assert second.incident.reporter_count == 2
    assert incident_reports == ["agent-vpn-1", "agent-vpn-2"]


async def test_repeated_question_is_served_from_cache() -> None:
    cache.clear()
    retrievals.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            first = await (await _run(env, "agent-cache-1", "How do I reset my password?")).result()
            second = await (await _run(env, "agent-cache-2", "How do I reset my password?")).result()
    assert first.cached is False
    assert second.cached is True
    assert second.answer == first.answer
    assert second.citations[0].chunk_id == "chunk-1"
    assert retrievals == ["How do I reset my password?"]


async def test_plain_questions_are_never_offered_tools() -> None:
    tool_offers.clear()
    executed.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            result = await (await _run(env, "agent-question-access", "How do contractors get VPN access?")).result()
    assert result.intent == "question"
    assert result.status == RunStatus.COMPLETED
    assert tool_offers == [False]
    assert executed == []


async def test_weakly_supported_answers_are_not_cached() -> None:
    cache.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            weak_retrieval.append(True)
            try:
                await (await _run(env, "agent-weak", "What is the L4 salary band?")).result()
            finally:
                weak_retrieval.clear()
    assert cache == {}


async def test_a_failing_tool_ends_the_run_as_failed_with_a_reason() -> None:
    failing_tools.append(True)
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            agent_worker, tools_worker = _workers(env)
            async with agent_worker, tools_worker:
                handle = await _run(env, "agent-tool-fails", "I need access to prod-db please")
                await env.sleep(timedelta(seconds=5))
                approval = (await handle.query(Queries.STATE, result_type=AgentRunState)).approval_id
                assert approval is not None
                await handle.signal(Signals.APPROVAL, ApprovalDecision(approved=True, approver_id="manager-1"))
                result = await handle.result()
    finally:
        failing_tools.clear()
    assert result.status == RunStatus.FAILED
    assert "couldn't complete" in result.answer


async def test_a_model_outage_ends_the_run_as_failed_instead_of_hanging() -> None:
    failing_model.append(True)
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            agent_worker, tools_worker = _workers(env)
            async with agent_worker, tools_worker:
                result = await (await _run(env, "agent-model-down", "How do I reset my password?")).result()
    finally:
        failing_model.clear()
    assert result.status == RunStatus.FAILED
    assert "temporarily unavailable" in result.answer


async def test_forbidden_actions_are_refused_before_any_approval_is_opened() -> None:
    opened_approvals.clear()
    executed.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            contractor = UserContext("bob", ["contractors"])
            result = await (await _run(env, "agent-contractor", "I need access to prod-db", contractor)).result()
    assert result.status == RunStatus.DENIED
    assert "not allowed" in result.answer
    assert opened_approvals == []
    assert executed == []


async def test_an_undecided_request_expires_after_a_day_without_running_the_tool() -> None:
    executed.clear()
    expirations.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            result = await (await _run(env, "agent-expire", "I need access to prod-db please")).result()
    assert result.status == RunStatus.EXPIRED
    assert "expired" in result.answer
    assert expirations == ["approval-for-agent-expire:0"]
    assert executed == []


async def test_follow_ups_search_with_context_and_skip_the_cache() -> None:
    from common.models import ConversationTurn

    retrievals.clear()
    cache.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        agent_worker, tools_worker = _workers(env)
        async with agent_worker, tools_worker:
            history = [ConversationTurn("Who approves read access to production databases?", "The manager [1]")]
            handle = await env.client.start_workflow(
                Workflows.AGENT_RUN,
                AgentRunRequest("and what about write access?", UserContext("alice", ["employees"]), history=history),
                id="agent-follow-up",
                task_queue=AGENT,
                result_type=AgentRunState,
            )
            result = await handle.result()
    assert retrievals == ["Who approves read access to production databases? and what about write access?"]
    assert result.status == RunStatus.COMPLETED
    assert cache == {}
