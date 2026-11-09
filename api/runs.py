from datetime import timedelta
from typing import Any, Protocol

from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from common.models import AgentRunState
from common.names import Queries

QUERY_TIMEOUT = timedelta(seconds=4)


class RunNotFoundError(Exception):
    pass


class RunUnavailableError(Exception):
    pass


class RunHandle(Protocol):
    async def query(self, name: str, result_type: Any = None, rpc_timeout: timedelta | None = None) -> Any: ...

    async def describe(self) -> Any: ...

    async def result(self, result_type: Any = None) -> Any: ...


async def read_run_state(handle: RunHandle) -> AgentRunState:
    try:
        return await handle.query(Queries.STATE, result_type=AgentRunState, rpc_timeout=QUERY_TIMEOUT)
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            raise RunNotFoundError("Run not found") from error
    try:
        description = await handle.describe()
    except RPCError as error:
        raise RunNotFoundError("Run not found") from error
    if description.status == WorkflowExecutionStatus.COMPLETED:
        return await handle.result(result_type=AgentRunState)
    raise RunUnavailableError("Run status is temporarily unavailable")
