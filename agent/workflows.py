from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from common.models import (
        AgentRunRequest,
        AgentRunState,
        ApprovalDecision,
        ApprovalOpenRequest,
        ChunkResult,
        GenerateRequest,
        ModelTurn,
        RetrieveRequest,
        RunStatus,
        ToolCall,
        ToolRequest,
        ToolResult,
    )
    from common.names import Activities, Queries, Signals, Workflows
    from common.queues import AGENT, TOOLS
    from common.retry import DEFAULT_RETRY

MAX_STEPS = 6
ESCALATE_AFTER = timedelta(minutes=2)


@workflow.defn(name=Workflows.AGENT_RUN)
class AgentRunWorkflow:
    def __init__(self) -> None:
        self._state = AgentRunState(status=RunStatus.RUNNING)
        self._decision: ApprovalDecision | None = None

    @workflow.run
    async def run(self, request: AgentRunRequest) -> AgentRunState:
        chunks = await workflow.execute_activity(
            Activities.RETRIEVE,
            RetrieveRequest(query=request.question, user=request.user),
            result_type=list[ChunkResult],
            task_queue=AGENT,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        self._state.citations = chunks
        tool_results: list[dict[str, Any]] = []
        for step in range(MAX_STEPS):
            turn = await workflow.execute_activity(
                Activities.GENERATE,
                GenerateRequest(question=request.question, chunks=chunks, tool_results=tool_results),
                result_type=ModelTurn,
                task_queue=AGENT,
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
            if turn.tool_call is None:
                self._state.answer = turn.answer
                self._state.status = RunStatus.COMPLETED
                return self._state
            if turn.tool_call.requires_approval and not await self._await_approval(request, turn.tool_call, step):
                self._state.status = RunStatus.DENIED
                return self._state
            self._state.status = RunStatus.RUNNING
            result = await workflow.execute_activity(
                Activities.EXECUTE_TOOL,
                ToolRequest(
                    idempotency_key=f"{workflow.info().workflow_id}:{step}",
                    call=turn.tool_call,
                    user=request.user,
                    run_id=workflow.info().workflow_id,
                ),
                result_type=ToolResult,
                task_queue=TOOLS,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_RETRY,
            )
            self._state.tool_results.append(result)
            tool_results.append({"tool": turn.tool_call.name, "status": result.status, "output": result.output})
        self._state.status = RunStatus.FAILED
        return self._state

    async def _await_approval(self, request: AgentRunRequest, call: ToolCall, step: int) -> bool:
        run_id = workflow.info().workflow_id
        self._state.approval_id = await workflow.execute_activity(
            Activities.OPEN_APPROVAL,
            ApprovalOpenRequest(approval_key=f"{run_id}:{step}", run_id=run_id, user=request.user, call=call),
            result_type=str,
            task_queue=TOOLS,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        self._state.status = RunStatus.WAITING_APPROVAL
        try:
            await workflow.wait_condition(lambda: self._decision is not None, timeout=ESCALATE_AFTER)
        except TimeoutError:
            self._state.escalated = True
            self._state.status = RunStatus.ESCALATED
            await workflow.execute_activity(
                Activities.ESCALATE_APPROVAL,
                self._state.approval_id,
                task_queue=TOOLS,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
            await workflow.wait_condition(lambda: self._decision is not None)
        approved = self._decision.approved
        self._decision = None
        return approved

    @workflow.signal(name=Signals.APPROVAL)
    def approval(self, decision: ApprovalDecision) -> None:
        self._decision = decision

    @workflow.query(name=Queries.STATE)
    def state(self) -> AgentRunState:
        return self._state
