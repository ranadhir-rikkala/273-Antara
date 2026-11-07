import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from api.runs import RunNotFoundError, RunUnavailableError, read_run_state
from common.models import AgentRunState


class Description:
    def __init__(self, status: WorkflowExecutionStatus) -> None:
        self.status = status


class FakeHandle:
    def __init__(self, query=None, describe=None, result=None) -> None:
        self._query, self._describe, self._result = query, describe, result

    async def query(self, name: str, result_type=None):
        if isinstance(self._query, Exception):
            raise self._query
        return self._query

    async def describe(self):
        if isinstance(self._describe, Exception):
            raise self._describe
        return self._describe

    async def result(self, result_type=None):
        return self._result


def rpc(code: RPCStatusCode) -> RPCError:
    return RPCError("boom", code, b"")


async def test_live_runs_are_answered_by_their_query() -> None:
    state = AgentRunState(status="RUNNING")
    assert await read_run_state(FakeHandle(query=state)) is state


async def test_unknown_runs_are_not_found() -> None:
    with pytest.raises(RunNotFoundError):
        await read_run_state(FakeHandle(query=rpc(RPCStatusCode.NOT_FOUND)))
    with pytest.raises(RunNotFoundError):
        await read_run_state(
            FakeHandle(query=rpc(RPCStatusCode.DEADLINE_EXCEEDED), describe=rpc(RPCStatusCode.NOT_FOUND))
        )


async def test_finished_runs_are_read_from_history_when_no_worker_answers() -> None:
    final = AgentRunState(status="COMPLETED", answer="done")
    handle = FakeHandle(
        query=rpc(RPCStatusCode.DEADLINE_EXCEEDED),
        describe=Description(WorkflowExecutionStatus.COMPLETED),
        result=final,
    )
    assert await read_run_state(handle) is final


async def test_running_workflows_without_a_worker_are_temporarily_unavailable() -> None:
    handle = FakeHandle(
        query=rpc(RPCStatusCode.DEADLINE_EXCEEDED), describe=Description(WorkflowExecutionStatus.RUNNING)
    )
    with pytest.raises(RunUnavailableError):
        await read_run_state(handle)
